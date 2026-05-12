"""
Scraper for Makan.org.il — Arabic-language public broadcaster (part of Kan,
Israel's public broadcaster). Covers Arab-society Israeli news heavily,
including Bedouin/Negev cases that Arab48 and Ynet underserve.

Discovery: Google News RSS with ``site:makan.org.il`` (Makan's on-site
search is client-rendered JS — no usable HTML endpoint). Same window-
sharding + 429 backoff pattern as Ynet.

Fetch: ``.article-content`` selector for body; JSON-LD ``NewsArticle`` for
publication date and headline (canonical fields, immune to CSS class
churn on the Makan/Kan platform).

Added 2026-05 after the Jan 2026 truth investigation showed two real
homicide victims (تيمور عطالله, بسمة أبو فريحة) had no Ynet or Arab48
coverage but were on Makan.
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

_MAKAN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,he-IL;q=0.8,en-US;q=0.6,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Both schemes/hosts observed on Makan article URLs. resolve_google_url
# uses prefix matching to verify the redirect landed on Makan.
_MAKAN_VALID_PREFIXES = (
    "https://www.makan.org.il/",
    "https://makan.org.il/",
)

# Selectors tried in order — first non-empty wins. ``.article-content``
# is the standard container on the Kan platform Makan runs on.
_BODY_SELECTORS = [
    ".article-content",
    "main article",
    "article",
]


class _RetryableHTTPError(Exception):
    """Raised for status codes that warrant a retry (429, 5xx)."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_MAKAN_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _extract_published_from_jsonld(html: str) -> Optional[datetime]:
    """Read ``datePublished`` from any schema.org NewsArticle / Article
    JSON-LD block. Canonical source — survives CSS / DOM refactors.

    Returns the first parseable datetime found, or None.
    """
    for raw in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    ):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        # Some pages return a list of entities under one script tag.
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if t not in ("NewsArticle", "Article"):
                continue
            pub = item.get("datePublished") or item.get("dateModified")
            if not pub:
                continue
            try:
                # Handle ``2026-01-27T12:00:39+03:00`` and Z variants.
                if pub.endswith("Z"):
                    return datetime.fromisoformat(pub[:-1]).replace(tzinfo=timezone.utc)
                return datetime.fromisoformat(pub)
            except ValueError:
                continue
    return None


def _extract_headline_from_jsonld(html: str) -> Optional[str]:
    """Read NewsArticle ``headline`` from JSON-LD. og:title is a fine
    fallback caller-side; we just prefer the canonical schema field
    when available."""
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


class MakanScraper(BaseScraper):
    source_name = "makan"
    language = "ar"
    base_domain = "www.makan.org.il"

    # ------------------------------------------------------------------ #
    #  Internal: rate-limited raw GET                                      #
    # ------------------------------------------------------------------ #

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("Makan: sleeping %.2f s", delay)
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

    # ------------------------------------------------------------------ #
    #  discover — Google News RSS with site:makan.org.il                   #
    # ------------------------------------------------------------------ #

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        """Discover Makan articles via Google News RSS.

        Mirrors the Ynet discover loop: 48h windowing with iterative
        bisection on saturated windows (>= 90 items), 10s spacing
        between Google requests, 429 backoff via _RateLimitStop.
        Locale is ``ar/IL/IL:ar`` because Makan is Arabic.
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
                        "makan.org.il",
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
                            gnews_client, google_url, _MAKAN_VALID_PREFIXES
                        )
                        if canonical is None:
                            logger.warning(
                                "makan: google_redirect_unresolved: %s", google_url
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
                    "makan gnews: 429 — stopping, returning %d partial results",
                    len(results),
                )

        except Exception as exc:
            logger.error(
                "Makan discover: unexpected error — %s", exc, exc_info=True
            )

        finally:
            await gnews_client.aclose()

        logger.info(
            "Makan discover: found %d URLs for query=%r", len(results), query
        )
        return results[:max_results]

    # ------------------------------------------------------------------ #
    #  fetch                                                               #
    # ------------------------------------------------------------------ #

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

            # Title — prefer JSON-LD headline (canonical), fall back to
            # og:title, then h1, then <title>.
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

            # Body — try selectors in priority order. Makan's article body
            # lives in ``.article-content`` on the standard template; the
            # ``main article`` and bare ``article`` fallbacks defend
            # against future template changes.
            body_text = ""
            for sel in _BODY_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(separator=" ", strip=True)
                    if len(text) > 200:
                        body_text = text
                        break

            # Publication date — JSON-LD ``datePublished`` is canonical.
            published_at = _extract_published_from_jsonld(raw_html)

            content_type = self._classify_content(body_text, self.language)

            logger.info(
                "Makan fetch: %s | title=%r | words=%d | type=%s",
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
