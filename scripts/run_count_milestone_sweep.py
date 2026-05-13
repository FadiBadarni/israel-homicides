"""Targeted sweep for Arab48 count-milestone articles.

Arab48 publishes "X قتيلا منذ بدء العام" headlines whenever the running
counter increments. Those articles enumerate the new victims and are
goldmines for the cases that single-keyword sweeps miss. We didn't catch
them in the YTD sweep because the keyword filters were just murder verbs,
not count-update phrases.

This script runs Arabic count-update phrases against Arab48 + Makan
+ Walla + Ynet (the Hebrew "13 הרוגים מתחילת השנה" pattern exists too).
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


# Arabic count-update phrases — Arab48 / Makan style.
_AR_COUNT_KEYWORDS = [
    "قتيلا منذ بدء العام",
    "قتيلا منذ مطلع العام",
    "حصيلة القتلى",
    "نزيف الأرواح",
]
# Hebrew count-update phrases — Ynet/Walla equivalents.
_HE_COUNT_KEYWORDS = [
    "הרוגים מתחילת השנה",
    "מתחילת השנה רצח",
]

_AR_SOURCES = ["arab48", "makan"]
_HE_SOURCES = ["ynet", "walla"]

DATE_FROM = "2026-01-01"
DATE_TO = "2026-05-13"
MAX_PER_SOURCE = 60
MAX_PAGES = 5


async def run_one(settings: Settings, kw: str, source: str, lang: str) -> tuple[str, dict]:
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"count26_{lang}_{source}_{slug}"
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

    print(f"Count-milestone sweep: {len(plan)} (keyword, source) pairs", flush=True)
    print(f"  Window: {DATE_FROM} → {DATE_TO}", flush=True)
    print(f"  Cap: {MAX_PER_SOURCE}/source × {MAX_PAGES} pages", flush=True)

    for kw, src, lang in plan:
        run_id, stats = await run_one(settings, kw, src, lang)
        print(
            f"  ✓ {run_id} fetched={stats.get('fetched',0)} "
            f"extracted={stats.get('extracted',0)}",
            flush=True,
        )

    print()
    print("Done. Now run:", flush=True)
    print(f"  python -m crime_pipeline --build-canonical --date-from {DATE_FROM} --date-to {DATE_TO}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
