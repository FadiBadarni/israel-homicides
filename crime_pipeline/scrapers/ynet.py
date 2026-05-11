"""
Scraper for Ynet.co.il — Hebrew, server-rendered.

Uses httpx + BeautifulSoup. No JavaScript rendering required.
Rate-limited to 1 req / (request_delay + random jitter 0-2 s).
Tenacity exponential back-off on HTTP 429 / 5xx errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote, quote_plus, urljoin, urlparse

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

_GOOGLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://news.google.com/",
}

_GNEWS_RSS_BASE = "https://news.google.com/rss/search"
_YNET_RSS_URL = "https://www.ynet.co.il/Integration/StoryRss2.xml"
_BASE_URL = "https://www.ynet.co.il"

# Selectors tried in order — first match wins
_TITLE_SELECTORS = [
    "h1.mainTitle",
    "h1.art-title",
    "h1[data-testid='article-title']",
    "h1",
]
_BODY_SELECTORS = [
    # Modern Ynet (2024+) — React component class names
    ".ArticleBodyComponent .text_editor_paragraph",
    ".ArticleBodyComponent p",
    ".ArticleBodyComponent",
    ".PremiumArticleBody",
    # Legacy selectors retained as fallback
    "div.article-body p",
    "div.art-body p",
    "div[data-testid='article-body'] p",
    "div.text p",
    "article p",
]


def _extract_body_from_jsonld(html: str) -> str:
    """Extract articleBody from schema.org JSON-LD blocks.

    Modern Ynet (and most news sites) embed the full article in a
    ``<script type="application/ld+json">`` block whose JSON has an
    ``articleBody`` field. This is the canonical extraction path —
    immune to CSS-class churn and not blocked by JS rendering.
    Returns empty string if no JSON-LD block carries an articleBody.
    """
    import re
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>',
        html, re.S,
    )
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        # JSON-LD can be a single object or a list. Walk both.
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("articleBody"):
                body = obj["articleBody"]
                if isinstance(body, str) and body.strip():
                    return body.strip()
    return ""
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


class _RateLimitStop(Exception):
    """Raised when Google returns 429 — stop issuing further windows."""


def _make_client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_YNET_HEADERS,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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

    for name in ("article:published_time", "date", "pubdate", "DC.date"):
        meta = soup.find("meta", {"property": name}) or soup.find(
            "meta", {"name": name}
        )
        if meta and meta.get("content"):
            parsed = _parse_ynet_date(meta["content"])
            if parsed:
                return parsed

    for cls in ("date", "art-publishing-date", "article-date"):
        el = soup.find(class_=cls)
        if el:
            parsed = _parse_ynet_date(el.get_text(strip=True))
            if parsed:
                return parsed

    return None


def _build_windows(date_from: str, date_to: str) -> list[tuple[datetime, datetime]]:
    start = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(hours=48), end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def _matches_query(title: str, tags: str, query: str) -> bool:
    terms = [t.lower() for t in query.split() if t.strip()]
    haystack = (title + " " + tags).lower()
    return all(term in haystack for term in terms)


_GNEWS_DECODE_CACHE: dict[str, str | None] = {}
_GNEWS_DECODE_LOCK = asyncio.Lock()
# Path segments Google News uses for article-redirect URLs. As of 2026 both
# /articles/<base64> and /read/<base64> have been observed in the wild.
_GNEWS_ARTICLE_SEGMENTS = frozenset({"articles", "read"})


async def _resolve_google_url(
    client: httpx.AsyncClient, google_url: str
) -> str | None:
    """Resolve a Google News redirect URL to its canonical ynet.co.il URL.

    Uses the ``googlenewsdecoder`` PyPI library (community-maintained
    wrapper around Google's batchexecute API). The library is sync, so
    we run it in a worker thread to avoid blocking the event loop, and
    serialize concurrent decodes behind a module-level ``asyncio.Lock``
    so multi-window discover() runs don't burst Google's rate limit.

    The previous implementation reverse-engineered the page DOM
    (extracting ``data-n-a-sg`` / ``data-n-a-ts`` from a ``c-wiz``
    element) and broke when Google changed their HTML in early 2026.
    Delegating to a maintained library means the next Google change
    requires ``pip install --upgrade googlenewsdecoder`` rather than
    a re-reverse-engineering session.

    Per-process in-memory cache prevents re-decoding the same URL
    within a single pipeline run; cross-run caching could be added
    later via the SQLite DB if rate-limit pressure becomes real.

    The ``client`` argument is unused now (kept for backward-compat
    with the existing call sites).
    """
    parsed = urlparse(google_url)
    path_parts = parsed.path.strip("/").split("/")
    if (
        parsed.netloc != "news.google.com"
        or not _GNEWS_ARTICLE_SEGMENTS.intersection(path_parts)
    ):
        return None

    if google_url in _GNEWS_DECODE_CACHE:
        return _GNEWS_DECODE_CACHE[google_url]

    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:  # pragma: no cover — hard dep, missing means env broken
        logger.error("googlenewsdecoder not installed — discover() cannot resolve URLs")
        return None

    def _sync_decode() -> dict:
        # interval=1 spaces requests by 1s when called repeatedly to soften
        # rate-limit risk against Google's batchexecute endpoint.
        return gnewsdecoder(google_url, interval=1)

    # Serialize concurrent decodes — prevents bursts when multiple discover
    # windows resolve in parallel and trip Google's rate limit.
    async with _GNEWS_DECODE_LOCK:
        # Re-check cache inside lock (could have populated while waiting).
        if google_url in _GNEWS_DECODE_CACHE:
            return _GNEWS_DECODE_CACHE[google_url]
        try:
            result = await asyncio.to_thread(_sync_decode)
        except Exception as exc:  # pragma: no cover — network paths
            logger.warning("gnews decoder threw: %s", str(exc)[:160])
            _GNEWS_DECODE_CACHE[google_url] = None
            return None

    if not isinstance(result, dict) or not result.get("status"):
        logger.warning(
            "gnews decoder failed for %s — %s",
            google_url[:80],
            str(result)[:120],
        )
        _GNEWS_DECODE_CACHE[google_url] = None
        return None

    decoded_url = result.get("decoded_url")
    if not isinstance(decoded_url, str):
        _GNEWS_DECODE_CACHE[google_url] = None
        return None

    # Defensive: only accept Ynet URLs since this resolver lives in the
    # Ynet scraper. The Google News feed is queried with ``site:ynet.co.il``
    # so anything else is suspicious.
    if not decoded_url.startswith(("https://www.ynet.co.il/", "https://ynet.co.il/")):
        logger.warning("gnews decoder: unexpected domain in %s", decoded_url[:120])
        _GNEWS_DECODE_CACHE[google_url] = None
        return None

    _GNEWS_DECODE_CACHE[google_url] = decoded_url
    return decoded_url


async def _fetch_gnews_window(
    client: httpx.AsyncClient, query: str, start: datetime, end: datetime
) -> list[tuple[str, str | None, datetime | None]]:
    """Fetch one Google News RSS window. Returns list of (google_url, title, pubdate)."""
    q = (
        f"{query} site:ynet.co.il"
        f" after:{start.strftime('%Y-%m-%d')}"
        f" before:{end.strftime('%Y-%m-%d')}"
    )
    url = f"{_GNEWS_RSS_BASE}?q={quote_plus(q)}&hl=he&gl=IL&ceid=IL:he"

    try:
        resp = await client.get(url, headers=_GOOGLE_HEADERS, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning("gnews window fetch error: %s", exc)
        return []

    if resp.status_code == 429:
        raise _RateLimitStop("Google returned 429")
    if resp.status_code != 200:
        logger.warning("gnews window: HTTP %d for window %s-%s", resp.status_code, start.date(), end.date())
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("gnews window: XML parse error — %s", exc)
        return []

    items: list[tuple[str, str | None, datetime | None]] = []
    ns = {"media": "http://search.yahoo.com/mrss/"}
    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            continue
        link = link_el.text.strip()
        if "news.google.com" not in link:
            continue

        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else None

        pubdate_el = item.find("pubDate")
        pubdate = _parse_pubdate(pubdate_el.text if pubdate_el is not None else None)

        items.append((link, title, pubdate))

    logger.debug(
        "gnews window %s-%s: %d items",
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        len(items),
    )
    return items


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
        Discover Ynet articles via Google News RSS (primary) + Ynet RSS (supplement).

        Primary: Google News RSS with 48h window sharding and iterative bisection.
        Supplement: Ynet native RSS when date_to is within 72h of now.
        """
        results: list[DiscoveredUrl] = []
        seen: set[str] = set()

        gnews_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            http2=True,
        )

        try:
            # --- Phase 1: Google News RSS ---
            initial_windows = _build_windows(date_from, date_to)
            queue: deque[tuple[datetime, datetime, int]] = deque(
                (s, e, 0) for s, e in initial_windows
            )

            try:
                while queue:
                    if len(results) >= max_results:
                        break

                    win_start, win_end, depth = queue.popleft()

                    await asyncio.sleep(10)  # 10s between Google News requests
                    raw_items = await _fetch_gnews_window(gnews_client, query, win_start, win_end)

                    if len(raw_items) >= 90 and depth < 4 and (win_end - win_start) > timedelta(hours=3):
                        mid = win_start + (win_end - win_start) / 2
                        queue.appendleft((mid, win_end, depth + 1))
                        queue.appendleft((win_start, mid, depth + 1))
                        logger.debug(
                            "gnews_window_saturated: %d items in %s-%s, bisecting",
                            len(raw_items),
                            win_start.date(),
                            win_end.date(),
                        )
                        continue

                    if len(raw_items) == 0:
                        logger.warning(
                            "gnews window %s-%s returned 0 items (possible silent empty)",
                            win_start.date(),
                            win_end.date(),
                        )

                    for google_url, title, pubdate in raw_items:
                        if len(results) >= max_results:
                            break

                        canonical = await _resolve_google_url(gnews_client, google_url)
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
                    "gnews: 429 received — stopping window iteration, returning %d partial results",
                    len(results),
                )

            # --- Phase 2: Ynet RSS supplement ---
            date_to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            if date_to_dt >= datetime.now(timezone.utc) - timedelta(hours=72):
                if len(results) < max_results:
                    await self._fetch_ynet_rss_supplement(
                        gnews_client, query, date_from, date_to, results, seen, max_results
                    )

        except Exception as exc:
            logger.error("Ynet discover: unexpected error — %s", exc, exc_info=True)

        finally:
            await gnews_client.aclose()

        logger.info("Ynet discover: found %d URLs for query=%r", len(results), query)
        return results[:max_results]

    async def _fetch_ynet_rss_supplement(
        self,
        client: httpx.AsyncClient,
        query: str,
        date_from: str,
        date_to: str,
        results: list[DiscoveredUrl],
        seen: set[str],
        max_results: int,
    ) -> None:
        """Fetch Ynet's native RSS feed and merge matching recent articles."""
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)

        try:
            await asyncio.sleep(5)
            resp = await client.get(_YNET_RSS_URL, headers=_GOOGLE_HEADERS, timeout=20.0)
        except httpx.HTTPError as exc:
            logger.warning("ynet rss supplement: fetch failed — %s", exc)
            return

        if resp.status_code != 200:
            logger.warning("ynet rss supplement: HTTP %d", resp.status_code)
            return

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("ynet rss supplement: XML parse error — %s", exc)
            return

        channel = root.find("channel")
        if channel is None:
            return

        added = 0
        for item in channel.findall("item"):
            if len(results) >= max_results:
                break

            link_el = item.find("link")
            if link_el is None or not link_el.text:
                continue
            canonical = link_el.text.strip()
            if not canonical.startswith(("https://www.ynet.co.il/", "https://ynet.co.il/")):
                continue
            if canonical in seen:
                continue

            title_el = item.find("title")
            title_raw = title_el.text.strip() if title_el is not None and title_el.text else ""

            tags_el = item.find("tags")
            tags_raw = tags_el.text.strip() if tags_el is not None and tags_el.text else ""

            if not _matches_query(title_raw, tags_raw, query):
                continue

            pubdate_el = item.find("pubDate")
            pubdate = _parse_pubdate(pubdate_el.text if pubdate_el is not None else None)

            if pubdate and not (from_dt <= pubdate <= to_dt):
                continue

            seen.add(canonical)
            results.append(
                DiscoveredUrl(
                    url=canonical,
                    source=self.source_name,
                    language=self.language,
                    title=title_raw or None,
                    published_at=pubdate,
                    discovered_at=datetime.now(tz=timezone.utc),
                )
            )
            added += 1

        logger.info("ynet rss supplement: added %d articles", added)

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
            # Try JSON-LD articleBody first — canonical, immune to CSS churn,
            # works even when modern Ynet React components rename their classes.
            article_text = _extract_body_from_jsonld(raw_html)

            # Fall back to CSS selectors (modern + legacy) when JSON-LD is
            # absent or empty (e.g. on liveblog/category pages).
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
