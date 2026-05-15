"""3-stage funnel for 2023 backfill from arab48/محليات candidates.

Identical to ``funnel_arab48_2024.py`` — only year window + run_id differ.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

import jellyfish
from google import genai
from google.genai import types

from crime_pipeline.config import Settings
from crime_pipeline.dedup.name_normalizer import romanize_name
from crime_pipeline.models import CanonicalCase
from crime_pipeline.pipeline import Pipeline
from crime_pipeline.scrapers import Arab48Scraper
from crime_pipeline.scrapers.base import DiscoveredUrl
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


YEAR = "2022"
CSV_PATH = Path(f"output/arab48_localities_{YEAR}_candidates.csv")
AUDIT_PATH = Path(f"output/funnel{YEAR[-2:]}_audit.csv")
RUN_ID = f"funnel{YEAR[-2:]}"

FOLLOWUP_MARKERS = [
    "السجن", "حبس", "لائحة اتهام", "اعتقال", "تمديد",
    "محكمة", "إدانة", "استئناف", "النيابة",
]


def _is_followup_title(title: str) -> bool:
    return any(m in title for m in FOLLOWUP_MARKERS)


_NAME_CITY_RE = re.compile(
    r"(?:مقتل|قتل|وفاة|استشهاد)\s+"
    r"(\S+(?:\s+\S+){0,3})\s+"
    r"(?:من|في|قرب)\s+"
    r"(\S+)"
)


def _extract_name_city(title: str) -> tuple[Optional[str], Optional[str]]:
    m = _NAME_CITY_RE.search(title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def _is_known_case(
    name: Optional[str],
    city: Optional[str],
    date: str,
    existing_cases: list[dict],
) -> bool:
    if not name:
        return False
    name_r = romanize_name(name)
    month = (date or "")[:7]
    for c in existing_cases:
        if c["date"][:7] != month and month:
            continue
        if name and c["name_ar"]:
            if name in c["name_ar"] or c["name_ar"] in name:
                return True
        if name_r and c["name_romanized"]:
            if jellyfish.jaro_winkler_similarity(name_r, c["name_romanized"]) >= 0.85:
                return True
    return False


def _load_existing_year_cases(year: str) -> list[dict]:
    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        rows = (
            session.query(CanonicalCase)
            .filter(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .all()
        )
    cases: list[dict] = []
    for cc in rows:
        cj = cc.case_json or {}
        if (cj.get("incident_date") or "")[:4] != year:
            continue
        name_ar = cj.get("victim_name_ar") or ""
        cases.append({
            "name_ar": name_ar,
            "name_romanized": romanize_name(name_ar) if name_ar else "",
            "city": cj.get("city") or "",
            "date": cj.get("incident_date") or "",
        })
    return cases


def _load_csv_candidates() -> list[dict]:
    candidates: list[dict] = []
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("in_db") == "yes":
                continue
            candidates.append({
                "date": row["date"],
                "title": row["title"],
                "url": row["url"],
            })
    return candidates


async def _flash_classify_batch(
    client: genai.Client,
    model: str,
    batch: list[dict],
) -> list[dict]:
    numbered = "\n".join(
        f"{i + 1}. [{c['date']}] {c['title']}" for i, c in enumerate(batch)
    )
    prompt = (
        "You classify Arabic news headlines about violent incidents in Israeli "
        "Arab society. For each numbered title, return a JSON object with:\n"
        "  type: \"fresh\" (a new homicide event) | \"followup\" (arrest/trial/"
        "sentence of a past killing) | \"non_homicide\" (injury without death, "
        "threats, political) | \"unclear\"\n"
        "  name: candidate victim full name in Arabic, or null\n"
        "  city: candidate city in Arabic, or null\n\n"
        "Output a JSON array, one object per title, in the SAME ORDER.\n\n"
        f"Titles:\n{numbered}\n"
    )
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
    )
    resp = await client.aio.models.generate_content(
        model=model, contents=prompt, config=config,
    )
    raw = resp.text or "[]"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARNING: JSON parse error: {e}; raw={raw[:200]}")
        return [{"type": "unclear"} for _ in batch]
    if not isinstance(parsed, list):
        return [{"type": "unclear"} for _ in batch]
    while len(parsed) < len(batch):
        parsed.append({"type": "unclear"})
    return parsed[: len(batch)]


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    print(f"=== Stage 1: free deterministic filter ({YEAR}) ===")
    candidates = _load_csv_candidates()
    print(f"  CSV rows (excl. in_db): {len(candidates)}")

    existing = _load_existing_year_cases(YEAR)
    print(f"  existing {YEAR} cases for dedup: {len(existing)}")

    s1_pass: list[dict] = []
    s1_followup_flag = 0
    s1_known_skip = 0
    for c in candidates:
        flags: list[str] = []
        if _is_followup_title(c["title"]):
            flags.append("followup_marker")
            s1_followup_flag += 1
        name, city = _extract_name_city(c["title"])
        c["candidate_name"] = name
        c["candidate_city"] = city
        if _is_known_case(name, city, c["date"], existing):
            flags.append("known_case")
            s1_known_skip += 1
        c["flags"] = flags
        if "known_case" not in flags:
            s1_pass.append(c)
    print(
        f"  flagged_as_followup={s1_followup_flag}  "
        f"hard_skipped_as_known={s1_known_skip}  "
        f"pass_to_stage2={len(s1_pass)}"
    )

    print()
    print("=== Stage 2: Gemini Flash batch on titles ===")
    if not s1_pass:
        print("  nothing to classify"); return

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.llm_model

    BATCH = 30
    classified: list[dict] = []
    for i in range(0, len(s1_pass), BATCH):
        batch = s1_pass[i:i + BATCH]
        try:
            results = await _flash_classify_batch(client, model, batch)
        except Exception as exc:  # noqa: BLE001
            print(f"  batch {i // BATCH + 1} failed: {exc!r}")
            results = [{"type": "unclear"} for _ in batch]
        for c, r in zip(batch, results):
            c["flash_type"] = r.get("type", "unclear")
            c["flash_name"] = r.get("name")
            c["flash_city"] = r.get("city")
            classified.append(c)
        print(f"  batch {i // BATCH + 1}/{(len(s1_pass) + BATCH - 1) // BATCH}: {len(batch)} titles")

    type_dist = Counter(c["flash_type"] for c in classified)
    print(f"  flash distribution: {dict(type_dist)}")

    fresh_urls = [
        c["url"] for c in classified
        if c["flash_type"] in ("fresh", "unclear")
    ]
    print(f"  passed to stage 3: {len(fresh_urls)}")

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "title", "url",
            "stage1_flags", "candidate_name", "candidate_city",
            "flash_type", "flash_name", "flash_city",
        ])
        for c in classified:
            w.writerow([
                c["date"], c["title"], c["url"],
                ";".join(c.get("flags") or []),
                c.get("candidate_name") or "",
                c.get("candidate_city") or "",
                c.get("flash_type", ""),
                c.get("flash_name") or "",
                c.get("flash_city") or "",
            ])
    print(f"  audit trail: {AUDIT_PATH}")

    if not fresh_urls:
        print("  no fresh URLs — done"); return

    print()
    print(f"=== Stage 3: pipeline run on {len(fresh_urls)} URLs ===")
    _injected = list(fresh_urls)
    original_discover = Arab48Scraper.discover

    async def _custom_discover(
        self, query, date_from, date_to, max_results=50, max_pages=5,
    ):
        return [
            DiscoveredUrl(
                url=u, source="arab48", language="ar",
                title=None, published_at=None,
                discovered_at=datetime.now(timezone.utc),
            )
            for u in _injected
        ]

    Arab48Scraper.discover = _custom_discover  # type: ignore[assignment]
    try:
        pipeline = Pipeline(
            settings, run_id=RUN_ID, strict_date=False, run_narration=False,
        )
        stats = await pipeline.run(
            query="funnel",
            sources=["arab48"],
            date_from=f"{YEAR}-01-01",
            date_to=f"{YEAR}-12-31",
            max_per_source=len(fresh_urls) + 10,
            max_pages=1,
            stages={
                "discover", "fetch", "triage", "extract",
                "dedup", "merge", "sanity", "quality", "reconcile",
            },
        )
    finally:
        Arab48Scraper.discover = original_discover  # type: ignore[assignment]

    print()
    print("=== Done ===")
    print(f"  fetched: {stats.get('fetched', 0)}")
    print(f"  triage_kept: {stats.get('triage_kept', 0)}")
    print(f"  extracted: {stats.get('extracted', 0)}")


if __name__ == "__main__":
    asyncio.run(main())
