"""Tests for E+D discovery query expansion in pipeline._discover().

Covers:
- _generate_descriptor_variants() helper (unit tests, no network)
- Pipeline._discover() two-pass behaviour (mock scraper, no network)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from crime_pipeline.pipeline import _generate_descriptor_variants, _DISCOVER_SECOND_PASS_THRESHOLD
from crime_pipeline.scrapers.base import DiscoveredUrl


# ---------------------------------------------------------------------------
# _generate_descriptor_variants — unit tests
# ---------------------------------------------------------------------------

def test_hebrew_query_generates_hebrew_variants():
    variants = _generate_descriptor_variants("עראבה", "2026-01-01")
    assert variants == ["עראבה רצח 2026", "עראבה ירי 2026"]


def test_arabic_query_generates_arabic_variants():
    variants = _generate_descriptor_variants("بكر ياسين", "2026-01-01")
    assert variants == ["بكر ياسين مقتل 2026", "بكر ياسين قتل 2026"]


def test_query_with_crime_term_returns_empty():
    assert _generate_descriptor_variants("עראבה רצח", "2026-01-01") == []
    assert _generate_descriptor_variants("עראבה ירי", "2026-01-01") == []
    assert _generate_descriptor_variants("مقتل في عرابة", "2026-01-01") == []


def test_year_not_duplicated_when_already_in_query():
    variants = _generate_descriptor_variants("עראבה 2026", "2026-01-01")
    for v in variants:
        assert v.count("2026") == 1, f"Year duplicated in: {v!r}"


def test_no_date_from_omits_year():
    variants = _generate_descriptor_variants("עראבה", "")
    assert all("2026" not in v for v in variants)
    assert variants == ["עראבה רצח", "עראבה ירי"]


def test_latin_query_generates_hebrew_variants():
    variants = _generate_descriptor_variants("Arraba", "2026-06-01")
    assert variants == ["Arraba רצח 2026", "Arraba ירי 2026"]


def test_threshold_constant_is_positive_int():
    assert isinstance(_DISCOVER_SECOND_PASS_THRESHOLD, int)
    assert _DISCOVER_SECOND_PASS_THRESHOLD > 0


# ---------------------------------------------------------------------------
# Pipeline._discover() — two-pass integration (mock scraper, no network)
# ---------------------------------------------------------------------------

def _make_discovered_url(url: str, source: str = "ynet") -> DiscoveredUrl:
    return DiscoveredUrl(
        url=url,
        source=source,
        language="he",
        title=None,
        published_at=None,
        discovered_at=datetime.now(timezone.utc),
    )


def _make_pipeline():
    """Return a Pipeline with mocked settings (no DB init needed for discover tests)."""
    from crime_pipeline.pipeline import Pipeline
    from crime_pipeline.config import Settings

    settings = MagicMock(spec=Settings)
    settings.request_delay_seconds = 0.0
    settings.robots_txt_respect = False
    settings.jaro_threshold = 0.88
    settings.cosine_threshold = 0.82
    settings.db_path = MagicMock()
    settings.db_path.parent = MagicMock()

    pipeline = MagicMock(spec=Pipeline)
    pipeline.settings = settings
    pipeline.stats = {}
    # Re-bind the real _discover to this mock so we exercise the real code
    pipeline._discover = Pipeline._discover.__get__(pipeline, Pipeline)
    return pipeline


@pytest.mark.asyncio
async def test_second_pass_triggered_when_first_pass_empty():
    """When the first query returns 0 URLs, crime-type variants are tried."""
    called_queries: list[str] = []

    async def mock_discover(q, date_from, date_to, max_results=50, max_pages=5):
        called_queries.append(q)
        if q == "עראבה":
            return []  # sparse first pass
        # second-pass variant returns one article
        return [_make_discovered_url(f"https://www.ynet.co.il/news/article/{hash(q)}")]

    mock_scraper = MagicMock()
    mock_scraper.discover = mock_discover

    pipeline = _make_pipeline()

    with patch("crime_pipeline.pipeline.get_scraper", return_value=mock_scraper):
        result = await pipeline._discover(
            "עראבה", ["ynet"], "2026-01-01", "2026-01-31",
            max_per_source=50, max_pages=5,
        )

    assert "עראבה" in called_queries
    assert any("רצח" in q or "ירי" in q for q in called_queries), (
        "Expected second-pass crime-term variant to be called"
    )
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_second_pass_skipped_when_first_pass_sufficient():
    """When the first query returns >= threshold URLs, no variant queries run."""
    called_queries: list[str] = []

    async def mock_discover(q, date_from, date_to, max_results=50, max_pages=5):
        called_queries.append(q)
        return [
            _make_discovered_url(f"https://www.ynet.co.il/news/article/{i}")
            for i in range(_DISCOVER_SECOND_PASS_THRESHOLD + 2)
        ]

    mock_scraper = MagicMock()
    mock_scraper.discover = mock_discover

    pipeline = _make_pipeline()

    with patch("crime_pipeline.pipeline.get_scraper", return_value=mock_scraper):
        result = await pipeline._discover(
            "עראבה", ["ynet"], "2026-01-01", "2026-01-31",
            max_per_source=50, max_pages=5,
        )

    assert called_queries == ["עראבה"], (
        f"Expected only original query to be called, got: {called_queries}"
    )
    assert len(result) >= _DISCOVER_SECOND_PASS_THRESHOLD


@pytest.mark.asyncio
async def test_second_pass_skipped_when_query_has_crime_term():
    """A query that already contains crime vocabulary triggers no second pass."""
    called_queries: list[str] = []

    async def mock_discover(q, date_from, date_to, max_results=50, max_pages=5):
        called_queries.append(q)
        return []  # always empty — threshold would trigger, but no variants exist

    mock_scraper = MagicMock()
    mock_scraper.discover = mock_discover

    pipeline = _make_pipeline()

    with patch("crime_pipeline.pipeline.get_scraper", return_value=mock_scraper):
        await pipeline._discover(
            "עראבה רצח 2026", ["ynet"], "2026-01-01", "2026-01-31",
            max_per_source=50, max_pages=5,
        )

    assert called_queries == ["עראבה רצח 2026"], (
        "No variant queries should run when original already has crime term"
    )


@pytest.mark.asyncio
async def test_results_deduped_across_queries():
    """URLs returned by both original and variant queries are deduped."""
    shared_url = "https://www.ynet.co.il/news/article/shared"

    async def mock_discover(q, date_from, date_to, max_results=50, max_pages=5):
        return [_make_discovered_url(shared_url)]  # same URL every time

    mock_scraper = MagicMock()
    mock_scraper.discover = mock_discover

    pipeline = _make_pipeline()

    with patch("crime_pipeline.pipeline.get_scraper", return_value=mock_scraper):
        result = await pipeline._discover(
            "עראבה", ["ynet"], "2026-01-01", "2026-01-31",
            max_per_source=50, max_pages=5,
        )

    urls = [u.url for u in result]
    assert urls.count(shared_url) == 1, "Duplicate URL should appear only once"
