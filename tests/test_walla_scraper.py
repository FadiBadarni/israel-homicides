"""Structural tests for the Walla scraper.

Walla was added 2026-05 after the Walla-vs-Kan debate (see
``~/.claude-octopus/debates/walla-vs-kan-001/synthesis.md``). Kan was
blocked at the transport layer (httpx 403 even with a real Chrome UA);
Walla works cleanly. Walla closes the Bedouin/Negev femicide coverage
gap (specifically the Jan 2026 Basma Abu Freiha case).

Tests cover signature / contract shape; live HTTP probing is covered by
the smoke script in ``scripts/`` and the actual sweep runs.
"""
from __future__ import annotations

import inspect

from crime_pipeline.scrapers import SCRAPER_REGISTRY, get_scraper
from crime_pipeline.scrapers.walla import WallaScraper


def test_walla_registered_in_registry() -> None:
    """Missing the registration silently drops Walla from the keyword
    sweep and the --sources CLI."""
    assert "walla" in SCRAPER_REGISTRY
    assert SCRAPER_REGISTRY["walla"] is WallaScraper


def test_walla_scraper_basic_attrs() -> None:
    assert WallaScraper.source_name == "walla"
    assert WallaScraper.language == "he"
    assert WallaScraper.base_domain == "news.walla.co.il"


def test_walla_in_tier_registry() -> None:
    """Walla is a major Hebrew commercial outlet — Tier 1 alongside
    Ynet/Mako/Israel Hayom."""
    from crime_pipeline.scrapers.tier_registry import (
        DOMAIN_TO_TIER, DOMAIN_TO_PUBLISHER, TIER_1_DOMAINS,
    )
    # The registry pre-existed Walla; the bare and news subdomain are
    # both there. We assert at least the news subdomain is recognised
    # because that's where the article URLs resolve to.
    assert "news.walla.co.il" in TIER_1_DOMAINS
    assert DOMAIN_TO_TIER.get("news.walla.co.il") == 1


def test_get_scraper_constructs_walla() -> None:
    scraper = get_scraper("walla")
    assert isinstance(scraper, WallaScraper)


def test_walla_discover_signature() -> None:
    sig = inspect.signature(WallaScraper.discover)
    params = sig.parameters
    assert "query" in params
    assert "date_from" in params
    assert "date_to" in params
    assert "max_results" in params


def test_walla_discover_uses_google_news_helper() -> None:
    """Discovery is via Google News RSS with site:news.walla.co.il.
    If this regresses we silently start scraping the wrong site (or
    nothing) — pin the locale + site filter literally."""
    src = inspect.getsource(WallaScraper.discover)
    assert "fetch_gnews_window" in src
    assert "news.walla.co.il" in src
    # Hebrew locale
    assert 'hl="he"' in src or "hl='he'" in src


def test_walla_body_selector_chain_includes_article() -> None:
    """Walla's JSON-LD articleBody is empty on production templates.
    The fetch path falls through to ``<article>`` as the verified
    working selector. Order: most specific first."""
    from crime_pipeline.scrapers.walla import _BODY_SELECTORS
    assert "article" in _BODY_SELECTORS
    # JSON-LD-style hint should come before the bare <article> tag
    assert _BODY_SELECTORS[0] == '[itemprop="articleBody"]'
    # <article> must be present as the verified fallback
    assert _BODY_SELECTORS.index("article") < _BODY_SELECTORS.index("main")


def test_walla_date_extractor_handles_offset() -> None:
    """Walla's JSON-LD timestamps include +02:00 (IST). Coerce must
    accept this without throwing."""
    from crime_pipeline.scrapers.walla import _extract_published_from_jsonld
    html = '''
    <html><head><script type="application/ld+json">
      {"@type":"NewsArticle","headline":"x",
       "datePublished":"2026-01-19T03:13:00+02:00"}
    </script></head></html>
    '''
    dt = _extract_published_from_jsonld(html)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 1 and dt.day == 19


def test_walla_headline_extractor() -> None:
    """JSON-LD headline preferred over og:title / h1 because canonical."""
    from crime_pipeline.scrapers.walla import _extract_headline_from_jsonld
    html = '''
    <html><head><script type="application/ld+json">
      {"@type":"NewsArticle","headline":"בסמה אבו פריחה נורתה למוות"}
    </script></head></html>
    '''
    h = _extract_headline_from_jsonld(html)
    assert h == "בסמה אבו פריחה נורתה למוות"


def test_walla_listed_in_hebrew_sources_for_sweep() -> None:
    """The keyword-mode plan must dispatch Hebrew keywords to both
    ynet and walla. Without Walla in the source list, the scraper
    is dead code for keyword-mode sweeps."""
    from crime_pipeline import __main__ as cli_module
    src = inspect.getsource(cli_module)
    # Hebrew side must include ynet AND walla in some order.
    assert (
        '"ynet", "walla"' in src
        or '"walla", "ynet"' in src
        or "'ynet', 'walla'" in src
        or "'walla', 'ynet'" in src
    )


def test_walla_valid_prefixes_cover_both_hostnames() -> None:
    """Google News redirects can land on either ``news.walla.co.il`` or
    bare ``walla.co.il`` (rare, but observed). The validator must
    accept both so we don't silently reject legitimate URLs."""
    from crime_pipeline.scrapers.walla import _WALLA_VALID_PREFIXES
    assert any("news.walla.co.il" in p for p in _WALLA_VALID_PREFIXES)
    assert any(
        "//walla.co.il" in p or "//www.walla.co.il" in p
        for p in _WALLA_VALID_PREFIXES
    )
