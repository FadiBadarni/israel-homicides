"""Scraper for kul-alarab.com — Arabic, server-rendered (article pages).

Discovery uses the JSON API at ``apiv3.alarab.com`` which powers the
site's infinite-scroll tag pages. Articles can be retrieved with full
date precision back to ~2011 via the murder tag (``قتل``) — much deeper
coverage than Arab48's search/tag pages.

Endpoint shape::

    GET https://apiv3.alarab.com/api/search/{query}/{page}
    -> {data: [{ID, pdate, title, url, ...}, ...]}

The page index is 1-based, 10 items per page. Empty ``data`` signals
end of archive. Each item's ``url`` field is the article path
(``/Article/{ID}``); we resolve it against ``www.kul-alarab.com`` for
fetch.

Article body lives in ``.fdatawrap`` on the detail page.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urljoin

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

_SITE_BASE = "https://www.kul-alarab.com"
_API_BASE = "https://apiv3.alarab.com/api/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,he-IL;q=0.8,en-US;q=0.6,en;q=0.5",
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.5",
}


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry."""


def _parse_pdate(raw: str) -> Optional[datetime]:
    """API returns ``2026-05-14 08:37:19`` (no timezone — treat as UTC)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class KulAlarabScraper(BaseScraper):
    source_name = "kul_alarab"
    language = "ar"
    base_domain = "www.kul-alarab.com"

    async def _sleep(self) -> None:
        # Smaller delay than HTML scrapers — the API is rate-limit-tolerant
        # and the per-page cost is just one JSON round-trip.
        await asyncio.sleep(self.request_delay)

    def _make_client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=_HEADERS,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            http2=True,
        )

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
        max_pages: int = 30,
    ) -> list[DiscoveredUrl]:
        """Walk the JSON API for ``query`` until window is satisfied.

        ``query`` is the tag/keyword (e.g. ``"قتل"``). The API serves
        results newest-first, so we stop early when ``pdate`` falls
        below ``date_from``. Items newer than ``date_to`` are skipped
        without ending the walk (more matches may follow).

        Stop conditions (any one ends the loop):
          1. ``len(discovered) >= max_results``
          2. ``page > max_pages``
          3. API returns empty ``data`` (end of archive)
          4. Every item on a page is older than ``date_from``
        """
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
        discovered: list[DiscoveredUrl] = []
        seen: set[str] = set()

        async with self._make_client() as client:
            for page in range(1, max_pages + 1):
                if len(discovered) >= max_results:
                    break

                api_url = f"{_API_BASE}/{quote(query)}/{page}"
                try:
                    resp = await self._get(client, api_url)
                    resp.raise_for_status()
                except (_RetryableHTTPError, httpx.HTTPError) as exc:
                    logger.error(
                        "Kul-Alarab discover page %d: HTTP error — %s",
                        page, exc,
                    )
                    break

                try:
                    payload = resp.json()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Kul-Alarab discover page %d: JSON parse failed — %s",
                        page, exc,
                    )
                    break

                items = payload.get("data") or []
                if not items:
                    logger.info(
                        "Kul-Alarab discover: API returned no items at page %d "
                        "(end of archive)", page,
                    )
                    break

                kept_this_page = 0
                pre_window = 0  # items newer than to_dt
                post_window = 0  # items older than from_dt

                for it in items:
                    pdate = _parse_pdate(it.get("pdate") or "")
                    article_id = it.get("ID") or ""
                    href = it.get("url") or (f"/Article/{article_id}" if article_id else "")
                    if not href:
                        continue
                    full_url = urljoin(_SITE_BASE, href)
                    if full_url in seen:
                        continue
                    seen.add(full_url)

                    if pdate is None:
                        # Without a usable date we can't apply the window;
                        # keep the URL and let downstream stages decide.
                        discovered.append(DiscoveredUrl(
                            url=full_url,
                            source=self.source_name,
                            language=self.language,
                            title=it.get("title"),
                            published_at=None,
                            discovered_at=datetime.now(timezone.utc),
                        ))
                        kept_this_page += 1
                        continue

                    if pdate > to_dt:
                        pre_window += 1
                        continue
                    if pdate < from_dt:
                        post_window += 1
                        continue

                    discovered.append(DiscoveredUrl(
                        url=full_url,
                        source=self.source_name,
                        language=self.language,
                        title=it.get("title"),
                        published_at=pdate,
                        discovered_at=datetime.now(timezone.utc),
                    ))
                    kept_this_page += 1

                logger.info(
                    "Kul-Alarab discover: page %d kept=%d pre_window=%d "
                    "post_window=%d (cumulative=%d) for query=%r",
                    page, kept_this_page, pre_window, post_window,
                    len(discovered), query,
                )

                # Early exit when every item on this page is older than from_dt:
                # the API is sorted newest-first, so no later page will contain
                # in-window items.
                if post_window == len(items):
                    logger.info(
                        "Kul-Alarab discover: entire page older than window "
                        "(%d items) — stopping early at page %d",
                        len(items), page,
                    )
                    break

        logger.info(
            "Kul-Alarab discover: found %d URLs for query=%r",
            len(discovered), query,
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

        async with self._make_client() as client:
            try:
                resp = await self._get(client, url)
                resp.raise_for_status()
            except _RetryableHTTPError as exc:
                return ArticleResult(
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article", fetch_status="fetch_failed",
                    error_message=str(exc),
                )
            except httpx.TimeoutException as exc:
                return ArticleResult(
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article", fetch_status="timeout",
                    error_message=str(exc),
                )
            except httpx.HTTPError as exc:
                return ArticleResult(
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article", fetch_status="fetch_failed",
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

            # ``.fdatawrap`` is the article body; everything else on the
            # page is chrome (nav, related links, ads, comments).
            body_parts: list[str] = []
            body = soup.select_one(".fdatawrap")
            if body:
                text = body.get_text(" ", strip=True)
                if text:
                    body_parts.append(text)

            article_text = "\n\n".join(dict.fromkeys(body_parts))

            # Date: prefer meta tags, fall back to nothing (the API gave
            # us the date during discover; this is a backstop).
            published_at: Optional[datetime] = None
            for key in ("article:published_time", "article:modified_time"):
                meta = soup.find("meta", {"property": key})
                if meta and meta.get("content"):
                    published_at = _parse_pdate(meta["content"])
                    if published_at:
                        break

            content_type = self._classify_content(article_text, self.language)
            logger.info(
                "Kul-Alarab fetch: %s | title=%r | words=%d | type=%s",
                final_url, title, len(article_text.split()), content_type,
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
            )
