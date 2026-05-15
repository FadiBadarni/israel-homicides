"""2021 supplemental sweep: pull beyond what arab48 funnel surfaced.

The arab48 funnel landed 47 cases for 2021 (37% of NGO ref 126). The
funnel only used arab48 as the source; six other registered scrapers
weren't exercised for this year. This script runs:

  * kul-alarab — best for historical coverage (API back to 2011)
  * makan      — Google News RSS, may have some 2021 articles
  * ynet       — GNews RSS, expect sparse (4-year retention decay)
  * walla      — same

Each (keyword, source) pair runs as a normal Pipeline.run() invocation.
Articles already in the DB are upserted (no re-fetch, no re-extract).
After this completes, run --build-canonical for 2021.

Expected: +10-30 NEW cases. Cost ~$0.50-1.50.
"""
from __future__ import annotations

import asyncio
import hashlib
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
_AR_KEYWORDS = ["قتل", "مقتل", "جريمة قتل", "إطلاق نار"]
_HE_SOURCES = ["ynet", "walla"]
_AR_SOURCES = ["makan", "kul_alarab"]

DATE_FROM = "2021-01-01"
DATE_TO = "2021-12-31"
MAX_PER_SOURCE = 80
MAX_PAGES = 5


async def run_one(settings: Settings, kw: str, source: str, lang: str) -> tuple[str, dict]:
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"sup21_{lang}_{source}_{slug}"
    pipeline = Pipeline(settings, run_id=run_id, strict_date=False, run_narration=False)
    print(f"▶ {kw!r} → {source} (run_id={run_id})", flush=True)
    try:
        stats = await pipeline.run(
            query=kw, sources=[source],
            date_from=DATE_FROM, date_to=DATE_TO,
            max_per_source=MAX_PER_SOURCE, max_pages=MAX_PAGES,
            stages={
                "discover", "fetch", "triage", "extract",
                "dedup", "merge", "sanity", "quality", "reconcile",
            },
        )
        return run_id, stats
    except Exception as e:
        print(f"  ✗ FAILED: {type(e).__name__}: {e}", flush=True)
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

    print(f"2021 supplemental sweep: {len(plan)} (keyword, source) pairs", flush=True)
    print(f"  Window: {DATE_FROM} → {DATE_TO}", flush=True)
    print(f"  Cap: {MAX_PER_SOURCE}/src × {MAX_PAGES} pages\n", flush=True)

    summary = []
    for kw, source, lang in plan:
        run_id, stats = await run_one(settings, kw, source, lang)
        summary.append((kw, source, stats.get("fetched", 0), stats.get("triage_kept", 0), stats.get("extracted", 0)))

    print("\n" + "=" * 72, flush=True)
    print(f"{'KW':<14} {'SRC':<14} {'FETCH':>6} {'TRIAGE+':>8} {'EXT':>6}", flush=True)
    for kw, src, f, t, e in summary:
        print(f"  {kw[:12]:<12} {src:<14} {f:>6} {t:>8} {e:>6}", flush=True)

    print("\nNow run:", flush=True)
    print(f"  python -m crime_pipeline --build-canonical --date-from {DATE_FROM} --date-to {DATE_TO} --no-narrate --cosine-threshold 0.92", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
