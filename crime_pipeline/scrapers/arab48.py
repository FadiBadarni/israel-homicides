"""
Scraper for Arab48.com — Arabic, server-rendered.

Uses httpx + BeautifulSoup. The search endpoint is:
    https://www.arab48.com/بحث?searchText=<query>
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ArticleResult, BaseScraper, DiscoveredUrl

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.arab48.com"
_SEARCH_URL = f"{_BASE_URL}/بحث"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,he-IL;q=0.8,en-US;q=0.6,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_ARTICLE_LINK_MARKERS = tuple(f"/{year}/" for year in range(2020, 2028))

# First-path-segment blocklist. Articles whose URL begins with one of these
# editorial categories are guaranteed not to be homicide news, so we drop
# them pre-fetch — saves bandwidth + LLM triage cost.
#
# Conservative by design: we only list categories where the editorial intent
# is unambiguous. Borderline cases stay in:
#   /محليات/* (Local) — KEEP, where homicides live
#   /الأخبار/* (Breaking news) — KEEP
#   /إسرائيليات/* (Israeli affairs) — KEEP
#   /أخبار-عربية-ودولية/* (Arab & World) — KEEP, may carry regional crimes
#   /فيديو/* (Video) — KEEP, crime reports may live here
#   /محليات/دراسات-وتقارير/* (Studies/Reports) — KEEP, may carry analysis
_NON_HOMICIDE_PATH_SEGMENTS = frozenset({
    "رياضة",          # Sports
    "ثقافة-وفنون",    # Culture & Arts
    "علوم-وتكنولوجيا",  # Science & Tech
    "مقالات-وآراء",   # Opinion / Op-eds
})


def _is_non_homicide_path(parsed_path: str) -> bool:
    """True if the URL's first non-empty path segment is in the blocklist."""
    parts = [p for p in parsed_path.split("/") if p]
    return bool(parts) and parts[0] in _NON_HOMICIDE_PATH_SEGMENTS

