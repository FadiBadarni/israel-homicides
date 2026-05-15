"""Auto-merge canonical case pairs that are unambiguous duplicates.

Criteria for "unambiguous":
  * Same gazetteer-resolved city
  * Names romanize identical (Jaro-Winkler = 1.0)
  * Incident date within ±1 day

For each matching pair, we pick the STRONG case (more sources, then higher
confidence) and absorb the WEAK case's content into it:

  * sources    — URL-dedup union
  * aliases    — string-dedup union, plus weak's primary name fields
  * media + media_evidence — media_id-dedup union (falls back to primary_url)
  * scalar fields — fill strong's NULLs from weak

The WEAK row is then deleted from canonical_cases. Idempotent: a second
run finds 0 pairs (all already merged).

Free (no LLM). Run after the duplicate scanner has surfaced the
suspect list to confirm what's about to be merged.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

import jellyfish

from crime_pipeline.config import Settings
from crime_pipeline.dedup.name_normalizer import romanize_name
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


JARO_EXACT = 0.999
DATE_PROXIMITY_DAYS = 1


def _canonical_city(raw: str | None) -> str | None:
    if not raw:
        return None
    rec = gazetteer.normalize_city(raw)
    if rec and rec.get("name_en"):
        return rec["name_en"].lower()
    return raw.strip().lower()


def _parse_date(s: str | None) -> _date | None:
    if not s:
        return None
    try:
        return _date.fromisoformat(s[:10])
    except ValueError:
        return None


def _all_names(c: dict) -> list[str]:
    out = []
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        v = (c.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    for v in (c.get("aliases") or []):
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _best_name_jaro(case_a: dict, case_b: dict) -> float:
    best = 0.0
    for na in _all_names(case_a):
        ra = romanize_name(na)
        if not ra:
            continue
        for nb in _all_names(case_b):
            rb = romanize_name(nb)
            if not rb:
                continue
            s = jellyfish.jaro_winkler_similarity(ra, rb)
            if s > best:
                best = s
                if best >= 1.0:
                    return best
    return best


def _strong_weak(a: CanonicalCase, b: CanonicalCase) -> tuple[CanonicalCase, CanonicalCase]:
    """Pick the row to KEEP (strong) and the row to DELETE (weak)."""
    aj = a.case_json or {}
    bj = b.case_json or {}
    a_n = len(aj.get("sources") or [])
    b_n = len(bj.get("sources") or [])
    if a_n != b_n:
        return (a, b) if a_n > b_n else (b, a)
    # Tie on source count → prefer higher confidence
    if (a.confidence_score or 0.0) >= (b.confidence_score or 0.0):
        return a, b
    return b, a


def _absorb(strong_json: dict, weak_json: dict) -> dict:
    """Mutate ``strong_json`` to absorb ``weak_json``'s content. Returns strong."""

    # 1. Fill NULL scalars from weak
    SCALAR_KEYS = [
        "victim_name", "victim_name_ar", "victim_name_he", "victim_name_en",
        "victim_age", "victim_gender", "victim_outcome",
        "incident_date", "death_date", "city", "neighborhood", "district",
        "weapon_type", "weapon_subtype", "suspect_status", "suspect_name",
        "incident_geography", "case_narrative",
        "case_narrative_ar", "case_narrative_he", "case_narrative_en",
    ]
    for k in SCALAR_KEYS:
        if strong_json.get(k) is None and weak_json.get(k) is not None:
            strong_json[k] = weak_json[k]

    # 2. Aliases — union + bring weak's primary names if not already alias
    aliases = list(strong_json.get("aliases") or [])
    for a in (weak_json.get("aliases") or []):
        if a and a not in aliases:
            aliases.append(a)
    strong_primaries = {
        strong_json.get(k) for k in
        ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
        if strong_json.get(k)
    }
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        v = weak_json.get(k)
        if v and v not in strong_primaries and v not in aliases:
            aliases.append(v)
    strong_json["aliases"] = aliases

    # 3. Sources — URL-dedup union
    existing_urls = {s.get("url") for s in (strong_json.get("sources") or [])}
    for s in (weak_json.get("sources") or []):
        if s.get("url") and s.get("url") not in existing_urls:
            (strong_json.setdefault("sources", [])).append(s)
            existing_urls.add(s.get("url"))

    # 4. Media + media_evidence — media_id (fallback primary_url) dedup union
    for field in ("media", "media_evidence"):
        existing_ids = {
            (m.get("media_id") or m.get("primary_url"))
            for m in (strong_json.get(field) or [])
        }
        for m in (weak_json.get(field) or []):
            key = m.get("media_id") or m.get("primary_url")
            if key and key not in existing_ids:
                (strong_json.setdefault(field, [])).append(m)
                existing_ids.add(key)

    return strong_json


