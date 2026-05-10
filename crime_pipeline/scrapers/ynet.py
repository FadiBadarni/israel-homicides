"""
Scraper for Ynet.co.il — Hebrew, server-rendered.

Uses httpx + BeautifulSoup. No JavaScript rendering required.
Rate-limited to 1 req / (request_delay + random jitter 0-2 s).
Tenacity exponential back-off on HTTP 429 / 5xx errors.
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

_YNET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_SEARCH_URL = "https://www.ynet.co.il/category/3340"
_SEARCH_PARAMS = (
    "cx=partner-pub-4207657971126930:3067011121"
    "&cof=GIMP:009900;T:000000;ALC:FF9900;GFNT:B0B0B0;LC:0000FF;"
    "BRC:FFFFFF;BGC:FFFFFF;VLC:666666;GALT:36A200;LBGC:FF0000;"
    "DIV:FFFFEE;FORID:9"
    "&as_qdr=all"
    "&hq=more:recent4"
    "&ynet_search_type=ynet"
)
_BASE_URL = "https://www.ynet.co.il"

# Selectors tried in order — first match wins
_TITLE_SELECTORS = [
    "h1.mainTitle",
    "h1.art-title",
    "h1[data-testid='article-title']",
    "h1",
]
_BODY_SELECTORS = [
    "div.article-body p",
    "div.art-body p",
    "div[data-testid='article-body'] p",
    "div.text p",
    "article p",
]
_LINK_SELECTORS = [
    "a.item-link",
    "a.art-l-item-title",
    "a[data-testid='article-link']",
    "div.search-result a",
    "h2 a",
    "h3 a",
]


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry (429, 5xx)."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_YNET_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _parse_ynet_date(raw: str) -> Optional[datetime]:
    """Try several common Ynet date formats; return None on failure."""
    raw = raw.strip()
    formats = [
        "%d.%m.%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_soup(soup: BeautifulSoup) -> Optional[datetime]:
    """Pull publication date from common Ynet date elements."""
    # 1. <time> element with datetime attribute
    time_el = soup.find("time")
    if time_el:
        dt_attr = time_el.get("datetime", "")
        parsed = _parse_ynet_date(dt_attr) if dt_attr else None
        if parsed:
            return parsed
        text = time_el.get_text(strip=True)
        parsed = _parse_ynet_date(text)
        if parsed:
            return parsed

    # 2. Meta tags
    for name in ("article:published_time", "date", "pubdate", "DC.date"):
        meta = soup.find("meta", {"property": name}) or soup.find(
            "meta", {"name": name}
        )
        if meta and meta.get("content"):
            parsed = _parse_ynet_date(meta["content"])
            if parsed:
                return parsed

    # 3. Known class-based spans
    for cls in ("date", "art-publishing-date", "article-date"):
        el = soup.find(class_=cls)
        if el:
            parsed = _parse_ynet_date(el.get_text(strip=True))
            if parsed:
                return parsed

    return None


class YnetScraper(BaseScraper):
    source_name = "ynet"
    language = "he"
    base_domain = "www.ynet.co.il"

    # ------------------------------------------------------------------ #
    #  Internal: rate-limited raw GET                                      #
    # ------------------------------------------------------------------ #

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("Ynet: sleeping %.2f s", delay)
        await asyncio.sleep(delay)

    async def _get(
        self, client: httpx.AsyncClient, url: str
    ) -> httpx.Response:
        """GET with retry logic for 429/5xx wrapped in tenacity."""

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
        Search Ynet for *query* and return up to *max_results* DiscoveredUrl.

        date_from / date_to: "YYYY-MM-DD" strings used to filter results.
        """
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)

        search_url = f"{_SEARCH_URL}?q={quote_plus(query)}&{_SEARCH_PARAMS}"
        discovered: list[DiscoveredUrl] = []

        async with _make_client() as client:
            try:
                resp = await self._get(client, search_url)
                resp.raise_for_status()
            except _RetryableHTTPError as exc:
                logger.error("Ynet discover: retries exhausted — %s", exc)
                return discovered
            except httpx.HTTPError as exc:
                logger.error("Ynet discover: HTTP error — %s", exc)
                return discovered

            soup = BeautifulSoup(resp.text, "lxml")

            # Gather candidate <a> tags using multiple selector strategies
            links: list[BeautifulSoup] = []
            for sel in _LINK_SELECTORS:
                links = soup.select(sel)
                if links:
                    break

            # Fallback: any anchor pointing to an internal article path
            if not links:
                links = [
                    a
                    for a in soup.find_all("a", href=True)
                    if "/article/" in a["href"] or "/news/" in a["href"]
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
                    logger.debug("Ynet: robots.txt blocks %s", full_url)
                    continue

                # Try to extract a date from the surrounding container
                container = a_tag.find_parent(
                    ["article", "div", "li", "section"]
                )
                pub_date: Optional[datetime] = None
                if container:
                    date_el = container.find(class_=lambda c: c and "date" in c.lower())
                    if date_el:
                        pub_date = _parse_ynet_date(date_el.get_text(strip=True))

                # Apply date filter when a date is available
                if pub_date:
                    if not (from_dt <= pub_date <= to_dt):
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

        logger.info("Ynet discover: found %d URLs for query=%r", len(discovered), query)
        return discovered

    # ------------------------------------------------------------------ #
    #  fetch                                                               #
    # ------------------------------------------------------------------ #

    async def fetch(self, url: str) -> ArticleResult:
        """Fetch a single Ynet article and return a populated ArticleResult."""
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
                        text = el.get_text(separator=" ", strip=True)
                        if text:
                            body_parts.append(text)
                    if body_parts:
                        break

            article_text = "\n\n".join(body_parts)

            # --- Publication date ---
            published_at = _extract_date_from_soup(soup)

            # --- Content classification ---
            content_type = self._classify_content(article_text, self.language)

            logger.info(
                "Ynet fetch: %s | title=%r | words=%d | type=%s",
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
