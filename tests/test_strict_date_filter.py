"""Tests for the --strict-date post-merge filter.

Drops cases whose extracted incident_date is outside the queried window.
Wafa Abahara (2020 murder reported in 2026 sentencing article) is the
canonical case this filter targets.

Per the synthesis rules:
- Missing incident_date → KEEP + flag `date_filter_unverified` (Sonnet rule)
- Date in window → KEEP
- Date out of window → DROP
"""
from __future__ import annotations

import inspect
import types
from datetime import date

from crime_pipeline.pipeline import Pipeline


def _stub_pipeline_with_window(date_from: str, date_to: str):
    return types.SimpleNamespace(
        strict_date=True,
        _strict_date_window=(date.fromisoformat(date_from), date.fromisoformat(date_to)),
        stats={},
        _matches_strict_date=Pipeline._matches_strict_date,
    )


def _case(incident_date=None, death_date=None, flags=None):
    return types.SimpleNamespace(
        canonical_case_id="test",
        incident_date=incident_date,
        death_date=death_date,
        flags=list(flags or []),
    )


# ---------------------------------------------------------------------------
# In-window — keep
# ---------------------------------------------------------------------------

def test_keeps_date_inside_window() -> None:
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date="2026-03-20")
    keep, reason = p._matches_strict_date(p, case)
    assert keep is True
    assert "in_window" in reason


def test_keeps_boundary_date_from() -> None:
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date="2026-01-01")
    assert p._matches_strict_date(p, case)[0] is True


def test_keeps_boundary_date_to() -> None:
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date="2026-12-31")
    assert p._matches_strict_date(p, case)[0] is True


# ---------------------------------------------------------------------------
# Out of window — drop (the Wafa Abahara scenario)
# ---------------------------------------------------------------------------

def test_drops_wafa_2020_in_2026_run() -> None:
    """The canonical regression: 2020-killed-Wafa reported in a 2026 sentencing
    article must not ship as a 2026 case."""
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date="2020-11-16")
    keep, reason = p._matches_strict_date(p, case)
    assert keep is False
    assert "out_of_window" in reason


def test_drops_future_date() -> None:
    """Anything dated after date_to is also out of window."""
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date="2027-04-01")
    assert p._matches_strict_date(p, case)[0] is False


def test_falls_back_to_death_date_if_incident_date_missing() -> None:
    """A case with only death_date should still get filtered."""
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date=None, death_date="2020-11-16")
    keep, reason = p._matches_strict_date(p, case)
    assert keep is False
    assert "out_of_window" in reason


# ---------------------------------------------------------------------------
# No date extracted — keep + flag (never silently drop)
# ---------------------------------------------------------------------------

def test_keeps_and_flags_when_no_date_at_all() -> None:
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date=None, death_date=None)
    keep, reason = p._matches_strict_date(p, case)
    assert keep is True
    assert "date_filter_unverified" in case.flags


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_window_none_keeps_everything() -> None:
    """Defensive: if strict_date was set but the window failed to parse,
    don't drop anything."""
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    p._strict_date_window = None
    case = _case(incident_date="2020-11-16")
    keep, _ = p._matches_strict_date(p, case)
    assert keep is True


def test_accepts_date_object_in_field() -> None:
    """Pydantic stores incident_date as date, not string. Filter must handle both."""
    p = _stub_pipeline_with_window("2026-01-01", "2026-12-31")
    case = _case(incident_date=date(2020, 11, 16))
    keep, _ = p._matches_strict_date(p, case)
    assert keep is False


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_exposes_strict_date_flag() -> None:
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    assert '"--strict-date"' in src
    sig = inspect.signature(cli_mod.cli.callback)
    assert "strict_date" in sig.parameters


def test_pipeline_constructor_accepts_strict_date() -> None:
    sig = inspect.signature(Pipeline.__init__)
    assert "strict_date" in sig.parameters
    assert sig.parameters["strict_date"].default is False
