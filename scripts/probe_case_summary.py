"""
One-shot probe: generate a memorial-register summary for ONE canonical case.

Reads the case + its source article bodies from the SQLite DB, prompts
Gemini-flash for a 2-3 sentence factual summary in ar/he/en, prints
everything to stdout so we can eyeball the tone/accuracy before deciding
whether to wire this into the pipeline.

Usage:
    python scripts/probe_case_summary.py
    python scripts/probe_case_summary.py --case-index 0
    python scripts/probe_case_summary.py --canonical output/canonical_2026-01-01_2026-02-16.json --case-index 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from google import genai
from google.genai import types
from sqlalchemy import select

from crime_pipeline.config import get_settings
from crime_pipeline.models import RawArticle
from crime_pipeline.storage import db


SYSTEM_PROMPT = """You are summarizing a documented homicide case for a public Arabic/Hebrew memorial register that records victims of crime in Arab society in Israel.

Voice: dignified, restrained, factual. This is not journalism and not eulogy — it is a public record entry.

HARD RULES:
1. Use ONLY facts present in the provided article bodies or structured fields. If something is not stated, do not infer it.
2. Do NOT speculate about motive, relationships between victim and suspect, family conflicts, or criminal background unless an article explicitly states them as confirmed fact.
3. Do NOT use loaded framing ("brutal", "tragic", "senseless"). Let the facts speak.
4. Refer to the victim by their name. Do not call them "the victim" repeatedly.
5. 2-3 short sentences. Plain prose, no headlines.

Output a single JSON object:
{
  "ar": "<Arabic summary, 2-3 sentences>",
  "he": "<Hebrew summary, 2-3 sentences>",
  "en": "<English summary, 2-3 sentences>"
}
"""


def _format_structured(case: dict) -> str:
    """Lay out the merged canonical fields in a flat key: value block."""
    fields = [
        ("victim_name_ar", case.get("victim_name_ar")),
        ("victim_name_he", case.get("victim_name_he")),
        ("victim_name_en", case.get("victim_name_en")),
        ("victim_age", case.get("victim_age")),
        ("victim_gender", case.get("victim_gender")),
        ("incident_date", case.get("incident_date")),
        ("death_date", case.get("death_date")),
        ("city", case.get("city")),
        ("neighborhood", case.get("neighborhood")),
        ("district", case.get("district")),
        ("weapon_type", case.get("weapon_type")),
        ("suspect_status", case.get("suspect_status")),
        ("legal_status", case.get("legal_status")),
        ("incident_type", case.get("incident_type")),
    ]
    lines = [f"  {k}: {v}" for k, v in fields if v not in (None, "", [])]
    return "\n".join(lines)


def _gather_article_bodies(source_urls: list[str]) -> list[tuple[str, str, str]]:
    """Return (url, language, article_text) for every URL we can resolve in the DB."""
    db.init_db(str(get_settings().db_path))
    out: list[tuple[str, str, str]] = []
    with db.SessionLocal() as session:
        rows = session.execute(
            select(RawArticle.url, RawArticle.language, RawArticle.article_text).where(
                RawArticle.url.in_(source_urls)
            )
        ).all()
        for url, lang, text in rows:
            if text:
                out.append((url, lang, text))
    return out


def _build_user_prompt(case: dict, articles: list[tuple[str, str, str]]) -> str:
    structured = _format_structured(case)
    article_block_parts = []
    for i, (url, lang, text) in enumerate(articles, start=1):
        snippet = text.strip()
        if len(snippet) > 4000:
            snippet = snippet[:4000] + "…"
        article_block_parts.append(
            f"--- Article {i} ({lang}, {url}) ---\n{snippet}"
        )
    article_block = "\n\n".join(article_block_parts) or "(no article bodies available)"
    return (
        "STRUCTURED FIELDS (merged from all sources):\n"
        f"{structured}\n\n"
        "SOURCE ARTICLES:\n\n"
        f"{article_block}\n\n"
        "Now produce the JSON summary as instructed."
    )


async def _generate(case: dict, articles: list[tuple[str, str, str]]) -> dict:
    client = genai.Client(api_key=get_settings().gemini_api_key)
    user_content = _build_user_prompt(case, articles)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
        max_output_tokens=1024,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_content,
        config=config,
    )
    raw = response.text or "{}"
    return {
        "raw": raw,
        "parsed": json.loads(raw),
        "input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
        "output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canonical",
        type=Path,
        default=Path("output/canonical_2026-01-01_2026-02-16.json"),
        help="Path to a canonical_*.json envelope.",
    )
    parser.add_argument("--case-index", type=int, default=0, help="Which case in the file to test.")
    args = parser.parse_args()

    envelope = json.loads(args.canonical.read_text(encoding="utf-8"))
    cases = envelope.get("cases", [])
    if not cases:
        print(f"[error] no cases in {args.canonical}", file=sys.stderr)
        return 1
    if args.case_index >= len(cases):
        print(f"[error] case_index {args.case_index} out of range (have {len(cases)})", file=sys.stderr)
        return 1

    case = cases[args.case_index]
    source_urls = [s.get("url") for s in case.get("sources", []) if s.get("url")]
    articles = _gather_article_bodies(source_urls)

    print("=" * 72)
    print(f"CASE #{args.case_index}")
    print(f"Name:    {case.get('victim_name_ar') or case.get('victim_name_he') or case.get('victim_name_en')}")
    print(f"Date:    {case.get('incident_date')}")
    print(f"City:    {case.get('city')}")
    print(f"Sources: {len(source_urls)} url(s) → {len(articles)} bodies resolved in DB")
    print("=" * 72)

    if not articles:
        print("[warn] no article bodies available in DB — summary will be very thin.")

    result = asyncio.run(_generate(case, articles))
    print()
    print(f"Tokens: in={result['input_tokens']} out={result['output_tokens']}")
    print()

    parsed = result["parsed"]
    for lang in ("ar", "he", "en"):
        print(f"── {lang} ──")
        print(parsed.get(lang, "(missing)"))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
