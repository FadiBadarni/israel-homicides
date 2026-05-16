"""One-shot: reattach cached narrations to every canonical_* row.

Walks ``canonical_cases`` for ``pipeline_run_id LIKE 'canonical_%'`` and
calls ``attach_cached_narrations`` on each row's ``case_json``. Writes
back any row whose ``case_narrative_*`` fields changed.

Free (no API calls). Idempotent. Reads from the ``case_narratives``
cache table populated by prior ``narrate_cases`` runs.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.enrichment.narrator import attach_cached_narrations
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(
            select(CanonicalCase).where(
                CanonicalCase.pipeline_run_id.like("canonical_%")
            )
        ))
        total = len(rows)
        print(f"scanning {total} canonical_* rows...")

        changed = 0
        already = 0
        no_cache = 0

        for row in rows:
            case = dict(row.case_json or {})
            before = (
                case.get("case_narrative_ar"),
                case.get("case_narrative_he"),
                case.get("case_narrative_en"),
            )
            # The attach helper expects a list — single-element call is fine.
            attached = attach_cached_narrations([case], db_module.SessionLocal)
            after = (
                case.get("case_narrative_ar"),
                case.get("case_narrative_he"),
                case.get("case_narrative_en"),
            )
            if attached and after != before:
                row.case_json = case
                flag_modified(row, "case_json")
                changed += 1
            elif any(before):
                already += 1
            else:
                no_cache += 1

        sess.commit()

    print()
    print(f"=== Summary ===")
    print(f"  rows scanned:        {total}")
    print(f"  newly attached:      {changed}")
    print(f"  already had it:      {already}")
    print(f"  no cache available:  {no_cache}")
    if changed:
        print()
        print("Triggering API reload by touching ui/api/main.py would refresh the UI.")


if __name__ == "__main__":
    main()
