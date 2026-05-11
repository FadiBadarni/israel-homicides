"""Regression tests for run_id scoping in repository getters.

The bug Sonnet found in the discover phase: ``get_all_extractions(session)``
returned every extraction in the shared SQLite DB. In a multi-city
backfill (e.g. ``--cities arraba,sakhnin,umm-al-fahm``), running 30
sequential pipelines means the dedup stage in the LATER runs would see
extractions from EARLIER cities — silently cross-contaminating the
blocking and merging across unrelated incidents.

Fix: ``RawArticle.pipeline_run_id`` column + optional ``pipeline_run_id``
parameter on ``get_articles_by_status`` and ``get_all_extractions``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from crime_pipeline.models import Base
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.repository import (
    get_all_extractions,
    get_articles_by_status,
    save_article,
    save_extraction,
)


@pytest.fixture
def session(tmp_path):
    """Fresh in-DB SQLAlchemy session per test."""
    db_path = tmp_path / "test.db"
    engine = db_module.get_engine(str(db_path))
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.close()


def _make_article(session, run_id: str, url_suffix: str) -> str:
    """Helper: insert a successfully-fetched article tagged to a run."""
    article = save_article(session, {
        "source": "arab48",
        "url": f"https://example.com/{run_id}/{url_suffix}",
        "language": "ar",
        "raw_html": "<html></html>",
        "article_text": "body",
        "fetch_status": "success",
        "pipeline_run_id": run_id,
    })
    session.commit()
    return article.id


def _make_extraction(session, article_id: str) -> None:
    save_extraction(session, article_id, {
        "extracted_json": {"victim_name": "X"},
        "validation_status": "valid",
        "llm_model": "test",
    })
    session.commit()


# ---------------------------------------------------------------------------
# get_articles_by_status — run scoping
# ---------------------------------------------------------------------------

def test_get_articles_returns_all_when_no_run_id_filter(session) -> None:
    """Backward-compat: omitting pipeline_run_id returns every article."""
    _make_article(session, "arraba_2026", "a")
    _make_article(session, "sakhnin_2026", "b")
    _make_article(session, "umm_al_fahm_2026", "c")

    rows = get_articles_by_status(session, "success")
    assert len(rows) == 3


def test_get_articles_filters_by_run_id_when_given(session) -> None:
    """The bug fix: filter scopes to one run."""
    _make_article(session, "arraba_2026", "a")
    _make_article(session, "arraba_2026", "b")
    _make_article(session, "sakhnin_2026", "c")
    _make_article(session, "umm_al_fahm_2026", "d")

    rows = get_articles_by_status(session, "success", pipeline_run_id="arraba_2026")
    assert len(rows) == 2
    assert all(r.pipeline_run_id == "arraba_2026" for r in rows)


def test_get_articles_returns_empty_for_unknown_run(session) -> None:
    _make_article(session, "arraba_2026", "a")
    rows = get_articles_by_status(session, "success", pipeline_run_id="nonexistent")
    assert rows == []


# ---------------------------------------------------------------------------
# get_all_extractions — run scoping via JOIN on RawArticle
# ---------------------------------------------------------------------------

def test_get_all_extractions_returns_all_when_no_run_id_filter(session) -> None:
    """Backward-compat."""
    a1 = _make_article(session, "arraba_2026", "a")
    a2 = _make_article(session, "sakhnin_2026", "b")
    _make_extraction(session, a1)
    _make_extraction(session, a2)

    rows = get_all_extractions(session)
    assert len(rows) == 2


def test_get_all_extractions_filters_via_join_on_article_run_id(session) -> None:
    """The fix: JOIN to RawArticle filters extractions by their article's run."""
    a1 = _make_article(session, "arraba_2026", "a")
    a2 = _make_article(session, "arraba_2026", "b")
    a3 = _make_article(session, "sakhnin_2026", "c")
    _make_extraction(session, a1)
    _make_extraction(session, a2)
    _make_extraction(session, a3)

    rows = get_all_extractions(session, pipeline_run_id="arraba_2026")
    assert len(rows) == 2
    assert all(r.article_id in {a1, a2} for r in rows)


def test_legacy_articles_without_run_id_are_excluded_when_filter_active(session) -> None:
    """Articles inserted before this column existed have NULL run_id.
    A run_id filter must NOT match NULL — those are 'untagged legacy data'."""
    legacy = _make_article(session, None, "legacy")  # type: ignore[arg-type]
    _make_article(session, "arraba_2026", "current")

    # Filter by current run — legacy row excluded
    rows = get_articles_by_status(
        session, "success", pipeline_run_id="arraba_2026"
    )
    assert len(rows) == 1
    assert rows[0].pipeline_run_id == "arraba_2026"
