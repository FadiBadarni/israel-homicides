"""
Scraper for Arab48.com — Arabic, server-rendered.

Uses httpx + BeautifulSoup. The search endpoint is:
    https://www.arab48.com/بحث?searchText=<query>
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

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
        search_url = f"{_SEARCH_URL}?searchText={quote_plus(query)}"
        discovered: list[DiscoveredUrl] = []

        async with _make_client() as client:
            try:
                resp = await self._get(client, search_url)
                resp.raise_for_status()
            except (_RetryableHTTPError, httpx.HTTPError) as exc:
                logger.error("Arab48 discover: HTTP error — %s", exc)
                return discovered

            soup = BeautifulSoup(resp.text, "lxml")
            seen: set[str] = set()

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

                # Path-segment blocklist (B-stage cheap pre-fetch reject).
                # Drops sports/culture/tech/opinion articles before we spend
                # bandwidth fetching them or LLM tokens triaging them.
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

                if pub_date and not (from_dt <= pub_date <= to_dt):
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

        logger.info("Arab48 discover: found %d URLs for query=%r", len(discovered), query)
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
