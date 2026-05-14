"""Targeted 2025 sweep for count-milestone / running-total articles.

Periodic news pieces enumerate the year's homicide victims via running
totals — Arabic ``قتيلا منذ بدء العام`` and Hebrew
``הרוגים מתחילת השנה`` patterns. They're a gap-filler for victims who
only got a single mention in such a rollup rather than standalone news.

For 2026 this added meaningful coverage; running it for 2025 to close
the remaining ~100-case gap (truth = 252, currently at ~152 cases).

Includes kul_alarab as a 3rd Arabic source — it wasn't available when
the 2026 count-milestone sweep ran.
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


_AR_COUNT_KEYWORDS = [
    "قتيلا منذ بدء العام",
    "قتيلا منذ مطلع العام",
    "حصيلة القتلى",
    "نزيف الأرواح",
]
_HE_COUNT_KEYWORDS = [
    "הרוגים מתחילת השנה",
    "מתחילת השנה רצח",
]

# Bumped to include kul_alarab — its API-backed discovery is also a
# good fit for count-milestone phrase searches.
_AR_SOURCES = ["arab48", "makan", "kul_alarab"]
_HE_SOURCES = ["ynet", "walla"]

DATE_FROM = "2025-01-01"
DATE_TO = "2025-12-31"
MAX_PER_SOURCE = 60
MAX_PAGES = 5


async def run_one(settings: Settings, kw: str, source: str, lang: str) -> tuple[str, dict]:
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"count25_{lang}_{source}_{slug}"
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
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ FAILED: {type(e).__name__}: {e}", flush=True)
        return run_id, {}


async def main() -> None:
    settings = Settings()
    plan: list[tuple[str, str, str]] = []
    for kw in _AR_COUNT_KEYWORDS:
        for src in _AR_SOURCES:
            plan.append((kw, src, "ar"))
    for kw in _HE_COUNT_KEYWORDS:
        for src in _HE_SOURCES:
            plan.append((kw, src, "he"))

    print(f"Count-milestone 2025 sweep: {len(plan)} (keyword, source) pairs", flush=True)
    print(f"  Window: {DATE_FROM} → {DATE_TO}", flush=True)
    print(f"  Cap: {MAX_PER_SOURCE}/source × {MAX_PAGES} pages", flush=True)
    print(flush=True)

    summary: list[dict] = []
    for kw, src, lang in plan:
        run_id, stats = await run_one(settings, kw, src, lang)
        summary.append({
            "kw": kw, "src": src, "run_id": run_id,
            "fetched": stats.get("fetched", 0),
            "triage_kept": stats.get("triage_kept", 0),
            "extracted": stats.get("extracted", 0),
        })

    print()
    print("=" * 72, flush=True)
    print(f"{'KW':<26} {'SRC':<12} {'FETCH':>6} {'TRIAGE+':>8} {'EXT':>6}", flush=True)
    for r in summary:
        print(
            f"  {r['kw'][:24]:<24} {r['src']:<12} {r['fetched']:>6} "
            f"{r['triage_kept']:>8} {r['extracted']:>6}",
            flush=True,
        )
    print()
    print("Sweep complete. Now run:", flush=True)
    print(
        f"  python -m crime_pipeline --build-canonical "
        f"--date-from {DATE_FROM} --date-to {DATE_TO} --no-narrate",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
