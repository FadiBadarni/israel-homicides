"""Regression test for Gap 1 — silent extraction failures.

Pre-fix: Pipeline._extract only wrote `extracted_records` rows when
extraction succeeded. Failures (LLM timeout, schema validation reject,
truncated JSON) were logged but invisible in the DB. Result:
articles that passed triage but failed extraction looked identical to
articles never sent to extract — masking real bugs and breaking the
funnel CLI's drop-off accounting.

Post-fix: on failure we write a sentinel row with extraction_status='failed'
and validation_status='invalid'. The funnel CLI surfaces these as a
distinct EXT_FAIL column.

We test the *shape* of the writeback rather than running a live LLM call
(too brittle, too slow). The pipeline source must contain the failure-row
construction logic adjacent to the warning log; if the literal disappears
the regression is back.
"""
from __future__ import annotations

import inspect

from crime_pipeline.pipeline import Pipeline


def test_extract_writes_failure_row_on_llm_failure() -> None:
    """The _extract method's else-branch (extraction failed) must call
    save_extraction with extraction_status='failed'. Without this, the
    funnel CLI cannot distinguish 'attempted-and-failed' from 'never-run'."""
    src = inspect.getsource(Pipeline._extract)
    # Failure path must write a sentinel row, not just warn.
    assert "extraction_status" in src
    assert "'failed'" in src or '"failed"' in src
    # save_extraction must be called in BOTH the success and the failure
    # branch — the regression was that only success wrote a row.
    assert src.count("save_extraction") >= 2


def test_extract_failure_row_has_validation_status_invalid() -> None:
    """A failed extraction must mark validation as 'invalid' so it
    doesn't accidentally enter downstream stages."""
    src = inspect.getsource(Pipeline._extract)
    # The failure row block sets validation_status='invalid'.
    # Pin the marker so reordering doesn't silently regress.
    assert "validation_status" in src
    assert "'invalid'" in src or '"invalid"' in src
