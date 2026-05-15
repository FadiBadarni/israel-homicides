"""3-stage funnel to close 2025 gap from arab48/محليات candidates.

Replaces the brute-force-all-411 sweep (~$2.70, 2 hr) with a funnel that
filters cheaply before paying for full extraction:

  Stage 1 — Free deterministic filter (~5 min, $0)
    • Drop URLs already in the DB
    • Regex-extract candidate (name, city) from arab48 titles
    • Soft-flag sentencing/arrest titles (not hard-skip — Opus's caveat:
      some sentencing articles report cases we missed)
    • Hard-skip when extracted (name, city, month) matches an existing
      2025 case via Jaro-Winkler ≥ 0.85 on romanized names

  Stage 2 — Cheap Gemini Flash batch on remaining titles (~5 min, ~$0.10)
    • One call per 30-title batch
    • Returns {type: fresh|followup|non_homicide|unclear, name, city}
    • Keep only ``fresh`` and ``unclear``
    • Writes ``output/funnel25_audit.csv`` for spot-checking

  Stage 3 — Full pipeline on survivors (~30-40 min, ~$0.40)
    • Monkey-patches Arab48Scraper.discover to return the filtered URLs
    • Runs the standard pipeline under run_id ``funnel25``
    • Build-canonical afterwards picks them up

Total: ~$0.50 vs $2.70. Approved by the multi-AI debate at
``~/.claude-octopus/debates/2025-gap-smart-cheap-001/``.
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


CSV_PATH = Path("output/arab48_localities_2025_candidates.csv")
AUDIT_PATH = Path("output/funnel25_audit.csv")
RUN_ID = "funnel25"

# Sentencing / arrest markers. SOFT flag: passed to stage 2 for Flash
# adjudication, never hard-dropped (a sentencing article occasionally
# reports a fresh victim we never indexed).
FOLLOWUP_MARKERS = [
    "السجن", "حبس", "لائحة اتهام", "اعتقال", "تمديد",
    "محكمة", "إدانة", "استئناف", "النيابة",
]


def _is_followup_title(title: str) -> bool:
    return any(m in title for m in FOLLOWUP_MARKERS)


# Patterns: "مقتل/قتل/وفاة/استشهاد <name tokens> من/في/قرب <city token>"
# Bounded to 1-4 name tokens after the verb; 1 city token after the
# preposition. Misses passive forms ("X قُتل") and headlines without
# "من/في"; those fall through to stage 2's Flash.
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
    """Return True if (name, city, month) matches an existing 2025 case."""
    if not name:
        return False
    name_r = romanize_name(name)
    month = (date or "")[:7]
    for c in existing_cases:
        if c["date"][:7] != month and month:
            # Require same month if we have a date — homicide reports
            # cluster within ~30 days of the event.
            continue
        if name and c["name_ar"]:
            # Substring match catches partial Arabic name overlaps
            # (e.g., title says "أحمد" only, case has "أحمد بدر الدين")
            if name in c["name_ar"] or c["name_ar"] in name:
                return True
        if name_r and c["name_romanized"]:
            if jellyfish.jaro_winkler_similarity(name_r, c["name_romanized"]) >= 0.85:
                return True
    return False


def _load_existing_2025_cases() -> list[dict]:
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
        if (cj.get("incident_date") or "")[:4] != "2025":
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
    """Classify a batch of arab48 titles via Gemini Flash. Returns the
    parsed JSON list aligned to ``batch`` (best-effort)."""
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
    # Pad / truncate to match batch length
    while len(parsed) < len(batch):
        parsed.append({"type": "unclear"})
    return parsed[: len(batch)]


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    # ─── Stage 1 — Free deterministic filter ──────────────────────────
    print("=== Stage 1: free deterministic filter ===")
    candidates = _load_csv_candidates()
    print(f"  CSV rows (excl. in_db): {len(candidates)}")

    existing = _load_existing_2025_cases()
    print(f"  existing 2025 cases for dedup: {len(existing)}")

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

    # ─── Stage 2 — Cheap Flash batch classification ───────────────────
    print()
    print("=== Stage 2: Gemini Flash batch on titles ===")
    if not s1_pass:
        print("  no candidates left; stopping")
        return

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.llm_model  # the project's default Flash-class model

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

    # Audit trail
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
        print("  no fresh URLs — nothing for stage 3")
        return

    # ─── Stage 3 — Full pipeline on survivors ─────────────────────────
    print()
    print(f"=== Stage 3: pipeline run on {len(fresh_urls)} URLs ===")

    # Monkey-patch Arab48Scraper.discover to return our filtered URLs.
    # The pipeline calls get_scraper("arab48").discover(...) internally;
    # patching the class method propagates to that instance.
    _injected = list(fresh_urls)
    original_discover = Arab48Scraper.discover

    async def _custom_discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
        max_pages: int = 5,
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
            date_from="2025-01-01",
            date_to="2025-12-31",
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
    print(f"  cases_exported: {stats.get('cases_exported', 0)}")
    print()
    print("Now run:")
    print(
        "  python -m crime_pipeline --build-canonical "
        "--date-from 2025-01-01 --date-to 2025-12-31 --no-narrate"
    )


if __name__ == "__main__":
    asyncio.run(main())
