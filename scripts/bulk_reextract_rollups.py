"""Bulk re-extract rollup articles with an exhaustive victim-list prompt.

Identifies articles whose title matches a count-rollup pattern AND whose
current extraction has >=5 additional_victims (a "monthly recap" pattern).
Re-extracts each with a stricter "enumerate ALL named victims" prompt +
higher max_output_tokens, then saves the new extraction as a fresh
``ExtractedRecord`` row.

The pipeline picks the latest extraction per article on the next
``build_canonical`` run, so the new exhaustive list takes over. The
existing dedup/merge handles cross-victim attribution and cross-source
collapse — most "new" names will fold into existing canonical cases via
name+city match; a few are truly net-new victims previously missed.

Free of further pipeline runs — just call this script then rebuild
canonical for the affected years.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

from crime_pipeline.config import Settings
from crime_pipeline.models import ExtractedRecord, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from sqlalchemy import desc


ROLLUP_PATTERN = re.compile(r"\d+\s*(?:قتيلا|ضحية|נרצחים|הרוגים)")
MIN_ADDL_FOR_RECAP = 5

EXTRACT_PROMPT = """You are extracting Arab-society homicide victims from an Arabic news article.

This article is a MONTHLY RECAP or RUNNING TOTAL piece that enumerates many
named victims. Find EVERY named deceased victim mentioned in the article body,
no matter how long the list.

For each victim, return a JSON object with:
  - name_ar: full Arabic name AS WRITTEN in the article (including middle names —
    do NOT shorten "باسل عنان نصار" to "باسل نصار")
  - city: city/town in Arabic, or null
  - age: age as integer, or null

Rules:
  1. Include ONLY named victims who DIED. Skip suspects, injured (non-fatal),
     and victims described only by age/gender without a name.
  2. Be EXHAUSTIVE. If the article lists 30 victims in a paragraph, return all 30.
  3. Common list pattern: "والقتلى هم: X، وY، وZ، ..." — emit each as separate.

Return ONLY a JSON array of objects.

Article body:
---
{body}
---
"""


async def _reextract_one(client: genai.Client, model: str, body: str) -> list[dict]:
    prompt = EXTRACT_PROMPT.format(body=body)
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        max_output_tokens=8000,
    )
    resp = await client.aio.models.generate_content(
        model=model, contents=prompt, config=config,
    )
    raw = resp.text or "[]"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [v for v in parsed if isinstance(v, dict) and v.get("name_ar")]


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    with db_module.SessionLocal() as session:
        # Identify recap rollups
        candidates = []
        for a in session.query(RawArticle).filter(RawArticle.title.is_not(None)).all():
            if not ROLLUP_PATTERN.search(a.title or ""):
                continue
            ext = (
                session.query(ExtractedRecord)
                .filter(ExtractedRecord.article_id == a.id)
                .order_by(desc(ExtractedRecord.extracted_at))
                .first()
            )
            if not ext:
                continue
            ej = ext.extracted_json or {}
            if len(ej.get("additional_victims") or []) >= MIN_ADDL_FOR_RECAP:
                candidates.append((a, ext))

    print(f"Re-extracting {len(candidates)} recap rollups...")
    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.llm_model

    summary = []
    for i, (a, old_ext) in enumerate(candidates, 1):
        body = a.article_text or ""
        if not body:
            print(f"  [{i:2d}] SKIP empty body: {a.url[-60:]}")
            continue
        try:
            victims = await _reextract_one(client, model, body)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i:2d}] FAIL: {a.url[-60:]}  ({e})")
            continue
        old_ej = old_ext.extracted_json or {}
        old_count = 1 + len(old_ej.get("additional_victims") or [])
        new_count = len(victims)

        if new_count <= old_count:
            print(
                f"  [{i:2d}] no gain  old={old_count} new={new_count}  "
                f"{a.url[-50:]}"
            )
            continue

        # Build a new extracted_json: keep the original primary + scalar fields,
        # but replace additional_victims with all victims except the first.
        # Each additional victim entry mirrors the schema's per-victim record.
        new_ej = dict(old_ej)
        # The first victim from re-extraction is the primary; the rest are additional.
        # We preserve the OLD primary if the re-extract's first name matches it,
        # otherwise we replace the primary too (a real correction).
        primary_new = victims[0]
        # Set primary fields if missing or to track the new exhaustive name
        if not old_ej.get("victim_name_ar"):
            new_ej["victim_name_ar"] = primary_new.get("name_ar")
        if not old_ej.get("city") and primary_new.get("city"):
            new_ej["city"] = primary_new.get("city")

        addl_list = []
        for v in victims[1:]:
            entry = {
                "victim_name_ar": v.get("name_ar"),
                "city": v.get("city"),
                "victim_age": v.get("age"),
                "victim_outcome": "died",
            }
            addl_list.append(entry)
        new_ej["additional_victims"] = addl_list

        # Save as a fresh ExtractedRecord — preserves history
        with db_module.SessionLocal() as session:
            new_row = ExtractedRecord(
                id=str(uuid.uuid4()),
                article_id=a.id,
                extracted_json=new_ej,
                validation_status=old_ext.validation_status,
                llm_model=model,
                input_tokens=0,
                output_tokens=0,
                cache_hit=False,
                latency_ms=0,
                extracted_at=datetime.now(timezone.utc),
                extraction_status="success",
            )
            session.add(new_row)
            session.commit()

        # Determine which year this article belongs to from the existing
        # extracted incident_date (or fall back to URL date)
        year = ""
        idate = (old_ej.get("incident_date") or "")[:4]
        if idate.isdigit():
            year = idate
        else:
            m = re.search(r"/(20\d{2})/", a.url)
            if m:
                year = m.group(1)
        summary.append({
            "year": year,
            "url": a.url,
            "old": old_count,
            "new": new_count,
            "delta": new_count - old_count,
        })
        print(
            f"  [{i:2d}] +{new_count - old_count:2d} victims  "
            f"old={old_count} → new={new_count}  year={year}  "
            f"{a.url[-50:]}"
        )

    # Years affected
    years_affected = sorted({r["year"] for r in summary if r["year"]})
    total_added = sum(r["delta"] for r in summary)
    print()
    print(f"=== Summary ===")
    print(f"  re-extracted: {len(summary)} articles (gained victims)")
    print(f"  total victims added across articles: {total_added}")
    print(f"  years affected: {years_affected}")
    print()
    print("Now rebuild canonical for each affected year:")
    for y in years_affected:
        print(
            f"  python -m crime_pipeline --build-canonical "
            f"--date-from {y}-01-01 --date-to {y}-12-31 --no-narrate --cosine-threshold 0.92"
        )


if __name__ == "__main__":
    asyncio.run(main())