def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        rows = (
            session.query(CanonicalCase)
            .filter(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .all()
        )

        # Dedupe to one row per canonical_case_id (newest updated_at wins).
        # This skips the historical duplicate rows from prior rebuilds.
        by_id: dict[str, CanonicalCase] = {}
        rows_sorted = sorted(rows, key=lambda r: r.updated_at or "", reverse=True)
        for r in rows_sorted:
            cj = r.case_json or {}
            cid = cj.get("canonical_case_id") or r.id
            if cid in by_id:
                continue
            by_id[cid] = r
        canonical_rows = list(by_id.values())

        # Group by year for the scan
        by_year: dict[str, list[CanonicalCase]] = defaultdict(list)
        for r in canonical_rows:
            cj = r.case_json or {}
            date = _parse_date(cj.get("incident_date"))
            if not date:
                continue
            by_year[str(date.year)].append(r)

        pairs_to_merge: list[tuple[CanonicalCase, CanonicalCase, float]] = []
        for year, cases in by_year.items():
            for i in range(len(cases)):
                for j in range(i + 1, len(cases)):
                    a_json = cases[i].case_json or {}
                    b_json = cases[j].case_json or {}
                    da = _parse_date(a_json.get("incident_date"))
                    db_ = _parse_date(b_json.get("incident_date"))
                    if not da or not db_:
                        continue
                    if abs((da - db_).days) > DATE_PROXIMITY_DAYS:
                        continue
                    city_a = _canonical_city(a_json.get("city"))
                    city_b = _canonical_city(b_json.get("city"))
                    if not city_a or city_a != city_b:
                        continue
                    score = _best_name_jaro(a_json, b_json)
                    if score < JARO_EXACT:
                        continue
                    pairs_to_merge.append((cases[i], cases[j], score))

        print(f"Found {len(pairs_to_merge)} unambiguous duplicate pairs")
        if not pairs_to_merge:
            print("Nothing to merge.")
            return

        # Connected components — if A=B and B=C, merge all three into one
        parent: dict[str, str] = {r.id: r.id for r, _, _ in pairs_to_merge}
        parent.update({r.id: r.id for _, r, _ in pairs_to_merge})

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for a, b, _ in pairs_to_merge:
            union(a.id, b.id)

        # Build clusters from union-find
        clusters: dict[str, list[CanonicalCase]] = defaultdict(list)
        row_by_id: dict[str, CanonicalCase] = {}
        for a, b, _ in pairs_to_merge:
            row_by_id[a.id] = a
            row_by_id[b.id] = b
        for rid, r in row_by_id.items():
            clusters[find(rid)].append(r)

        print(f"  → {len(clusters)} cluster(s) to merge")
        print()

        deleted_count = 0
        for cluster in clusters.values():
            # Pick the strong row (most sources, then highest confidence).
            sorted_rows = sorted(
                cluster,
                key=lambda r: (
                    -len((r.case_json or {}).get("sources") or []),
                    -(r.confidence_score or 0.0),
                ),
            )
            strong = sorted_rows[0]
            weaks = sorted_rows[1:]

            strong_json = dict(strong.case_json or {})
            for w in weaks:
                strong_json = _absorb(strong_json, w.case_json or {})

            # Update strong row's columns to reflect the absorb
            strong.case_json = strong_json
            strong.sources_merged = [
                s.get("url") for s in (strong_json.get("sources") or [])
                if s.get("url")
            ]

            s_name = (
                strong_json.get("victim_name_ar")
                or strong_json.get("victim_name_he")
                or strong_json.get("victim_name_en")
                or "?"
            )
            new_src_count = len(strong_json.get("sources") or [])
            print(
                f"  ✓ Keeping {s_name[:30]:30s} ({strong_json.get('city')}) "
                f"with {new_src_count} sources; removing {len(weaks)} dup(s)"
            )

            # Delete the weak rows
            for w in weaks:
                session.delete(w)
                deleted_count += 1

        session.commit()

    print()
    print(f"=== Done: deleted {deleted_count} duplicate canonical_cases rows ===")
    print()
    print("Verify with the UI: refresh the browser.")


if __name__ == "__main__":
    main()
