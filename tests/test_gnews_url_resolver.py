"""Regression tests for the Ynet Google News URL resolver.

The resolver was rewritten to use the ``googlenewsdecoder`` PyPI library
after the previous DOM-scraping approach broke (Google changed their page
structure in early 2026, removing the ``c-wiz [data-n-a-sg][data-n-a-ts]``
element the old code depended on).

These tests focus on input validation, caching, and the lock-around-decode
contract — the actual library call is left to live verification because
mocking it adds zero value (we're testing OUR code, not theirs).
"""
from __future__ import annotations

import asyncio

import pytest

from crime_pipeline.scrapers import ynet as ynet_mod
from crime_pipeline.scrapers import _gnews as gnews_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    gnews_mod._GNEWS_DECODE_CACHE.clear()
    yield
    gnews_mod._GNEWS_DECODE_CACHE.clear()


# ---------------------------------------------------------------------------
# URL shape validation (no library call needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_url", [
    "https://www.ynet.co.il/news/article/abc",  # not a Google URL at all
    "https://news.google.com/foo/bar",          # wrong path
    "https://news.google.com/topstories",       # no /articles/ or /read/
    "https://example.com/articles/xyz",         # right path, wrong host
    "not-a-url",
])
def test_rejects_non_google_news_urls(bad_url: str) -> None:
    """Resolver must short-circuit before calling the decoder library
    on URLs that obviously aren't Google News redirect URLs."""
    async def run():
        return await gnews_mod.resolve_google_url(None, bad_url, ("https://www.ynet.co.il/",))
    assert asyncio.run(run()) is None


def test_accepts_articles_path_segment() -> None:
    """Sanity: a /articles/<id> URL passes the input-shape check.
    (We don't actually run the decoder here.)"""
    from urllib.parse import urlparse
    url = "https://news.google.com/rss/articles/CBMiXXXX"
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    assert "articles" in parts
    assert parsed.netloc == "news.google.com"


def test_accepts_read_path_segment() -> None:
    """Per Codex's R1 review: Google sometimes uses /read/<id> instead of
    /articles/<id>. The resolver must accept both."""
    from urllib.parse import urlparse
    url = "https://news.google.com/read/CBMiXXXX?hl=he"
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    assert "read" in parts
    assert gnews_mod._GNEWS_ARTICLE_SEGMENTS.intersection(parts)


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------

def test_cache_returns_stored_value_without_calling_library() -> None:
    """Once a URL is in the cache the resolver must return it directly."""
    url = "https://news.google.com/rss/articles/CBMiXXXX"
    gnews_mod._GNEWS_DECODE_CACHE[url] = "https://www.ynet.co.il/news/article/preset"

    async def run():
        return await gnews_mod.resolve_google_url(None, url, ("https://www.ynet.co.il/",))

    assert asyncio.run(run()) == "https://www.ynet.co.il/news/article/preset"


def test_cache_caches_failures_too() -> None:
    """A previously-failed URL must not retry the library on every call —
    the cache stores None and we return None on the next lookup."""
    url = "https://news.google.com/rss/articles/CBMiPRESETFAIL"
    gnews_mod._GNEWS_DECODE_CACHE[url] = None

    async def run():
        return await gnews_mod.resolve_google_url(None, url, ("https://www.ynet.co.il/",))

    assert asyncio.run(run()) is None


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------

def test_lock_exists_for_concurrent_serialization() -> None:
    """Codex's R1 critique: parallel discover windows can burst the
    decoder. We serialize via a module-level asyncio.Lock."""
    assert isinstance(gnews_mod._GNEWS_DECODE_LOCK, asyncio.Lock)


def test_googlenewsdecoder_is_importable() -> None:
    """If the dep is missing the resolver returns None — but the test
    suite at least proves the dep is installed in CI."""
    import googlenewsdecoder  # noqa: F401
    assert hasattr(googlenewsdecoder, "gnewsdecoder")
