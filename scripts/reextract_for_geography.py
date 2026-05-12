"""Re-extract every fetched article so the new ``incident_geography``
field gets populated.

The Jan/Feb 2026 sweeps were extracted before ``incident_geography``
existed in the schema. Without re-running, every existing case has
``incident_geography = None`` and the new declarative filter in
``build_validated_2026.py`` will fall back to "treat None as unknown"
which keeps foreign-news leakage.

This script:
  1. Loads every ``raw_articles`` row with ``fetch_status='success'``
     AND ``triage_status IN ('yes', 'maybe')`` — i.e. articles that
     passed triage and were sent to extract at least once.
  2. Sends each through ``ArticleExtractor`` again. The current prompt
     emits ``incident_geography`` per the new schema.
  3. ``save_extraction`` upserts a NEW extracted_records row per
     article (the table preserves history, the dedup_and_merge step
     picks the most-recent). Old rows stay for audit.

Cost: ~$0.30-0.50 in Gemini (~800 articles × 8K input + 1K output @
2.5-flash pricing). Runtime: ~30 min with concurrency=4.

Run from project root:
    python scripts/reextract_for_geography.py
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

import sqlite3

from crime_pipeline.config import Settings
from crime_pipeline.extraction.extractor import ArticleExtractor
import crime_pipeline.storage.db as db_mod
from crime_pipeline.storage.db import init_db
from crime_pipeline.storage.repository import save_extraction


# Concurrency cap — Gemini rate limits are 1000 RPM for flash; 4 parallel
# requests with ~5-15s latency each = ~16-50 articles/min, well within
# the cap.
CONCURRENCY = 4

# Set BATCH=1 to test on a small slice before committing to a full
# re-extraction run. The full ~800-article run costs ~$0.40 and takes
# 30-60 min; the smoke test costs cents.
BATCH_LIMIT = int(os.environ.get("REEXTRACT_BATCH", "0")) or None


async def main() -> None:
    settings = Settings()
    init_db(str(settings.db_path))

    conn = sqlite3.connect(str(settings.db_path))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, article_text, language, source, published_at
        FROM raw_articles
        WHERE fetch_status = 'success'
          AND triage_status IN ('yes', 'maybe')
          AND article_text IS NOT NULL
          AND length(article_text) > 200
        ORDER BY fetched_at DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    if BATCH_LIMIT is not None:
        rows = rows[:BATCH_LIMIT]

    article_inputs = [
        {
            "article_id": r[0],
            "article_text": r[1],
            "language": r[2],
            "source": r[3],
            "published_at": r[4],
        }
        for r in rows
    ]
    print(f"Re-extracting {len(article_inputs)} articles "
          f"(concurrency={CONCURRENCY})...")

    extractor = ArticleExtractor(
        api_key=settings.gemini_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        concurrency=CONCURRENCY,
    )

    # extract_batch handles internal concurrency + retries
    results = await extractor.extract_batch(article_inputs)

    success = failed = 0
    geo_counts: dict[str | None, int] = {}
    with db_mod.SessionLocal() as session:
        for inp, res in zip(article_inputs, results):
            if res.get("status") == "success" and res.get("extracted_data"):
                data = res["extracted_data"]
                geo = data.get("incident_geography")
                geo_counts[geo] = geo_counts.get(geo, 0) + 1
                save_extraction(
                    session,
                    inp["article_id"],
                    {
                        "article_id": inp["article_id"],
                        "extracted_json": data,
                        "validation_status": "valid",
                        "llm_model": settings.llm_model,
                        "input_tokens": res.get("input_tokens", 0),
                        "output_tokens": res.get("output_tokens", 0),
                        "cache_hit": res.get("cache_hit", False),
                        "latency_ms": res.get("latency_ms", 0),
                        "extraction_status": "success",
                    },
                )
                success += 1
            else:
                failed += 1
        session.commit()

    print()
    print(f"Done. Success: {success}  Failed: {failed}")
    print("Geography breakdown across successful extractions:")
    for geo, n in sorted(geo_counts.items(), key=lambda x: -x[1]):
        print(f"  {geo!r}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
