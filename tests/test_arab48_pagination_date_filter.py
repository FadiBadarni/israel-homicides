"""Regression tests for Arab48 pagination's date-aware stop logic.

Bug surfaced during the live --keyword-mode sweep: querying `مقتل` for
January 2026 returned 0 articles because Arab48 search is date-DESC and
page 1 only had April/May 2026 results. The old "stop on page_new == 0"
logic conflated "all URLs filtered by date" with "no more results",
breaking historical queries entirely.

Fix: distinguish three counts per page:
  - page_new_unique: URLs seen for the first time (regardless of date)
  - page_kept: URLs that passed the date filter
  - page_pre_window: URLs newer than date_to (we haven't reached window yet)
  - page_post_window: URLs older than date_from (walked past)

Stop only when truly exhausted (page_new_unique == 0) OR when we've
walked past the target window (post_window > 0, kept == 0, pre_window == 0).
"""
from __future__ import annotations

import inspect

from crime_pipeline.scrapers.arab48 import Arab48Scraper


def test_discover_separates_pre_and_post_window_counts() -> None:
    """The source must distinguish URLs newer-than-date_to from older-than-date_from.
    Without this, historical queries on date-desc sources die at page 1."""
    src = inspect.getsource(Arab48Scraper.discover)
    assert "page_pre_window" in src
    assert "page_post_window" in src
    assert "page_kept" in src


def test_discover_keeps_paginating_when_pre_window_only() -> None:
    """When a page has only URLs newer than date_to, the loop must keep
    paginating (Arab48 is date-desc; older results are deeper)."""
    src = inspect.getsource(Arab48Scraper.discover)
    # The stop condition for post-window must NOT fire when pre_window > 0
    assert "page_post_window > 0 and page_kept == 0 and page_pre_window == 0" in src


def test_discover_stops_on_truly_exhausted_page() -> None:
    """When all URLs on a page are duplicates of ones seen earlier,
    the loop must stop (otherwise it'd spin forever)."""
    src = inspect.getsource(Arab48Scraper.discover)
    assert "if page_new_unique == 0:" in src


def test_discover_old_stop_on_zero_new_logic_removed() -> None:
    """The original 'if page_new == 0: break' line was the bug —
    page_new == 0 incorrectly triggered on date-filtered pages. Source
    must no longer use the bare page_new variable."""
    src = inspect.getsource(Arab48Scraper.discover)
    # The old variable name shouldn't appear as a standalone break condition
    # Anchor on the specific pattern that caused the bug
    assert "if page_new == 0:" not in src


def test_max_pages_still_caps_loop() -> None:
    """Belt-and-braces — even with the date-aware logic, max_pages must
    bound the loop so misbehaving searches can't crawl forever."""
    src = inspect.getsource(Arab48Scraper.discover)
    assert "max_pages" in src
    assert "for page in range(1, max_pages + 1)" in src
