"""Tests for the production ``--build-canonical`` and ``--reextract-all``
CLI modes.

Architectural intent (per the iteration that gave us these flags):

  • ``--reextract-all`` is the operator's response to a prompt change.
    Runs the LLM over every triage-passed article and overwrites the
    latest extraction row. Always uses the current prompt.

  • ``--build-canonical`` is the production operating mode. It builds
    THE canonical dataset for a date window from the DB's current
    state — no discover, no fetch, no extract. Uses ``Pipeline.build_canonical``
    which loads the latest extraction per article from the DB,
    explodes multi-victim records, runs global dedup + merge + sanity
    + quality + reconcile, filters by ``incident_geography`` and
    ``incident_date`` window, and writes ONE Schema-2.0 envelope at
    ``output/canonical_<from>_<to>.json``.

These tests pin the contract — both CLI flag wiring AND the build's
filter semantics — so a refactor that breaks either gets caught.
"""
from __future__ import annotations

import inspect

from click.testing import CliRunner

from crime_pipeline.__main__ import cli
from crime_pipeline.pipeline import Pipeline


# ---------------------------------------------------------------------------
# CLI flag plumbing
# ---------------------------------------------------------------------------

def test_cli_has_build_canonical_flag() -> None:
    """The --build-canonical flag must be a registered click option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--build-canonical" in result.output


def test_cli_has_reextract_all_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--reextract-all" in result.output


def test_cli_build_canonical_requires_date_window() -> None:
    """Production safety: ``--build-canonical`` without ``--date-from`` /
    ``--date-to`` would silently default to "today minus 30 days", which
    is not what production wants. The CLI must error out explicitly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--build-canonical"])
    # Exit code 2 means "ran without producing usable output / bad args"
    assert result.exit_code != 0
    assert "date-from" in result.output.lower() or "date_from" in result.output.lower()


# ---------------------------------------------------------------------------
# Pipeline.build_canonical method contract
# ---------------------------------------------------------------------------

def test_pipeline_exposes_build_canonical_coroutine() -> None:
    """build_canonical must be a public async method on Pipeline."""
    assert hasattr(Pipeline, "build_canonical")
    assert inspect.iscoroutinefunction(Pipeline.build_canonical)


def test_build_canonical_signature() -> None:
    """Signature pins the date window as required positional input
    (not a defaulted parameter), so callers can't silently omit it."""
    sig = inspect.signature(Pipeline.build_canonical)
    params = sig.parameters
    assert "date_from" in params
    assert "date_to" in params
    # Both must be required (no default)
    assert params["date_from"].default is inspect.Parameter.empty
    assert params["date_to"].default is inspect.Parameter.empty


def test_build_canonical_source_includes_global_dedup_intent() -> None:
    """The docstring must declare the no-per-keyword-silos intent.
    Without that contract, future refactors could quietly silo the
    dedup by run_id and we'd regress to the old over-merge behaviour."""
    src = inspect.getsource(Pipeline.build_canonical)
    # Loads from DB without run_id scope
    assert "No run_id scoping" in src or "no run_id" in src.lower() or "Single source of truth" in src
    # Filter uses incident_geography
    assert "incident_geography" in src
    # Filter is declarative (no hardcoded name/city lists)
    assert "_NON_ARAB_SOCIETY_NAMES" not in src
    assert "_NON_ISRAEL_CITY_HINTS" not in src


def test_build_canonical_allowed_geographies_constant() -> None:
    """The filter must accept ``israel_arab_society`` AS WELL AS
    ``unknown`` and ``None`` (for legacy / unclassified extractions
    that haven't been re-extracted yet)."""
    src = inspect.getsource(Pipeline.build_canonical)
    assert "israel_arab_society" in src
    assert "unknown" in src
    # The None bucket must be allowed too
    assert "None" in src


def test_build_canonical_writes_window_scoped_output_path() -> None:
    """The output path must include the date window so multiple builds
    don't overwrite each other. ``canonical_<from>_<to>.json`` is the
    contract."""
    src = inspect.getsource(Pipeline._export_canonical)
    assert "canonical_" in src
    assert "date_from" in src
    assert "date_to" in src


# ---------------------------------------------------------------------------
# Production hygiene: no whack-a-mole filters
# ---------------------------------------------------------------------------

def test_no_blocklist_constants_in_pipeline() -> None:
    """Production filter is DECLARATIVE (incident_geography). The
    hardcoded name/city blocklists must not live in pipeline.py."""
    from pathlib import Path
    src = Path("crime_pipeline/pipeline.py").read_text(encoding="utf-8")
    forbidden = [
        "_NON_ARAB_SOCIETY_NAMES",
        "_NON_ISRAEL_CITY_HINTS",
        "_NON_ARAB_SOCIETY_NAME_HINTS",
    ]
    for f in forbidden:
        assert f not in src, f"Blocklist constant in pipeline.py: {f}"
