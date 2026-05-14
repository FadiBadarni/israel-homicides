"""Full-year 2025 backfill: Jan 1 → Dec 31, 2025.

Mirrors ``run_2026_ytd_sweep.py`` but for the full 2025 calendar year.
Truth number for 2025 Arab-society homicides is ~252 victims; this sweep
discovers + triages + extracts the source articles. After it completes,
run::

    python -m crime_pipeline --build-canonical \\
        --date-from 2025-01-01 --date-to 2025-12-31

to materialize the cases into the canonical_cases table; the UI's DB-
backed snapshot will pick them up automatically (the 2026 + Dec 2025
windows already coexist).

Caps bumped vs the 2026 sweep (200/source × 8 pages) because the window
is 2.8x longer and historical coverage is more spread out. Expected
runtime: 4-6 hours. Expected API cost: $1-3.
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


# Same keywords as the 2026 sweep — Arabic + Hebrew murder verbs.
_HE_KEYWORDS = ["רצח", "נרצח", "ירי", "דקירה"]
_AR_KEYWORDS = ["مقتل", "جريمة قتل", "إطلاق نار", "طعن"]
# Same source set. Arab48 will likely under-deliver because its site
# search doesn't honor date ranges well — verified empirically when we
# tried it for Dec 2025. We include it anyway in case some 2025 articles
# surface via the recent-results path.
_HE_SOURCES = ["ynet", "walla"]
_AR_SOURCES = ["arab48", "makan"]

DATE_FROM = "2025-01-01"
DATE_TO = "2025-12-31"
# 365-day window vs the 130-day 2026 sweep => bump caps to capture more
# historical articles before pagination cuts us off.
MAX_PER_SOURCE = 200
MAX_PAGES = 8


async def run_one(settings: Settings, kw: str, source: str, lang: str) -> tuple[str, dict]:
    import hashlib
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"ytd25_{lang}_{source}_{slug}"
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

    print(f"YTD 2025 sweep: {len(plan)} (keyword, source) pairs", flush=True)
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
    print(
        f"  python -m crime_pipeline --build-canonical "
        f"--date-from {DATE_FROM} --date-to {DATE_TO}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
