"""Test re-extraction of one rollup article with a stricter exhaustive prompt.

Picks the ``239 قتيلا عربيا هذا العام`` article (URL hardcoded). Currently
we extracted 20 victims; the article body actually enumerates 23. This
script shows whether a focused "enumerate all names" prompt + higher
output token budget recovers the missing 3.

Outputs a side-by-side comparison and an estimate of net-new cases the
recovered victims would create (cross-referenced against existing DB).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase, ExtractedRecord, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from sqlalchemy import desc

# Test article — 239-count rollup from Dec 1 2025
TARGET_URL = (
    "https://www.arab48.com/الأخبار/أخبار-عاجلة/2025/12/01/"
    "كفر-كنا-مصاب-بحالة-حرجة-بجريمة-إطلاق-نار"
)

REEXTRACT_PROMPT = """You are extracting Arab-society homicide victims from an Arabic news article.

This article may be a MONTHLY RECAP or RUNNING TOTAL piece that enumerates many
named victims. Your job is to find EVERY named deceased victim mentioned in the
article body, no matter how long the list.

For each victim, return a JSON object with:
  - name_ar: full Arabic name as written in the article (do not normalize)
  - city: their city/town, or null if not specified

Rules:
  1. Include ONLY named victims who DIED. Skip suspects, injured (non-fatal),
     and victims described only by age/gender without a name.
  2. Be EXHAUSTIVE. If the article lists 25 named victims in a paragraph,
     return all 25. Do NOT truncate. Do NOT summarize.
  3. The text often uses a serialized list format like
     "والقتلى هم: X، وY، وZ، ..." — extract each as a separate entry.

Return a JSON array of objects, nothing else.

Article body:
---
{body}
---
"""


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    with db_module.SessionLocal() as session:
        art = session.query(RawArticle).filter(RawArticle.url == TARGET_URL).first()
        if not art:
            print(f"Article not found: {TARGET_URL}"); return
        ext = (
            session.query(ExtractedRecord)
            .filter(ExtractedRecord.article_id == art.id)
            .order_by(desc(ExtractedRecord.extracted_at))
            .first()
        )

    ej = ext.extracted_json or {}
    primary = ej.get("victim_name_ar") or "?"
    addl = ej.get("additional_victims") or []
    print(f"=== Original extraction ===")
    print(f"  primary: {primary}")
    print(f"  additional: {len(addl)} victims")
    for v in addl:
        n = v.get("victim_name_ar") or v.get("victim_name") or "?"
        c = v.get("city") or "?"
        print(f"    - {n}  ({c})")
    print(f"  TOTAL: {1 + len(addl)} victims")
    print()

    print(f"=== Re-extracting with exhaustive prompt ===")
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = REEXTRACT_PROMPT.format(body=art.article_text or "")
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        max_output_tokens=8000,
    )
    resp = await client.aio.models.generate_content(
        model=settings.llm_model, contents=prompt, config=config,
    )
    raw = resp.text or "[]"
    try:
        new_victims = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(raw[:500])
        return

    print(f"  re-extracted: {len(new_victims)} victims")
    for v in new_victims:
        print(f"    - {v.get('name_ar')}  ({v.get('city')})")

    # Compare — which names are NEW vs already in our DB
    print()
    print(f"=== Net-new analysis ===")
    with db_module.SessionLocal() as session:
        all_cases = (
            session.query(CanonicalCase)
            .filter(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .all()
        )
    known_names: set[str] = set()
    for cc in all_cases:
        cj = cc.case_json or {}
        for k in ("victim_name_ar", "victim_name_he", "victim_name"):
            v = cj.get(k)
            if v:
                known_names.add(v.strip())
        for v in (cj.get("aliases") or []):
            if v:
                known_names.add(v.strip())

    really_new: list[dict] = []
    for v in new_victims:
        name = (v.get("name_ar") or "").strip()
        if not name:
            continue
        # Substring match — name in any known name, or vice versa
        matched = any(
            (name in kn or kn in name)
            for kn in known_names
            if len(kn) >= 3
        )
        if not matched:
            really_new.append(v)

    print(f"  victims in re-extraction: {len(new_victims)}")
    print(f"  already represented in DB: {len(new_victims) - len(really_new)}")
    print(f"  GENUINELY net-new candidates: {len(really_new)}")
    for v in really_new:
        print(f"    + {v.get('name_ar')}  ({v.get('city')})")


if __name__ == "__main__":
    asyncio.run(main())
