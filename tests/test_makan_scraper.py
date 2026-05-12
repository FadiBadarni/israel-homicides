"""Structural tests for the Makan scraper.

Makan (مكان, the Arabic-language public broadcaster — Kan-affiliated)
was added 2026-05 after the Jan 2026 truth investigation showed real
homicide victims (تيمور عطالله, بسمة أبو فريحة) with Makan-only coverage.

Tests cover signature/contract shape; live HTTP probing of Google News
and the Makan site is out of scope here (covered by /scripts/demo_*).
"""
from __future__ import annotations

import inspect

from crime_pipeline.scrapers import SCRAPER_REGISTRY, get_scraper
from crime_pipeline.scrapers.makan import MakanScraper


def test_makan_registered_in_registry() -> None:
    """The keyword-mode sweep + --sources CLI both go through
    SCRAPER_REGISTRY. Missing the registration silently drops Makan."""
    assert "makan" in SCRAPER_REGISTRY
    assert SCRAPER_REGISTRY["makan"] is MakanScraper


def test_makan_scraper_basic_attrs() -> None:
    """Source slug, language, and base domain must match expectations
    used by the tier registry and the merger's source-priority weights."""
    assert MakanScraper.source_name == "makan"
    assert MakanScraper.language == "ar"
    assert MakanScraper.base_domain == "www.makan.org.il"


def test_makan_in_tier_registry() -> None:
    """Makan must appear in the Arabic-language Tier 2 bucket so
    cross-tier corroboration credit fires correctly."""
    from crime_pipeline.scrapers.tier_registry import (
        DOMAIN_TO_TIER, DOMAIN_TO_PUBLISHER, TIER_2_DOMAINS,
    )
    assert "makan.org.il" in TIER_2_DOMAINS
    assert DOMAIN_TO_TIER.get("makan.org.il") == 2
    assert DOMAIN_TO_PUBLISHER.get("makan.org.il") == "Makan"


def test_get_scraper_constructs_makan() -> None:
    """The factory must construct a MakanScraper given the slug."""
    scraper = get_scraper("makan")
    assert isinstance(scraper, MakanScraper)


def test_makan_discover_signature() -> None:
    """discover() must match the BaseScraper contract:
    (query, date_from, date_to, max_results)."""
    sig = inspect.signature(MakanScraper.discover)
    params = sig.parameters
    assert "query" in params
    assert "date_from" in params
    assert "date_to" in params
    assert "max_results" in params


def test_makan_discover_uses_google_news_helper() -> None:
    """The discover loop must use the shared _gnews helper with a
    Makan site filter. If this regresses we'd silently start scraping
    something else (or nothing)."""
    src = inspect.getsource(MakanScraper.discover)
    assert "fetch_gnews_window" in src
    assert 'makan.org.il' in src
    # Arabic locale — important: hl=ar / gl=IL / ceid=IL:ar
    assert "hl=\"ar\"" in src or "hl='ar'" in src


def test_makan_fetch_uses_article_content_selector() -> None:
    """The Makan article body lives in ``.article-content`` on the
    standard Kan-platform template. Live probe of a real article
    (Timour Atallah's case) confirmed this is the only selector that
    yields a > 200-char body — others either miss or pick up nav text."""
    from crime_pipeline.scrapers.makan import _BODY_SELECTORS
    assert ".article-content" in _BODY_SELECTORS
    # Order matters — most specific first
    assert _BODY_SELECTORS[0] == ".article-content"


def test_makan_date_extractor_handles_iso_with_offset() -> None:
    """Makan's JSON-LD datePublished includes a timezone offset
    (observed: ``2026-01-27T12:00:39+03:00``). Coercing this with
    ``datetime.fromisoformat`` must succeed without throwing."""
    from crime_pipeline.scrapers.makan import _extract_published_from_jsonld
    html = '''
    <html><head><script type="application/ld+json">
      {"@context":"https://schema.org","@type":"NewsArticle",
       "headline":"sample",
       "datePublished":"2026-01-27T12:00:39+03:00"}
    </script></head></html>
    '''
    dt = _extract_published_from_jsonld(html)
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 1
    assert dt.day == 27


def test_makan_date_extractor_handles_zulu_suffix() -> None:
    """Defensive: some JSON-LD blocks use the Zulu 'Z' suffix for UTC.
    ``datetime.fromisoformat`` in Python 3.10 doesn't accept 'Z' — the
    helper must normalise it."""
    from crime_pipeline.scrapers.makan import _extract_published_from_jsonld
    html = '''
    <html><head><script type="application/ld+json">
      {"@type":"NewsArticle","datePublished":"2026-01-27T09:00:39Z"}
    </script></head></html>
    '''
    dt = _extract_published_from_jsonld(html)
    assert dt is not None
    assert dt.year == 2026


def test_makan_headline_extractor() -> None:
    """JSON-LD headline must be preferred over og:title / h1 because
    it's the canonical schema field and survives template churn."""
    from crime_pipeline.scrapers.makan import _extract_headline_from_jsonld
    html = '''
    <html><head><script type="application/ld+json">
      {"@type":"NewsArticle","headline":"  مقتل تيمور عطالله  "}
    </script></head></html>
    '''
    h = _extract_headline_from_jsonld(html)
    assert h == "مقتل تيمور عطالله"


def test_makan_run_id_format_in_keyword_mode() -> None:
    """The keyword-mode pair_run_id must include the source so makan
    and arab48 sweeps of the same keyword don't collide on a shared
    run_id (which would silently mix their articles in the SQLite
    checkpoints)."""
    from crime_pipeline import __main__ as cli_module
    src = inspect.getsource(cli_module)
    # The format string for pair_run_id must reference {source}
    assert "kw_{lang}_{source}_{slug}_{year}" in src or "kw_%s_%s_%s_%s" in src


def test_makan_listed_in_arabic_sources_for_sweep() -> None:
    """The keyword-mode plan must dispatch Arabic keywords to BOTH
    arab48 and makan. Without makan in the source list, the new
    scraper is dead code."""
    from crime_pipeline import __main__ as cli_module
    src = inspect.getsource(cli_module)
    # The plan-building branch must list both sources for ar.
    assert '"arab48", "makan"' in src or '"makan", "arab48"' in src or \
           "'arab48', 'makan'" in src or "'makan', 'arab48'" in src
