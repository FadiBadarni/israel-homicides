"""
Scraper for Panet.com — Arabic news site, server-rendered article pages.

Discovery uses Google News RSS with ``site:panet.com`` (mirrors the
Walla / Makan / Ynet pattern). This replaces an earlier Playwright-
based scraper that broke when Panet's SPA layout changed and was never
registered in the pipeline. The GNews path is faster (no browser),
more reliable (no anti-bot), and consistent with the rest of the
Arabic-source set.

Fetch: httpx + BeautifulSoup. JSON-LD ``NewsArticle`` for headline +
datePublished; body falls through to DOM selectors because Panet's
JSON-LD ``articleBody`` is empty on the live template.

Caveat: Panet covers broad current affairs, so the ``قتل`` keyword
matches many non-homicide articles (politics, education, opinion).
Triage filters those out; expect a lower triage-yes rate than tag-
based sources like kul-alarab.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
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

from ._gnews import (
    _RateLimitStop,
    _build_windows,
    fetch_gnews_window,
    resolve_google_url,
)
from .base import ArticleResult, BaseScraper, DiscoveredUrl

logger = logging.getLogger(__name__)

_PANET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,he-IL;q=0.8,en-US;q=0.6,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    # NOTE: Brotli (br) intentionally omitted. Panet returns a 21KB stub
    # response (no title, no body) when ``br`` is in Accept-Encoding,
    # vs the full 134KB article HTML without it. Verified empirically
    # on https://panet.com/story/4106194 — minimal headers + no-br both
    # work; full-Walla headers with br break the response.
    "Accept-Encoding": "gzip, deflate",
}

# Google redirects validated against this prefix set. Production traffic
# uses ``panet.com`` and ``www.panet.com``; older articles may resolve
# to ``arabic.panet.com`` so we accept that too.
_PANET_VALID_PREFIXES = (
    "https://panet.com/",
    "https://www.panet.com/",
    "https://arabic.panet.com/",
)

# Body selectors in priority order. Verified on live Panet pages
# (https://panet.com/story/{id}): the article body lives in
# ``.story-content`` (~700 words). The schema.org / generic fallbacks
# below are kept as a safety net in case Panet ships a template
# refresh that adds them.
_BODY_SELECTORS = [
    ".story-content",
    '[itemprop="articleBody"]',
    ".article-content",
    ".article-body",
    ".article__body",
    "article",
    "main",
]


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry (429, 5xx)."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_PANET_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _extract_published_from_jsonld(html: str) -> Optional[datetime]:
    """Read ``datePublished`` from any schema.org NewsArticle / Article
    JSON-LD block. Canonical signal that survives template churn."""
    for raw in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    ):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") not in ("NewsArticle", "Article"):
                continue
            pub = item.get("datePublished") or item.get("dateModified")
            if not pub:
                continue
            try:
                if pub.endswith("Z"):
                    return datetime.fromisoformat(pub[:-1]).replace(
                        tzinfo=timezone.utc
                    )
                return datetime.fromisoformat(pub)
            except ValueError:
                continue
    return None


def _extract_headline_from_jsonld(html: str) -> Optional[str]:
    """Read NewsArticle ``headline`` from JSON-LD. og:title is a
    fine fallback at the caller, but JSON-LD is canonical."""
    for raw in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    ):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("NewsArticle", "Article"):
                h = item.get("headline")
                if h and isinstance(h, str):
                    return h.strip()
    return None


class PanetScraper(BaseScraper):
    source_name = "panet"
    language = "ar"
    base_domain = "panet.com"

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        await asyncio.sleep(delay)

    async def _get(
        self, client: httpx.AsyncClient, url: str
    ) -> httpx.Response:
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

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        """Discover Panet articles via Google News RSS with site filter.

        Same windowing + bisection pattern as Walla/Makan: 48h initial
        windows, 10s spacing, bisect saturated windows (>=90 items) up
        to depth 4. 429 backoff via ``_RateLimitStop``.
        """
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
                        gnews_client, query, win_start, win_end,
                        "panet.com",
                        hl="ar", gl="IL", ceid="IL:ar",
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

                    for google_url, title, pubdate in raw_items:
                        if len(results) >= max_results:
                            break

                        canonical = await resolve_google_url(
                            gnews_client, google_url, _PANET_VALID_PREFIXES
                        )
                        if canonical is None:
                            logger.warning(
                                "panet: google_redirect_unresolved: %s", google_url
                            )
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

            except _RateLimitStop:
                logger.warning(
                    "panet gnews: 429 — stopping, returning %d partial results",
                    len(results),
                )

        except Exception as exc:
            logger.error(
                "Panet discover: unexpected error — %s", exc, exc_info=True
            )

        finally:
            await gnews_client.aclose()

        logger.info(
            "Panet discover: found %d URLs for query=%r", len(results), query
        )
        return results[:max_results]

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
                    url=url, final_url=url,
                    source=self.source_name, language=self.language,
                    title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article",
                    fetch_status="fetch_failed",
                    error_message=str(exc),
                )
            except httpx.TimeoutException as exc:
                return ArticleResult(
                    url=url, final_url=url,
                    source=self.source_name, language=self.language,
                    title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article",
                    fetch_status="timeout",
                    error_message=str(exc),
                )
            except httpx.HTTPError as exc:
                return ArticleResult(
                    url=url, final_url=url,
                    source=self.source_name, language=self.language,
                    title=None, published_at=None,
                    raw_html="", article_text="",
                    content_type="non_article",
                    fetch_status="fetch_failed",
                    error_message=str(exc),
                )

            raw_html = resp.text
            final_url = str(resp.url)
            soup = BeautifulSoup(raw_html, "lxml")

            # Title — JSON-LD headline (canonical), then og:title, then h1.
            title: Optional[str] = _extract_headline_from_jsonld(raw_html)
            if not title:
                og = soup.find("meta", attrs={"property": "og:title"})
                if og and og.get("content"):
                    title = og["content"].strip()
            if not title:
                h1 = soup.find("h1")
                if h1:
                    title = h1.get_text(strip=True)
            if not title and soup.title:
                title = soup.title.get_text(strip=True)

            # Body via DOM (JSON-LD articleBody is empty on the template).
            # First selector yielding > 200 chars wins.
            body_text = ""
            for sel in _BODY_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(separator=" ", strip=True)
                    if len(text) > 200:
                        body_text = text
                        break

            published_at = _extract_published_from_jsonld(raw_html)

            content_type = self._classify_content(body_text, self.language)

            logger.info(
                "Panet fetch: %s | title=%r | words=%d | type=%s",
                final_url, title,
                len(body_text.split()), content_type,
            )

            return ArticleResult(
                url=url,
                final_url=final_url,
                source=self.source_name,
                language=self.language,
                title=title,
                published_at=published_at,
                raw_html=raw_html,
                article_text=body_text,
                content_type=content_type,
                fetch_status="success",
                error_message=None,
            )
