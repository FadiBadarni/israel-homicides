"""Generate narrations for canonical cases that don't have one yet.

Reads all canonical_* rows, dedupes by canonical_case_id, and runs
``narrate_cases`` on the ones with empty case_narrative_*. Writes the
generated narratives back to ``canonical_cases.case_json`` for every
row sharing that canonical_case_id (so overlapping windows stay in sync).

The narrator caches by (canonical_case_id, sources_hash, model_version),
so re-running this script is idempotent — already-narrated cases hit the
cache and cost $0.

Cost: ~$0.001 per case (Gemini 2.5-flash, thinking_budget=0,
~3K input + ~600 output × 3 langs). ~$0.40-0.60 for the full backfill.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.enrichment.narrator import narrate_cases
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


CONCURRENCY = 4
BATCH_SIZE = 50  # commit in batches so a mid-run crash doesn't lose work


def _has_narrative(case: dict) -> bool:
    return any(
        case.get(f"case_narrative_{lang}")
        and len(str(case[f"case_narrative_{lang}"]).strip()) > 30
        for lang in ("ar", "he", "en")
    )


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    # 1. Load all canonical_* rows
    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(
            select(CanonicalCase).where(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .order_by(CanonicalCase.updated_at.desc())
        ))

    # 2. Dedupe by canonical_case_id, taking most-recently-updated row
    seen: dict[str, CanonicalCase] = {}
    for r in rows:
        cid = (r.case_json or {}).get("canonical_case_id") or r.id
        if cid not in seen:
            seen[cid] = r
    unique_cases = list(seen.values())

    # 3. Pick the ones that need narration AND are died-outcome
    needs_narration: list[tuple[CanonicalCase, dict]] = []
    skipped_already = 0
    skipped_outcome = 0
    skipped_no_sources = 0
    for r in unique_cases:
        cj = dict(r.case_json or {})
        if cj.get("victim_outcome") != "died":
            skipped_outcome += 1
            continue
        if _has_narrative(cj):
            skipped_already += 1
            continue
        if not (cj.get("sources") or []):
            skipped_no_sources += 1
            continue
        needs_narration.append((r, cj))

    print(f"Total unique cases:          {len(unique_cases)}")
    print(f"  Already have narrative:    {skipped_already}")
    print(f"  Not 'died' (skipped):      {skipped_outcome}")
    print(f"  No sources (skipped):      {skipped_no_sources}")
    print(f"  Will generate for:         {len(needs_narration)}")
    print()
    if not needs_narration:
        print("Nothing to do.")
        return

    # 4. Run narrate_cases. It mutates the dicts in place by populating
    # case_narrative_{ar,he,en}. Uses cache where available.
    case_dicts = [cj for _, cj in needs_narration]
    print(f"Generating narrations (concurrency={CONCURRENCY})...")
    counter = await narrate_cases(
        case_dicts,
        api_key=settings.gemini_api_key,
        session_factory=db_module.SessionLocal,
        model=settings.llm_model,
        concurrency=CONCURRENCY,
    )
    print(f"  counter: {counter}")

    # 5. Map canonical_case_id → mutated case_dict, then write back to ALL
    # rows sharing that ID (overlapping windows).
    cid_to_narrative: dict[str, dict] = {}
    for (row, original), mutated in zip(needs_narration, case_dicts):
        cid = mutated.get("canonical_case_id")
        if not cid: continue
        if any(mutated.get(f"case_narrative_{lang}") for lang in ("ar","he","en")):
            cid_to_narrative[cid] = {
                "ar": mutated.get("case_narrative_ar"),
                "he": mutated.get("case_narrative_he"),
                "en": mutated.get("case_narrative_en"),
            }

    print()
    print(f"Writing back to canonical_cases (matches: {len(cid_to_narrative)} canonical_case_ids)...")
    written = 0
    with db_module.SessionLocal() as sess:
        all_rows = list(sess.scalars(
            select(CanonicalCase).where(CanonicalCase.pipeline_run_id.like("canonical_%"))
        ))
        for row in all_rows:
            cj = row.case_json or {}
            cid = cj.get("canonical_case_id")
            if not cid or cid not in cid_to_narrative: continue
            narr = cid_to_narrative[cid]
            if not any(narr.values()): continue
            updated = dict(cj)
            updated["case_narrative_ar"] = narr["ar"]
            updated["case_narrative_he"] = narr["he"]
            updated["case_narrative_en"] = narr["en"]
            row.case_json = updated
            flag_modified(row, "case_json")
            written += 1
            if written % BATCH_SIZE == 0:
                sess.commit()
                print(f"  committed batch ({written} rows)")
        sess.commit()
    print(f"Done. Wrote {written} rows.")


if __name__ == "__main__":
    asyncio.run(main())
