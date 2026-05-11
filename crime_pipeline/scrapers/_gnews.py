"""
Shared Google News RSS discovery helpers.

Used by any scraper that discovers articles via Google News RSS
(currently YnetScraper and IsraelhayomScraper).

All state (cache, lock) is module-level so it is shared across
scraper instances within the same process, preventing duplicate
URL resolution work in multi-source pipeline runs.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

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

# Path segments Google News uses for article-redirect URLs. As of 2026 both
# /articles/<base64> and /read/<base64> have been observed in the wild.
_GNEWS_ARTICLE_SEGMENTS = frozenset({"articles", "read"})

_GNEWS_DECODE_CACHE: dict[str, str | None] = {}
_GNEWS_DECODE_LOCK = asyncio.Lock()


class _RateLimitStop(Exception):
    """Raised when Google returns 429 — caller should stop issuing further windows."""


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


async def resolve_google_url(
    client: httpx.AsyncClient,
    google_url: str,
    valid_prefixes: tuple[str, ...],
) -> str | None:
    """Resolve a Google News redirect URL to its canonical article URL.

    Uses the ``googlenewsdecoder`` PyPI library (community-maintained wrapper
    around Google's batchexecute API). The library is sync, so it runs in a
    worker thread to avoid blocking the event loop. Concurrent decodes are
    serialized behind a module-level lock to prevent rate-limit bursts.

    ``valid_prefixes`` — caller specifies which domains are acceptable
    (e.g. ``("https://www.ynet.co.il/", "https://ynet.co.il/")``).
    Decoded URLs not matching any prefix are rejected and return None.

    The ``client`` argument is retained for API compatibility but is
    not used (the library handles its own HTTP).
    """
    from urllib.parse import urlparse

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
    except ImportError:  # pragma: no cover
        logger.error("googlenewsdecoder not installed — discover() cannot resolve URLs")
        return None

    def _sync_decode() -> dict:
        return gnewsdecoder(google_url, interval=1)

    async with _GNEWS_DECODE_LOCK:
        if google_url in _GNEWS_DECODE_CACHE:
            return _GNEWS_DECODE_CACHE[google_url]
        try:
            result = await asyncio.to_thread(_sync_decode)
        except Exception as exc:  # pragma: no cover
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

    if not decoded_url.startswith(valid_prefixes):
        logger.warning("gnews decoder: unexpected domain in %s", decoded_url[:120])
        _GNEWS_DECODE_CACHE[google_url] = None
        return None

    _GNEWS_DECODE_CACHE[google_url] = decoded_url
    return decoded_url


async def fetch_gnews_window(
    client: httpx.AsyncClient,
    query: str,
    start: datetime,
    end: datetime,
    site_domain: str,
    hl: str = "he",
    gl: str = "IL",
    ceid: str = "IL:he",
) -> list[tuple[str, str | None, datetime | None]]:
    """Fetch one Google News RSS window for ``site_domain``.

    Returns a list of ``(google_redirect_url, title, pubdate)`` tuples.
    Raises ``_RateLimitStop`` on HTTP 429.
    """
    q = (
        f"{query} site:{site_domain}"
        f" after:{start.strftime('%Y-%m-%d')}"
        f" before:{end.strftime('%Y-%m-%d')}"
    )
    url = f"{_GNEWS_RSS_BASE}?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

    try:
        resp = await client.get(url, headers=_GOOGLE_HEADERS, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning("gnews window fetch error: %s", exc)
        return []

    if resp.status_code == 429:
        raise _RateLimitStop("Google returned 429")
    if resp.status_code != 200:
        logger.warning(
            "gnews window: HTTP %d for %s window %s-%s",
            resp.status_code, site_domain, start.date(), end.date(),
        )
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("gnews window: XML parse error — %s", exc)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    items: list[tuple[str, str | None, datetime | None]] = []
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
        "gnews window %s-%s (%s): %d items",
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        site_domain,
        len(items),
    )
    return items
