"""Full-year 2026 YTD sweep: Jan 1 → May 13, 2026.

Runs each (keyword, source) pair across the wider window. Articles
already in the DB from prior Jan/Feb sweeps are upserted (no re-fetch),
new articles get triage + extract. After this completes, run
``--build-canonical --date-from 2026-01-01 --date-to 2026-05-13`` to
get the unified canonical dataset.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.pipeline import Pipeline


_HE_KEYWORDS = ["רצח", "נרצח", "ירי", "דקירה"]
_AR_KEYWORDS = ["مقتل", "جريمة قتل", "إطلاق نار", "طعن"]
_HE_SOURCES = ["ynet", "walla"]
_AR_SOURCES = ["arab48", "makan"]

DATE_FROM = "2026-01-01"
DATE_TO = "2026-05-13"
# Wider window than Feb sweep — push the per-source cap up since 4.5
# months will surface more headlines per keyword.
MAX_PER_SOURCE = 200
MAX_PAGES = 10


async def run_one(settings: Settings, kw: str, source: str, lang: str) -> tuple[str, dict]:
    import hashlib
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"ytd26_{lang}_{source}_{slug}"
    pipeline = Pipeline(settings, run_id=run_id, strict_date=False, run_narration=False)
    print(f"▶ {kw!r} → {source} (run_id={run_id})", flush=True)
    try:
        stats = await pipeline.run(
            query=kw,
            sources=[source],
            date_from=DATE_FROM,
            date_to=DATE_TO,
            max_per_source=MAX_PER_SOURCE,
            max_pages=MAX_PAGES,
            stages={
                "discover", "fetch", "triage", "extract",
                "dedup", "merge", "sanity", "quality", "reconcile",
            },
        )
        return run_id, stats
    except Exception as e:
        print(f"  ✗ {run_id} FAILED: {type(e).__name__}: {e}", flush=True)
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

    print(f"YTD 2026 sweep: {len(plan)} (keyword, source) pairs", flush=True)
    print(f"  Date range: {DATE_FROM} to {DATE_TO}", flush=True)
    print(f"  Max per source: {MAX_PER_SOURCE}, max pages: {MAX_PAGES}", flush=True)
    print(flush=True)

    summary: list[dict] = []
    for kw, source, lang in plan:
        run_id, stats = await run_one(settings, kw, source, lang)
        summary.append({
            "kw": kw, "src": source, "run_id": run_id,
            "fetched": stats.get("fetched", 0),
            "extracted": stats.get("extracted", 0),
            "triage_kept": stats.get("triage_kept", 0),
            "triage_dropped": stats.get("triage_dropped", 0),
        })

    print()
    print("=" * 72, flush=True)
    print(f"{'KW':<14} {'SRC':<10} {'FETCH':>6} {'TRIAGE+':>8} {'EXT':>6}", flush=True)
    for r in summary:
        print(
            f"  {r['kw']:<12} {r['src']:<10} {r['fetched']:>6} "
            f"{r['triage_kept']:>8} {r['extracted']:>6}",
            flush=True,
        )

    print()
    print("Sweep complete. Now run:", flush=True)
    print(f"  python -m crime_pipeline --build-canonical --date-from {DATE_FROM} --date-to {DATE_TO}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
