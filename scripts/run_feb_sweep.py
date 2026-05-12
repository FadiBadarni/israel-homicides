"""Blind Feb 1-16, 2026 sweep across all 4 sources.

Goal: find every Arab-society homicide case the pipeline can surface
between Feb 1 and Feb 16, 2026, then present the deduped union so the
operator can validate against the known truth (24 cases user-reported).

Why this is a script, not `--keyword-mode`:
- The keyword-mode CLI flag generates run_ids like `kw_he_ynet_<slug>_2026`
  which would clash with the January data we already have in
  data/pipeline.db. Using explicit Feb run_ids keeps the windows
  cleanly separable and avoids mutating January's pipeline_run_id
  tags via the URL-upsert path.
- Each (keyword, source) gets its own output JSON so the union step
  can dedupe cleanly.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# CHDIR to project root so the default `data/pipeline.db` relative path
# resolves correctly regardless of how the background runner invokes us.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.enrichment.reconciler import reconcile_cases
from crime_pipeline.pipeline import Pipeline
# init_db is called by Pipeline.__init__ — no need to call it separately.

# The 4 Hebrew + 4 Arabic keywords that gave the best Jan recall, plus
# the broader Hebrew set (רצח / ירי had the highest Walla yield).
_HE_KEYWORDS = ["רצח", "נרצח", "ירי", "דקירה"]
_AR_KEYWORDS = ["مقتل", "جريمة قتل", "إطلاق نار", "طعن"]

# Source compatibility — mirrors __main__.py's _SOURCES_FOR_LANG.
_HE_SOURCES = ["ynet", "walla"]
_AR_SOURCES = ["arab48", "makan"]

DATE_FROM = "2026-02-01"
DATE_TO = "2026-02-16"
MAX_PER_SOURCE = 60


async def run_one(
    settings: Settings,
    kw: str,
    source: str,
    lang: str,
) -> tuple[str, dict]:
    """Run discover→export for one (keyword, source) pair with a Feb-tagged run_id."""
    import hashlib

    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"feb26_{lang}_{source}_{slug}"
    pipeline = Pipeline(settings, run_id=run_id, strict_date=False)
    print(f"▶ keyword={kw!r} → {source} (run_id={run_id})")
    try:
        stats = await pipeline.run(
            query=kw,
            sources=[source],
            date_from=DATE_FROM,
            date_to=DATE_TO,
            max_per_source=MAX_PER_SOURCE,
            max_pages=5,
            stages={
                "discover", "fetch", "triage", "extract",
                "dedup", "merge", "sanity", "quality",
                "reconcile", "export",
            },
        )
        return run_id, stats
    except Exception as e:
        print(f"  ✗ {run_id} FAILED: {type(e).__name__}: {e}")
        return run_id, {}


async def main() -> None:
    settings = Settings()

    plan: list[tuple[str, str, str]] = []
    for kw in _HE_KEYWORDS:
        for src in _HE_SOURCES:
            plan.append((kw, src, "he"))
    for kw in _AR_KEYWORDS:
        for src in _AR_SOURCES:
            plan.append((kw, src, "ar"))

    print(f"Feb 1-16 sweep: {len(plan)} (keyword, source) pairs")
    print(f"  Date range: {DATE_FROM} to {DATE_TO}")
    print()

    summary: list[dict] = []
    for kw, source, lang in plan:
        run_id, stats = await run_one(settings, kw, source, lang)
        summary.append({
            "keyword": kw,
            "source": source,
            "run_id": run_id,
            "extracted": stats.get("extracted", 0),
            "cases_exported": stats.get("cases_exported", 0),
        })

    print()
    print("=" * 72)
    print(f"{'KEYWORD':<20} {'SOURCE':<10} {'EXT':>6} {'CASES':>6}")
    for r in summary:
        print(f"  {r['keyword']:<18} {r['source']:<10} "
              f"{r['extracted']:>6} {r['cases_exported']:>6}")

    # Aggregate union + reconcile
    output_dir = Path("output")
    all_cases: list[dict] = []
    for r in summary:
        path = output_dir / f"{r['run_id']}.json"
        if path.exists():
            env = json.loads(path.read_text(encoding="utf-8"))
            all_cases.extend(env.get("cases", []))

    print()
    print(f"Raw union: {len(all_cases)} cases (across all keyword/source pairs)")

    # Cross-source reconcile (collapses duplicates by Jaro + city + date)
    result = reconcile_cases(all_cases, jaro_threshold=0.85)
    print(f"After cross-source reconcile: {result.cases_after} cases "
          f"({len(result.merged_pairs)} merges)")

    # Dump deduped Feb union to a file for the operator to review
    union_path = output_dir / "feb26_union.json"
    union_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "kind": "crime_pipeline.feb26_union",
                "date_from": DATE_FROM,
                "date_to": DATE_TO,
                "case_count": result.cases_after,
                "cases": result.cases,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Union written to: {union_path}")


if __name__ == "__main__":
    asyncio.run(main())
