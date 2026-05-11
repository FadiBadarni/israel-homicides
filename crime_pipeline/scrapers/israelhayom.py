"""
Scraper for IsraelHayom.co.il — Hebrew, server-rendered NextJS.

Uses httpx + BeautifulSoup. No JavaScript rendering required.
Discovery via Google News RSS with 48h window sharding and iterative
bisection. Article body extracted from JSON-LD articleBody (primary)
with CSS selector fallback.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ArticleResult, BaseScraper, DiscoveredUrl
from ._gnews import (
    _RateLimitStop,
    _build_windows,
    fetch_gnews_window,
    resolve_google_url,
)
from .ynet import (
    _RetryableHTTPError,
    _YNET_HEADERS,
    _extract_body_from_jsonld,
    _extract_date_from_soup,
    _make_client,
)

logger = logging.getLogger(__name__)

_VALID_PREFIXES = (
    "https://www.israelhayom.co.il/",
    "https://israelhayom.co.il/",
)
_SITE_DOMAIN = "israelhayom.co.il"

_TITLE_SELECTORS = [
    "h1.article-title",
    "h1[class*='title']",
    "h1[class*='Title']",
    "h1",
]
_BODY_SELECTORS = [
    "div.article-body p",
    "div[class*='article-content'] p",
    "div[class*='articleContent'] p",
    "article p",
]


class IsraelhayomScraper(BaseScraper):
    source_name = "israelhayom"
    language = "he"
    base_domain = "www.israelhayom.co.il"

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("IsraelHayom: sleeping %.2f s", delay)
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
        """Discover IsraelHayom articles via Google News RSS with 48h window sharding."""
        results: list[DiscoveredUrl] = []
        seen: set[str] = set()

        gnews_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            http2=True,
        )

        try:
            initial_windows = _build_windows(date_from, date_to)
            queue: deque[tuple[datetime, datetime, int]] = deque(
                (s, e, 0) for s, e in initial_windows
            )

            try:
                while queue:
                    if len(results) >= max_results:
                        break

                    win_start, win_end, depth = queue.popleft()

                    await asyncio.sleep(10)
                    raw_items = await fetch_gnews_window(
                        gnews_client, query, win_start, win_end, _SITE_DOMAIN
                    )

                    if (
                        len(raw_items) >= 90
                        and depth < 4
                        and (win_end - win_start) > timedelta(hours=3)
                    ):
                        mid = win_start + (win_end - win_start) / 2
                        queue.appendleft((mid, win_end, depth + 1))
                        queue.appendleft((win_start, mid, depth + 1))
                        logger.debug(
                            "gnews_window_saturated: %d items in %s-%s, bisecting",
                            len(raw_items), win_start.date(), win_end.date(),
                        )
                        continue

                    if len(raw_items) == 0:
                        logger.warning(
                            "gnews window %s-%s returned 0 items (possible silent empty)",
                            win_start.date(), win_end.date(),
                        )

                    for google_url, title, pubdate in raw_items:
                        if len(results) >= max_results:
                            break

                        canonical = await resolve_google_url(
                            gnews_client, google_url, _VALID_PREFIXES
                        )
                        if canonical is None:
                            logger.warning("google_redirect_unresolved: %s", google_url)
                            continue

                        if canonical in seen:
                            continue
                        seen.add(canonical)

                        results.append(
                            DiscoveredUrl(
                                url=canonical,
                                source=self.source_name,
                                language=self.language,
                                title=title,
                                published_at=pubdate,
                                discovered_at=datetime.now(tz=timezone.utc),
                            )
                        )

                        if len(results) >= max_results:
                            break

            except _RateLimitStop:
                logger.warning(
                    "israelhayom gnews: 429 received — stopping, returning %d partial results",
                    len(results),
                )

        except Exception as exc:
            logger.error("IsraelHayom discover: unexpected error — %s", exc, exc_info=True)

        finally:
            await gnews_client.aclose()

        logger.info("IsraelHayom discover: found %d URLs for query=%r", len(results), query)
        return results[:max_results]

    async def fetch(self, url: str) -> ArticleResult:
        """Fetch a single IsraelHayom article and return a populated ArticleResult."""
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
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="", content_type="non_article",
                    fetch_status="fetch_failed", error_message=str(exc),
                )
            except httpx.TimeoutException as exc:
                return ArticleResult(
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="", content_type="non_article",
                    fetch_status="timeout", error_message=str(exc),
                )
            except httpx.HTTPError as exc:
                return ArticleResult(
                    url=url, final_url=url, source=self.source_name,
                    language=self.language, title=None, published_at=None,
                    raw_html="", article_text="", content_type="non_article",
                    fetch_status="fetch_failed", error_message=str(exc),
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

            # --- Body: JSON-LD primary, CSS fallback ---
            article_text = _extract_body_from_jsonld(raw_html)
            if not article_text:
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

            content_type = self._classify_content(article_text, self.language)

            logger.info(
                "IsraelHayom fetch: %s | title=%r | words=%d | type=%s",
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
                error_message=None,
            )
