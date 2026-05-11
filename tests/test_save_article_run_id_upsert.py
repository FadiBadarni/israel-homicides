"""Regression test for the Magdi-Shela'ata bug.

Symptom: Magdi's article had been fetched in an earlier run with
``pipeline_run_id=None``. The later ``--cities arraba`` backfill discovered
the same URL, called ``save_article``, which upserted the row but did NOT
update ``pipeline_run_id`` because that field wasn't in the upsert's
``mutable_fields`` set. The new run's resume-mode reads filtered by
run_id and the article disappeared.

Fix: ``pipeline_run_id`` is now in ``mutable_fields`` — last-run-to-touch
wins, so re-fetching an article under a new run_id re-tags it.
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.repository import (
    get_articles_by_status,
    save_article,
)


def _session(tmp_path):
    engine = db_module.get_engine(str(tmp_path / "test.db"))
    return sessionmaker(bind=engine)()


def _save_with_run(session, run_id, url):
    return save_article(session, {
        "source": "arab48",
        "url": url,
        "language": "ar",
        "raw_html": "<html></html>",
        "article_text": "body",
        "fetch_status": "success",
        "pipeline_run_id": run_id,
    })


def test_reupserting_article_under_new_run_id_overwrites_old(tmp_path) -> None:
    """Magdi scenario: article first saved with run_id=None, then re-saved
    under a real run_id. The new run_id MUST stick."""
    s = _session(tmp_path)
    url = "https://www.arab48.com/article/magdi"
    _save_with_run(s, None, url)
    s.commit()

    # Re-fetch the same URL under a new run_id
    _save_with_run(s, "arraba_2026_arab48", url)
    s.commit()

    rows = get_articles_by_status(
        s, "success", pipeline_run_id="arraba_2026_arab48"
    )
    assert len(rows) == 1
    assert rows[0].pipeline_run_id == "arraba_2026_arab48"


def test_reupserting_swaps_run_ids(tmp_path) -> None:
    """Different runs touching the same article: the latest run owns it.

    Acceptable semantics: ``pipeline_run_id`` records the most recent run
    that successfully fetched the article, not the first. The alternative
    (preserve original) would silently exclude shared articles from
    later runs' resume-mode reads."""
    s = _session(tmp_path)
    url = "https://www.arab48.com/article/shared"

    _save_with_run(s, "run_A", url)
    s.commit()
    _save_with_run(s, "run_B", url)
    s.commit()

    # run_A no longer sees the article
    assert get_articles_by_status(s, "success", pipeline_run_id="run_A") == []
    # run_B does
    rows_b = get_articles_by_status(s, "success", pipeline_run_id="run_B")
    assert len(rows_b) == 1
    assert rows_b[0].url == url
