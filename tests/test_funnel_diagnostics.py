"""Gap 4 — pipeline funnel diagnostic tests.

The funnel reads raw_articles + extracted_records straight from SQLite
and reports per-(run_id, source) counts:

    discovered → fetched → triage_passed → extraction_success
                                         → extraction_failed

Tests use a tmp SQLite DB seeded with known rows so they don't depend
on whatever state the developer's local pipeline.db happens to be in.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from crime_pipeline import diagnostics


def _seed_db(db_path: Path) -> None:
    """Create the minimum schema + seed a deterministic 2-run dataset.

    Schema mirrors the production raw_articles / extracted_records
    tables but only the columns gather_funnel reads. Keeping it inline
    rather than running init_db avoids pulling in the whole SQLAlchemy
    stack for a diagnostic test.
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE raw_articles (
          id TEXT PRIMARY KEY,
          source TEXT,
          fetch_status TEXT,
          triage_status TEXT,
          pipeline_run_id TEXT
        );
        CREATE TABLE extracted_records (
          id TEXT PRIMARY KEY,
          article_id TEXT,
          extraction_status TEXT
        );
        """
    )
    # Run A: ynet, 3 articles. 2 fetched, 2 triaged-pass, 1 ext_success, 1 ext_failed.
    rows = [
        ("a1", "ynet",   "success",      "yes", "run_A"),
        ("a2", "ynet",   "success",      "yes", "run_A"),
        ("a3", "ynet",   "fetch_failed", None,  "run_A"),
    ]
    cur.executemany(
        "INSERT INTO raw_articles VALUES (?,?,?,?,?)", rows
    )
    cur.executemany(
        "INSERT INTO extracted_records VALUES (?,?,?)",
        [("e1", "a1", "success"), ("e2", "a2", "failed")],
    )
    # Run B: arab48, 2 articles. Both fetched, 1 triage-pass (rest dropped),
    # 1 ext_success.
    rows_b = [
        ("b1", "arab48", "success", "yes", "run_B"),
        ("b2", "arab48", "success", "no",  "run_B"),
    ]
    cur.executemany(
        "INSERT INTO raw_articles VALUES (?,?,?,?,?)", rows_b
    )
    cur.executemany(
        "INSERT INTO extracted_records VALUES (?,?,?)",
        [("eb1", "b1", "success")],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def funnel_db(tmp_path, monkeypatch):
    """Patch the diagnostics DB-path resolver to point at a seeded tmp DB."""
    db = tmp_path / "pipeline.db"
    _seed_db(db)
    monkeypatch.setattr(diagnostics, "_resolve_db_path", lambda: db)
    return db


def test_gather_funnel_counts_per_run_and_source(funnel_db) -> None:
    """gather_funnel must aggregate by (pipeline_run_id, source) with
    correct stage counts."""
    rows = diagnostics.gather_funnel("all")
    assert len(rows) == 2  # one row per (run, source)

    by_run = {(r.pipeline_run_id, r.source): r for r in rows}
    a = by_run[("run_A", "ynet")]
    assert a.discovered == 3
    assert a.fetched == 2
    assert a.triage_passed == 2
    assert a.triage_dropped == 0
    assert a.extraction_success == 1
    assert a.extraction_failed == 1

    b = by_run[("run_B", "arab48")]
    assert b.discovered == 2
    assert b.fetched == 2
    assert b.triage_passed == 1
    assert b.triage_dropped == 1
    assert b.extraction_success == 1
    assert b.extraction_failed == 0


def test_gather_funnel_prefix_filter(funnel_db) -> None:
    """A non-'all' filter is interpreted as a prefix LIKE match."""
    rows = diagnostics.gather_funnel("run_A")
    assert len(rows) == 1
    assert rows[0].pipeline_run_id == "run_A"


def test_gather_funnel_empty_when_no_match(funnel_db) -> None:
    rows = diagnostics.gather_funnel("does_not_exist")
    assert rows == []


def test_format_funnel_table_smoke(funnel_db) -> None:
    """Table output must be a string with the run IDs visible."""
    rows = diagnostics.gather_funnel("all")
    out = diagnostics.format_funnel_as_table(rows)
    assert "run_A" in out
    assert "run_B" in out
    # Header columns must be present
    for h in ("RUN_ID", "SOURCE", "DISC", "FETCH", "TRIAGE", "EXT_OK", "EXT_FAIL"):
        assert h in out


def test_format_funnel_jsonl_is_parseable(funnel_db) -> None:
    """JSONL output: one JSON object per line, parseable."""
    rows = diagnostics.gather_funnel("all")
    out = diagnostics.format_funnel_as_jsonl(rows)
    lines = out.splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    # Schema check on first row
    sample = parsed[0]
    for key in (
        "pipeline_run_id", "source", "discovered", "fetched",
        "triage_passed", "extraction_success", "extraction_failed",
    ):
        assert key in sample


def test_funnel_yield_properties() -> None:
    """The yield properties should compute conversion correctly and
    handle zero denominators without crashing."""
    row = diagnostics.FunnelRow(
        pipeline_run_id="x", source="y",
        discovered=10, fetched=8, triage_passed=4, triage_dropped=4,
        extraction_success=3, extraction_failed=1,
    )
    assert row.fetch_yield == 0.8
    assert row.triage_yield == 0.5
    assert row.extract_yield == 0.75

    empty = diagnostics.FunnelRow(
        pipeline_run_id="x", source="y",
        discovered=0, fetched=0, triage_passed=0, triage_dropped=0,
        extraction_success=0, extraction_failed=0,
    )
    assert empty.fetch_yield == 0.0
    assert empty.triage_yield == 0.0
    assert empty.extract_yield == 0.0
