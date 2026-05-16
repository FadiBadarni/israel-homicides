"""Per-source-parallel supplemental sweep for any year.

Streams 4 sources concurrently (ynet, walla, makan, kul_alarab) — within
each source, the 4 keywords run sequentially so the source's polite-
crawler delay isn't defeated. Pipeline.run() is async, so asyncio.gather
across the 4 source-streams gives ~4x wall-clock speedup vs sequential.

The MiniLM embedder is now a process-level singleton (see
``crime_pipeline/dedup/embedder.py``), so the 4 concurrent
Deduplicators share one ~500MB model instead of allocating 2GB.

Year is taken from sys.argv[1] (YYYY). Run two of these in parallel
(one per year) for additional 2x speedup at the script level.

Usage:
    python scripts/run_supplemental_sweep_parallel.py 2022
    python scripts/run_supplemental_sweep_parallel.py 2023
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

# (source, language, keyword_list)
SOURCE_STREAMS = [
    ("ynet",       "he", _HE_KEYWORDS),
    ("walla",      "he", _HE_KEYWORDS),
    ("makan",      "ar", _AR_KEYWORDS),
    ("kul_alarab", "ar", _AR_KEYWORDS),
]

MAX_PER_SOURCE = 80
MAX_PAGES = 5


async def run_one(settings: Settings, year: str, kw: str, source: str, lang: str) -> dict:
    slug = hashlib.md5(kw.encode()).hexdigest()[:8]
    run_id = f"sup{year[-2:]}_{lang}_{source}_{slug}"
    pipeline = Pipeline(settings, run_id=run_id, strict_date=False, run_narration=False)
    print(f"  [{source:<10}] ▶ {kw!r}  (run_id={run_id})", flush=True)
    try:
        stats = await pipeline.run(
            query=kw, sources=[source],
            date_from=f"{year}-01-01", date_to=f"{year}-12-31",
            max_per_source=MAX_PER_SOURCE, max_pages=MAX_PAGES,
            stages={
                "discover", "fetch", "triage", "extract",
                "dedup", "merge", "sanity", "quality", "reconcile",
            },
        )
        print(
            f"  [{source:<10}] ✓ {kw!r}  fetched={stats.get('fetched',0)} "
            f"extracted={stats.get('extracted',0)}",
            flush=True,
        )
        return {"kw": kw, "source": source, "stats": stats}
    except Exception as e:  # noqa: BLE001
        print(f"  [{source:<10}] ✗ {kw!r}  FAILED: {type(e).__name__}: {e}", flush=True)
        return {"kw": kw, "source": source, "stats": {}}


async def run_source_serially(
    settings: Settings, year: str, source: str, lang: str, keywords: list[str]
) -> list[dict]:
    """Run all keywords for one source sequentially (preserves polite-crawler)."""
    out = []
    for kw in keywords:
        out.append(await run_one(settings, year, kw, source, lang))
    return out


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_supplemental_sweep_parallel.py <YYYY>")
        sys.exit(2)
    year = sys.argv[1]
    if not (year.isdigit() and len(year) == 4):
        print(f"Invalid year: {year}")
        sys.exit(2)

    settings = Settings()
    print(f"Per-source-parallel supplemental sweep — year {year}", flush=True)
    print(f"  Streams: {[s[0] for s in SOURCE_STREAMS]}", flush=True)
    print(f"  Cap: {MAX_PER_SOURCE}/src × {MAX_PAGES} pages", flush=True)
    print(flush=True)

    streams = [
        run_source_serially(settings, year, src, lang, kws)
        for src, lang, kws in SOURCE_STREAMS
    ]
    results_per_stream = await asyncio.gather(*streams)

    print()
    print("=" * 72, flush=True)
    print(f"{'KW':<14} {'SRC':<14} {'FETCH':>6} {'TRIAGE+':>8} {'EXT':>6}", flush=True)
    for stream in results_per_stream:
        for r in stream:
            f = r["stats"].get("fetched", 0)
            t = r["stats"].get("triage_kept", 0)
            e = r["stats"].get("extracted", 0)
            print(f"  {r['kw'][:12]:<12} {r['source']:<14} {f:>6} {t:>8} {e:>6}", flush=True)

    print()
    print("Now run:", flush=True)
    print(
        f"  python -m crime_pipeline --build-canonical "
        f"--date-from {year}-01-01 --date-to {year}-12-31 --no-narrate --cosine-threshold 0.92",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
