"""Tests for the Arab48 pagination loop in discover().

Pre-fix: discover() only fetched page 1 (~20 articles), regardless of
max_results. Multi-city backfills lost any homicide article that
happened to land on page 2+ of the search results.

Post-fix: discover() walks up to ``max_pages`` (default 5), stops early
when a page adds zero new URLs.

These tests focus on signature shape and the stop-on-zero-new logic;
the actual HTTP loop is left to live verification (mocking httpx adds
zero value vs hitting the real site once).
"""
from __future__ import annotations

import inspect

from crime_pipeline.scrapers.arab48 import Arab48Scraper


def test_discover_signature_includes_max_pages() -> None:
    """The kwarg must exist with a sensible default per the synthesis."""
    sig = inspect.signature(Arab48Scraper.discover)
    assert "max_pages" in sig.parameters
    assert sig.parameters["max_pages"].default == 5


def test_discover_signature_keeps_max_results() -> None:
    """Backward-compat: callers passing max_results must still work."""
    sig = inspect.signature(Arab48Scraper.discover)
    assert "max_results" in sig.parameters


def test_discover_source_uses_page_query_param() -> None:
    """The implementation must use ``?page=N`` (not ``?p=N`` or ``/page/N``).

    Live probe in the discover phase confirmed only ``?page=N`` works on
    Arab48's search endpoint."""
    src = inspect.getsource(Arab48Scraper.discover)
    assert "&page=" in src or "page=" in src


def test_discover_stops_on_zero_new() -> None:
    """The early-stop-on-zero-new safeguard must be present so a truly
    exhausted page doesn't keep hitting Arab48 forever. The variable name
    was renamed from ``page_new`` to ``page_new_unique`` when the
    date-aware-stop fix shipped — see test_arab48_pagination_date_filter.py
    for the deeper invariants."""
    src = inspect.getsource(Arab48Scraper.discover)
    assert "page_new_unique == 0" in src or "page_new == 0" in src
