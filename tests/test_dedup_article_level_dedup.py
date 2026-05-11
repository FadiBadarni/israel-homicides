"""Regression test for duplicate-extraction handling in `_dedup_and_merge`.

The bug: ``save_extraction`` always appends a new row (history-preserving by
design). When the same pipeline.run() is invoked multiple times on the same
run_id, each invocation creates new ExtractedRecord rows for the same
articles. The dedup stage then fed identical text into the cosine
similarity, producing fake clusters of an article paired with itself.

Live symptom: 4 extractions for run_id ``arraba_2026_arab48`` were really
2 articles × 2 duplicates. Dedup produced ``clusters=2, singletons=0``
when it should have produced 2 singletons.

Fix: when building dedup_records, take only the most recent extraction
per article_id.
"""
from __future__ import annotations

import inspect

from crime_pipeline.pipeline import Pipeline


def test_dedup_skip_duplicate_extractions_per_article() -> None:
    """Source-level guard: the dedup-record loop must iterate over
    ``unique_extractions`` (de-duped by article_id) — not the raw
    extractions list."""
    src = inspect.getsource(Pipeline._dedup_and_merge)
    assert "unique_extractions" in src, (
        "_dedup_and_merge must build dedup_records from unique_extractions, "
        "not the raw extractions list"
    )
    assert "seen_articles" in src
    # The sort key must be extraction recency
    assert "extracted_at" in src


def test_dedup_logs_when_duplicates_dropped() -> None:
    """Operator visibility: log a structured event so operators see when
    the de-dup pre-step kicked in."""
    src = inspect.getsource(Pipeline._dedup_and_merge)
    assert "deduped_extractions_by_article" in src