_BODY_SELECTORS = [
    ".article-content p",
    ".article-content",
    "article p",
]


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _parse_arab48_date(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_soup(soup: BeautifulSoup) -> Optional[datetime]:
    for key in ("article:published_time", "article:modified_time"):
        meta = soup.find("meta", {"property": key})
        if meta and meta.get("content"):
            parsed = _parse_arab48_date(meta["content"])
            if parsed:
                return parsed

    time_el = soup.find("time")
    if time_el:
        parsed = _parse_arab48_date(time_el.get("datetime", ""))
        if parsed:
            return parsed
        parsed = _parse_arab48_date(time_el.get_text(strip=True))
        if parsed:
            return parsed

    return None


class Arab48Scraper(BaseScraper):
    source_name = "arab48"
    language = "ar"
    base_domain = "www.arab48.com"

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("Arab48: sleeping %.2f s", delay)
        await asyncio.sleep(delay)

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        @retry(
            retry=retry_if_exception_type(_RetryableHTTPError),
            wait=wait_exponential(multiplier=1, min=4, max=60),
            stop=stop_after_attempt(4),
            reraise=True,
        )
        async def _inner() -> httpx.Response:
            await self._sleep()
            resp = await client.get(url)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _RetryableHTTPError(f"HTTP {resp.status_code} from {url}")
            return resp

        return await _inner()

    async def discover_from_category(
        self,
        category_path: str,
        date_from: str,
        date_to: str,
        title_keywords: list[str],
        max_results: int = 400,
        max_pages: int = 200,
        listing_delay: float = 0.2,
    ) -> list[DiscoveredUrl]:
        """Discover articles by walking an Arab48 category listing.

        The category index (e.g. ``/محليات``) paginates linearly by date
        — page 1 is newest, page 1000 reaches mid-2018. Each item carries
        a ``.category-inside-time`` span in ``DD/MM/YYYY`` form. We
        filter by date window AND require ``title_keywords`` to appear
        in the headline text BEFORE fetching the article, which cuts
        the LLM cost of triage drastically — most ``/محليات`` items
        are non-homicide local news (politics, weddings, ceremonies)
        that we'd otherwise pay triage on.

        ``category_path`` must start with ``/`` (e.g. ``/محليات``).

        Stops when either:
          * ``max_results`` candidates have been collected, or
          * ``max_pages`` pages have been walked, or
          * a page's items are all older than ``date_from`` (we've
            walked past the window — listing is newest-first).
        """
        from urllib.parse import quote

        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
        discovered: list[DiscoveredUrl] = []
        seen: set[str] = set()

        # Build the category URL. Arabic paths need percent-encoding.
        if not category_path.startswith("/"):
            category_path = "/" + category_path
        category_url = f"{_BASE_URL}{quote(category_path)}"

        async with _make_client() as client:
            for page in range(1, max_pages + 1):
                if len(discovered) >= max_results:
                    break

                page_url = (
                    category_url if page == 1
                    else f"{category_url}?page={page}"
                )
                # Static listing pages don't need the polite article-fetch
                # delay (1-3s with jitter). Use a flat short pause so a
                # year-walk runs in seconds, not minutes.
                if page > 1 and listing_delay > 0:
                    await asyncio.sleep(listing_delay)
                try:
                    # Bypass self._get (which applies the article delay)
                    # — call the client directly with retry.
                    @retry(
                        retry=retry_if_exception_type(_RetryableHTTPError),
                        wait=wait_exponential(multiplier=1, min=2, max=30),
                        stop=stop_after_attempt(3),
                        reraise=True,
                    )
                    async def _fetch_listing() -> httpx.Response:
                        resp = await client.get(page_url)
                        if resp.status_code == 429 or resp.status_code >= 500:
                            raise _RetryableHTTPError(
                                f"HTTP {resp.status_code} from {page_url}"
                            )
                        return resp
                    resp = await _fetch_listing()
                    resp.raise_for_status()
                except (_RetryableHTTPError, httpx.HTTPError) as exc:
                    logger.error(
                        "Arab48 category-discover page %d: HTTP error — %s",
                        page, exc,
                    )
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                spans = soup.select(".category-inside-time")
                if not spans:
                    logger.info(
                        "Arab48 category-discover: page %d has no date "
                        "spans — end of listing", page,
                    )
                    break

                kept_this_page = 0
                older_than_window_count = 0

                for span in spans:
                    raw_date = span.get_text(strip=True)
                    m = re.match(r"(\d{2})/(\d{2})/(20\d{2})", raw_date)
                    if not m:
                        continue
                    pubdate = datetime(
                        int(m.group(3)), int(m.group(2)), int(m.group(1)),
                        tzinfo=timezone.utc,
                    )

                    # Find the containing card by walking up to a parent
                    # that has an /<YYYY>/<MM>/<DD>/ article link.
                    card = span
                    href = ""
                    title = ""
                    for _ in range(8):  # bounded upward walk
                        card = card.parent
                        if card is None:
                            break
                        link = card.find(
                            "a", href=re.compile(r"/20\d{2}/\d{2}/\d{2}/")
                        )
                        if link:
                            href = link.get("href", "")
                            title = link.get_text(strip=True)
                            break
                    if not href or not title:
                        continue

                    full_url = urljoin(_BASE_URL, href)
                    if full_url in seen:
                        continue
                    seen.add(full_url)

                    # Drop non-homicide editorial paths early (sports,
                    # opinion, etc.) — same blocklist the keyword
                    # discover uses for the search-results path.
                    parsed = urlparse(full_url)
                    if _is_non_homicide_path(parsed.path):
                        continue

                    if pubdate < from_dt:
                        older_than_window_count += 1
                        continue
                    if pubdate > to_dt:
                        # Skip but don't terminate — listing is newest-
                        # first, so we may still have in-window items
                        # below this on the same page.
                        continue

                    # Title pre-filter: only fetch articles whose visible
                    # title contains a homicide-relevant keyword. The
                    # arab48 title duplicates the date suffix
                    # (``DD/MM/YYYY``); strip that before keyword check.
                    clean_title = re.sub(r"\d{2}/\d{2}/20\d{2}$", "", title).strip()
                    if not any(kw in clean_title for kw in title_keywords):
                        continue

                    discovered.append(DiscoveredUrl(
                        url=full_url,
                        source=self.source_name,
                        language=self.language,
                        title=clean_title,
                        published_at=pubdate,
                        discovered_at=datetime.now(timezone.utc),
                    ))
                    kept_this_page += 1
                    if len(discovered) >= max_results:
                        break

                logger.info(
                    "Arab48 category-discover: page %d kept=%d older=%d "
                    "(cumulative=%d)",
                    page, kept_this_page, older_than_window_count,
                    len(discovered),
                )

                # If every dated item on this page was older than the
                # window, we've walked past it — no point in continuing.
                if older_than_window_count == len(spans):
                    logger.info(
                        "Arab48 category-discover: page %d is entirely "
                        "older than window — stopping", page,
                    )
                    break

        logger.info(
            "Arab48 category-discover: found %d candidate URLs for "
            "category=%s in [%s, %s] (title_kw=%s)",
            len(discovered), category_path, date_from, date_to,
            ",".join(title_keywords)[:80],
        )
        return discovered

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
        max_pages: int = 5,
    ) -> list[DiscoveredUrl]:
        """Discover articles, walking up to ``max_pages`` of search results.

        Arab48's search exposes paginated results via ``?page=N`` (1-indexed,
        20 results per page). The previous implementation only fetched page 1,
        capping recall at ~20 articles per query regardless of ``max_results``.

        Stop conditions (any one ends the loop):
          1. ``len(discovered) >= max_results``
          2. ``page > max_pages``
          3. The current page added zero new URLs (e.g. out-of-range page
             returns the same generic content as a different page)
        """
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
        discovered: list[DiscoveredUrl] = []
        seen: set[str] = set()

        async with _make_client() as client:
            for page in range(1, max_pages + 1):
                if len(discovered) >= max_results:
                    break

                base = f"{_SEARCH_URL}?searchText={quote_plus(query)}"
                page_url = base if page == 1 else f"{base}&page={page}"

                try:
                    resp = await self._get(client, page_url)
                    resp.raise_for_status()
                except (_RetryableHTTPError, httpx.HTTPError) as exc:
                    logger.error("Arab48 discover page %d: HTTP error — %s", page, exc)
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                page_new_unique = 0    # truly new URLs (regardless of date)
                page_kept = 0          # URLs kept after date filter
                page_pre_window = 0    # URLs newer than date_to (we haven't reached window yet)
                page_post_window = 0   # URLs older than date_from (we've gone past)

                for a_tag in soup.find_all("a", href=True):
                    if len(discovered) >= max_results:
                        break

                    href = a_tag.get("href", "")
                    if not href or not any(marker in href for marker in _ARTICLE_LINK_MARKERS):
                        continue

                    full_url = urljoin(_BASE_URL, href)
                    parsed = urlparse(full_url)
                    if parsed.netloc and parsed.netloc != self.base_domain:
                        continue
                    if full_url in seen:
                        continue
                    seen.add(full_url)
                    page_new_unique += 1

                    # Path-segment blocklist (B-stage cheap pre-fetch reject).
                    if _is_non_homicide_path(parsed.path):
                        continue

                    title_text = a_tag.get_text(" ", strip=True) or None
                    pub_date: Optional[datetime] = None
                    parts = [p for p in parsed.path.split("/") if p]
                    for i, part in enumerate(parts):
                        if (
                            part.isdigit()
                            and len(part) == 4
                            and i + 2 < len(parts)
                            and parts[i + 1].isdigit()
                            and parts[i + 2].isdigit()
                        ):
                            pub_date = _parse_arab48_date(
                                f"{part}-{parts[i + 1]}-{parts[i + 2]} 00:00:00"
                            )
                            break

                    if pub_date:
                        if pub_date > to_dt:
                            page_pre_window += 1
                            continue
                        if pub_date < from_dt:
                            page_post_window += 1
                            continue

                    discovered.append(
                        DiscoveredUrl(
                            url=full_url,
                            source=self.source_name,
                            language=self.language,
                            title=title_text,
                            published_at=pub_date,
                            discovered_at=datetime.now(tz=timezone.utc),
                        )
                    )
                    page_kept += 1

                logger.info(
                    "Arab48 discover: page %d added %d kept "
                    "(new_unique=%d, pre_window=%d, post_window=%d, cumulative=%d) for query=%r",
                    page, page_kept, page_new_unique,
                    page_pre_window, page_post_window, len(discovered), query,
                )

                # New stop logic, ordered:
                # 1) Truly exhausted — page returned only URLs we'd already
                #    seen. Real "no more results".
                if page_new_unique == 0:
                    break
                # 2) Walked past the target window (page contains URLs older
                #    than date_from). Results are date-DESC on Arab48, so any
                #    older URLs mean further pages will also be older.
                if page_post_window > 0 and page_kept == 0 and page_pre_window == 0:
                    break
                # 3) Otherwise keep paginating — page_kept==0 with all URLs
                #    in page_pre_window means we haven't reached the target
                #    date range yet (Arab48 search is date-desc). The old
                #    logic stopped here, missing historical queries entirely.

        logger.info(
            "Arab48 discover: found %d URLs across %d page(s) for query=%r",
            len(discovered), page, query,
        )
        return discovered

    async def fetch(self, url: str) -> ArticleResult:
        if not self.can_fetch(url):
            return ArticleResult(
                url=url,
                final_url=url,
                source=self.source_name,
                language=self.language,
                title=None,
                published_at=None,
                raw_html="",
                article_text="",
                content_type="non_article",
                fetch_status="blocked",
                error_message="Blocked by robots.txt",
            )

        async with _make_client() as client:
            try:
                resp = await self._get(client, url)
                resp.raise_for_status()
            except _RetryableHTTPError as exc:
                return ArticleResult(
                    url=url,
                    final_url=url,
                    source=self.source_name,
                    language=self.language,
                    title=None,
                    published_at=None,
                    raw_html="",
                    article_text="",
                    content_type="non_article",
                    fetch_status="fetch_failed",
                    error_message=str(exc),
                )
            except httpx.TimeoutException as exc:
                return ArticleResult(
                    url=url,
                    final_url=url,
                    source=self.source_name,
                    language=self.language,
                    title=None,
                    published_at=None,
                    raw_html="",
                    article_text="",
                    content_type="non_article",
                    fetch_status="timeout",
                    error_message=str(exc),
                )
            except httpx.HTTPError as exc:
                return ArticleResult(
                    url=url,
                    final_url=url,
                    source=self.source_name,
                    language=self.language,
                    title=None,
                    published_at=None,
                    raw_html="",
                    article_text="",
                    content_type="non_article",
                    fetch_status="fetch_failed",
                    error_message=str(exc),
                )

            raw_html = resp.text
            final_url = str(resp.url)
            soup = BeautifulSoup(raw_html, "lxml")

            title = None
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(" ", strip=True)
            if not title and soup.title:
                title = soup.title.get_text(" ", strip=True)

            body_parts: list[str] = []
            for sel in _BODY_SELECTORS:
                elements = soup.select(sel)
                if elements:
                    for el in elements:
                        text = el.get_text(" ", strip=True)
                        if text:
                            body_parts.append(text)
                    if body_parts:
                        break

            article_text = "\n\n".join(dict.fromkeys(body_parts))
            published_at = _extract_date_from_soup(soup)
            content_type = self._classify_content(article_text, self.language)

            logger.info(
                "Arab48 fetch: %s | title=%r | words=%d | type=%s",
                final_url,
                title,
                len(article_text.split()),
                content_type,
            )

            return ArticleResult(
                url=url,
                final_url=final_url,
                source=self.source_name,
                language=self.language,
                title=title,
                published_at=published_at,
                raw_html=raw_html,
                article_text=article_text,
                content_type=content_type,
                fetch_status="success",
                error_message=None,
            )
