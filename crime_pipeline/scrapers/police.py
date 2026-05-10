"""
Scraper for police.gov.il press releases — Hebrew, server-rendered.

Uses httpx + BeautifulSoup only (no JS rendering required).
Rate-limited with random jitter; tenacity retry on 429 / 5xx.
"""

from __future__ import annotations

import asyncio
import logging
import random
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

_POLICE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_BASE_URL = "https://www.police.gov.il"
_SEARCH_URL = "https://www.police.gov.il/pressreleases"

# Selectors for the press-release listing page
_LISTING_LINK_SELECTORS = [
    "div.press-release-item a",
    "ul.press-list li a",
    "article.press-item a",
    "div.views-row a",
    "td.views-field-title a",
    "h3.node-title a",
    "h2.node-title a",
    "span.field-content a",
    "div.field-content a",
]

# Selectors tried in order for the article title
_TITLE_SELECTORS = [
    "h1.page-header",
    "h1.node-title",
    "div.page-header h1",
    "h1",
]

# Selectors tried in order for article body
_BODY_SELECTORS = [
    "div.content-body",
    "div.field-items",
    "div.field-item",
    "div.node-content",
    "div.view-content",
    "article .content",
    "div.body",
]


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry (429, 5xx)."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_POLICE_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _parse_police_date(raw: str) -> Optional[datetime]:
    """Try several common date formats used by police.gov.il."""
    raw = raw.strip()
    formats = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_soup(soup: BeautifulSoup) -> Optional[datetime]:
    """Pull publication date from common elements in a police.gov.il page."""
    # <time> element
    time_el = soup.find("time")
    if time_el:
        dt_attr = time_el.get("datetime", "")
        if dt_attr:
            parsed = _parse_police_date(dt_attr)
            if parsed:
                return parsed
        parsed = _parse_police_date(time_el.get_text(strip=True))
        if parsed:
            return parsed

    # Meta tags
    for attr_name, attr_value in [
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "pubdate"),
        ("name", "DC.date"),
    ]:
        meta = soup.find("meta", {attr_name: attr_value})
        if meta and meta.get("content"):
            parsed = _parse_police_date(meta["content"])
            if parsed:
                return parsed

    # Class-based date spans/divs
    for cls_fragment in ("date", "created", "submitted", "timestamp"):
        el = soup.find(class_=lambda c, f=cls_fragment: c and f in c.lower())
        if el:
            parsed = _parse_police_date(el.get_text(strip=True))
            if parsed:
                return parsed

    return None


class PoliceScraper(BaseScraper):
    source_name = "police"
    language = "he"
    base_domain = "www.police.gov.il"

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("Police: sleeping %.2f s", delay)
        await asyncio.sleep(delay)

    async def _get(
        self, client: httpx.AsyncClient, url: str
    ) -> httpx.Response:
        """GET with tenacity retry on 429 / 5xx."""

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
                raise _RetryableHTTPError(
                    f"HTTP {resp.status_code} from {url}"
                )
            return resp

        return await _inner()

    # ------------------------------------------------------------------ #
    #  discover                                                            #
    # ------------------------------------------------------------------ #

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        """
        Search police.gov.il press-release listing for *query*.
        Returns up to *max_results* DiscoveredUrl items.

        date_from / date_to: "YYYY-MM-DD" strings for date filtering.
        """
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)

        search_url = f"{_SEARCH_URL}?q={quote_plus(query)}"
        discovered: list[DiscoveredUrl] = []

        async with _make_client() as client:
            try:
                resp = await self._get(client, search_url)
                resp.raise_for_status()
            except _RetryableHTTPError as exc:
                logger.error("Police discover: retries exhausted — %s", exc)
                return discovered
            except httpx.HTTPError as exc:
                logger.error("Police discover: HTTP error — %s", exc)
                return discovered

            soup = BeautifulSoup(resp.text, "lxml")

            # Try structured selectors first
            links: list = []
            for sel in _LISTING_LINK_SELECTORS:
                links = soup.select(sel)
                if links:
                    logger.debug("Police discover: matched selector %r (%d links)", sel, len(links))
                    break

            # Generic fallback: any internal link that looks like a press release
            if not links:
                links = [
                    a
                    for a in soup.find_all("a", href=True)
                    if any(
                        kw in a["href"]
                        for kw in ("/press", "/node/", "/article", "/content/")
                    )
                ]

            seen: set[str] = set()
            for a_tag in links:
                if len(discovered) >= max_results:
                    break

                href = a_tag.get("href", "")
                if not href:
                    continue
                full_url = urljoin(_BASE_URL, href)
                parsed = urlparse(full_url)
                if parsed.netloc and self.base_domain not in parsed.netloc:
                    continue
                if full_url in seen:
                    continue
                seen.add(full_url)

                if not self.can_fetch(full_url):
                    logger.debug("Police: robots.txt blocks %s", full_url)
                    continue

                # Try to find a date in the surrounding container
                container = a_tag.find_parent(
                    ["article", "div", "li", "tr", "section"]
                )
                pub_date: Optional[datetime] = None
                if container:
                    date_el = container.find(
                        class_=lambda c: c and any(
                            f in c.lower() for f in ("date", "created", "timestamp")
                        )
                    )
                    if date_el:
                        pub_date = _parse_police_date(date_el.get_text(strip=True))

                # Apply date filter when a date is available
                if pub_date and not (from_dt <= pub_date <= to_dt):
                    continue

                title_text = a_tag.get_text(strip=True) or None

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

        logger.info(
            "Police discover: found %d URLs for query=%r", len(discovered), query
        )
        return discovered

    # ------------------------------------------------------------------ #
    #  fetch                                                               #
    # ------------------------------------------------------------------ #

    async def fetch(self, url: str) -> ArticleResult:
        """Fetch a single police.gov.il press release and extract clean text."""
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

            # --- Title ---
            title: Optional[str] = None
            for sel in _TITLE_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    break

            # --- Body text ---
            body_parts: list[str] = []
            for sel in _BODY_SELECTORS:
                elements = soup.select(sel)
                if elements:
                    for el in elements:
                        # Remove nested nav / breadcrumb / sidebar noise
                        for noise in el.select("nav, aside, .breadcrumb, script, style"):
                            noise.decompose()
                        text = el.get_text(separator="\n", strip=True)
                        if text:
                            body_parts.append(text)
                    if body_parts:
                        break

            # Fallback: grab all paragraphs in <main> or <article>
            if not body_parts:
                main = soup.find(["main", "article"])
                if main:
                    for p in main.find_all("p"):
                        text = p.get_text(separator=" ", strip=True)
                        if text:
                            body_parts.append(text)

            article_text = "\n\n".join(body_parts)

            # --- Publication date ---
            published_at = _extract_date_from_soup(soup)

            # --- Content classification ---
            content_type = self._classify_content(article_text, self.language)

            logger.info(
                "Police fetch: %s | title=%r | words=%d | type=%s",
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
