"""Regression tests for ``clamp_dates_to_published_year`` after the
Wafa Abahara bug.

The bug: a 2026 sentencing article reported on a 2020 murder. The LLM
correctly extracted ``incident_date=2020-11-16``. But sanity_pass's
clamp logic ("if year off by ≥ 2, swap") rewrote it to 2026-11-16 —
silently turning an old case into a fake current-year homicide.

Fix: clamp future dates and 1-2 year backward typos only. Keep historical
references (3+ years before publication) as-is.
"""
from __future__ import annotations

from crime_pipeline.enrichment.sanity_pass import clamp_dates_to_published_year


def _case(incident_date: str | None = None, **kw):
    """Tiny case dict with a single source for published_year derivation."""
    src = {
        "url": "x", "discovery_source": "arab48",
        "actual_publisher": "Arab48", "source_name": "arab48",
        "language": "ar", "published_at": kw.pop("published_at", "2026-01-20T00:00:00"),
        "confidence_score": 0.7,
    }
    return {
        "victim_name": kw.pop("victim_name", "X"),
        "incident_date": incident_date,
        "death_date": kw.pop("death_date", None),
        "sources": [src],
        "flags": [],
        "conflicts": {},
    }


# ---------------------------------------------------------------------------
# Historical references — KEEP unchanged (the Wafa Abahara fix)
# ---------------------------------------------------------------------------

def test_keeps_2020_date_in_2026_published_article() -> None:
    """Wafa Abahara scenario: sentencing article mentions 2020 murder."""
    c = _case(incident_date="2020-11-16")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2020-11-16"
    assert "date_year_corrected" not in out["flags"]


def test_keeps_2015_date_in_2026_published_article() -> None:
    """Older anniversary references stay as-is."""
    c = _case(incident_date="2015-03-30")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2015-03-30"


def test_keeps_2023_date_in_2026_published_article() -> None:
    """Boundary: 3-year-old reference is kept (this is the Wafa case
    if you take her death year as 2023 instead of 2020 — same logic)."""
    c = _case(incident_date="2023-04-15")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2023-04-15"


# ---------------------------------------------------------------------------
# Genuine LLM typos — STILL corrected (1-2 year backward)
# ---------------------------------------------------------------------------

def test_clamps_one_year_off_typo() -> None:
    """LLM defaulted to last year despite the prompt saying use pub date."""
    c = _case(incident_date="2025-03-15")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2026-03-15"
    assert "date_year_corrected" in out["flags"]


def test_clamps_two_year_off_typo() -> None:
    c = _case(incident_date="2024-06-10")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2026-06-10"
    assert "date_year_corrected" in out["flags"]


# ---------------------------------------------------------------------------
# Future dates — ALWAYS clamp (impossible)
# ---------------------------------------------------------------------------

def test_clamps_future_date() -> None:
    """Articles can't report on events that haven't happened yet."""
    c = _case(incident_date="2027-05-01")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2026-05-01"
    assert "date_year_corrected" in out["flags"]


def test_clamps_far_future_date() -> None:
    c = _case(incident_date="2030-12-25")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2026-12-25"


# ---------------------------------------------------------------------------
# Same-year date — no-op
# ---------------------------------------------------------------------------

def test_same_year_unchanged() -> None:
    c = _case(incident_date="2026-03-20")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2026-03-20"
    assert "date_year_corrected" not in out["flags"]


# ---------------------------------------------------------------------------
# death_date follows the same rules
# ---------------------------------------------------------------------------

def test_death_date_2020_kept_in_2026_article() -> None:
    c = _case(incident_date="2020-11-16", death_date="2020-11-16")
    out = clamp_dates_to_published_year(c)
    assert out["incident_date"] == "2020-11-16"
    assert out["death_date"] == "2020-11-16"
