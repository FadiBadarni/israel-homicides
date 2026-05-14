"""Null out ``raw_articles.raw_html`` for articles with a populated cache.

Run AFTER ``backfill_media_cache.py`` completes. Only zeroes out rows whose
``media_harvest_version`` matches the current ``MEDIA_HARVEST_VERSION`` —
any article that hasn't been cached yet keeps its HTML so future cache
writes still work.

The ``raw_html`` column is declared NOT NULL in the ORM, so this writes
an empty string instead of NULL (SQLite stores both with the same ~0-byte
overhead in TEXT). Reads after the operation get ``""`` instead of the
prior HTML.

After the UPDATE, runs ``VACUUM`` to compact the file. Expected savings:
~335 MB → ~50 MB on the dev DB at the time of this writing.

Usage::

    python scripts/null_raw_html.py [--dry-run] [--skip-vacuum]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import func, select, text, update

from crime_pipeline.config import Settings
from crime_pipeline.media.pipeline import MEDIA_HARVEST_VERSION
from crime_pipeline.models import RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-vacuum", action="store_true", help="Don't VACUUM after")
    args = ap.parse_args()

    settings = Settings()
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    db_path = Path(settings.db_path)
    before_size = db_path.stat().st_size if db_path.exists() else 0
    print(f"DB size before: {before_size / 1024 / 1024:.1f} MB")

    with db_module.SessionLocal() as session:
        # Eligible: has cache at current version, AND raw_html is currently
        # non-empty (avoid double-counting rows we've already nulled).
        eligible_q = (
            select(func.count())
            .select_from(RawArticle)
            .where(RawArticle.media_harvest_version == MEDIA_HARVEST_VERSION)
            .where(RawArticle.raw_html != "")
        )
        eligible = session.execute(eligible_q).scalar() or 0
        total = session.execute(
            select(func.count()).select_from(RawArticle)
        ).scalar() or 0

    print(f"Rows eligible to null: {eligible}/{total}")

    if args.dry_run:
        print("DRY RUN — no changes made.")
        return

    if eligible == 0:
        print("Nothing to do.")
        return

    with db_module.SessionLocal() as session:
        result = session.execute(
            update(RawArticle)
            .where(RawArticle.media_harvest_version == MEDIA_HARVEST_VERSION)
            .where(RawArticle.raw_html != "")
            .values(raw_html="")
        )
        session.commit()
        print(f"Nulled raw_html for {result.rowcount} rows.")

    if args.skip_vacuum:
        print("Skipping VACUUM.")
        return

    print("Running VACUUM (may take a minute)...")
    # VACUUM must run outside a transaction in SQLite. Use a fresh connection
    # in AUTOCOMMIT mode.
    with db_module.SessionLocal() as session:
        conn = session.connection()
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("VACUUM"))

    after_size = db_path.stat().st_size if db_path.exists() else 0
    print(f"DB size after:  {after_size / 1024 / 1024:.1f} MB")
    saved = (before_size - after_size) / 1024 / 1024
    print(f"Saved:          {saved:.1f} MB")


if __name__ == "__main__":
    main()
