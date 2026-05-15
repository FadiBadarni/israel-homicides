"""Per-year leak scan for the canonical_cases dataset.

Surfaces three categories per year:
  1. Unresolved city — flag for non-Arab-society leaks. Pull each case's
     first source title for triage.
  2. Cases sharing a source URL with another case (potential extraction
     artifact where the LLM extracted multiple "victims" from one article).
  3. Single-source cases where the article title mentions injury but the
     case is marked died (extraction error).

Output: console per-year report + CSV at output/year_leaks_<YYYY>.csv.
Free (no LLM).
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


# Injury markers that suggest the article describes a non-fatal event
# (i.e., the LLM may have wrongly classified a wounded person as died).
INJURY_TITLE_MARKERS_HE = [
    "פצוע", "נפצע", "פציעה", "נפצעה",
    "נער נפצע", "אישה נפצעה",
]
INJURY_TITLE_MARKERS_AR = [
    "إصابة", "أصيب", "أصيبت", "إصابات", "أُصيب",
    "بجراح",
]


def _has_injury_marker(title: str) -> bool:
    if not title:
        return False
    return any(m in title for m in INJURY_TITLE_MARKERS_HE + INJURY_TITLE_MARKERS_AR)


def _scan_year(year: str) -> None:
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

        # Dedupe to one row per canonical_case_id (most recent updated_at)
        by_id: dict[str, CanonicalCase] = {}
        for r in sorted(rows, key=lambda r: r.updated_at or "", reverse=True):
            cj = r.case_json or {}
            cid = cj.get("canonical_case_id") or r.id
            if cid in by_id:
                continue
            if (cj.get("incident_date") or "")[:4] != year:
                continue
            by_id[cid] = r
        cases = list(by_id.values())

        # Build URL → article-title lookup
        all_urls = set()
        for r in cases:
            for s in (r.case_json or {}).get("sources") or []:
                u = s.get("url")
                if u:
                    all_urls.add(u)
        title_by_url: dict[str, str] = {}
        for url in all_urls:
            art = session.query(RawArticle).filter(RawArticle.url == url).first()
            if art and art.title:
                title_by_url[url] = art.title

        print(f"=== Year {year}: {len(cases)} cases ===\n")

        # ─── 1. Unresolved-city cases ───────────────────────────────
        unresolved: list[dict] = []
        for r in cases:
            cj = r.case_json or {}
            raw_city = cj.get("city") or ""
            rec = gazetteer.normalize_city(raw_city) if raw_city else None
            if not rec or rec.get("lat") is None:
                name = (
                    cj.get("victim_name_ar") or cj.get("victim_name_he")
                    or cj.get("victim_name_en") or "?"
                )
                first_url = (cj.get("sources") or [{}])[0].get("url") or ""
                title = title_by_url.get(first_url) or ""
                unresolved.append({
                    "canonical_case_id": cj.get("canonical_case_id"),
                    "name": name,
                    "city": raw_city,
                    "date": cj.get("incident_date") or "",
                    "n_sources": len(cj.get("sources") or []),
                    "first_title": title,
                })

        print(f"1) Unresolved-city candidates: {len(unresolved)}")
        for u in unresolved:
            print(
                f"  [{u['date']}] {u['city']:25s} | "
                f"{u['name'][:30]:30s} | src={u['n_sources']:2d} | "
                f"{u['first_title'][:80]}"
            )
        print()

        # ─── 2. Cases sharing a source URL ─────────────────────────
        url_to_cases: dict[str, list[dict]] = defaultdict(list)
        for r in cases:
            cj = r.case_json or {}
            cid = cj.get("canonical_case_id")
            name = (
                cj.get("victim_name_ar") or cj.get("victim_name_he")
                or cj.get("victim_name_en") or "?"
            )
            for s in cj.get("sources") or []:
                u = s.get("url")
                if u:
                    url_to_cases[u].append({
                        "canonical_case_id": cid,
                        "name": name,
                        "city": cj.get("city") or "",
                        "date": cj.get("incident_date") or "",
                    })
        shared = [(u, cs) for u, cs in url_to_cases.items() if len(cs) > 1]
        # Filter to interesting ones — at least 3 cases sharing one URL,
        # which is highly suspicious (article enumerating fake victims)
        shared.sort(key=lambda x: -len(x[1]))
        print(f"2) URLs cited by >1 case: {len(shared)} (showing top 10 with >=3)")
        for u, cs in shared[:10]:
            if len(cs) < 3:
                continue
            title = title_by_url.get(u) or ""
            print(f"  → {u[:90]}")
            print(f"    title: {title[:100]}")
            for c in cs:
                print(f"      - [{c['date']}] {c['city'][:20]:20s} | {c['name'][:30]}")
        print()

        # ─── 3. Injury-marker title cases ──────────────────────────
        injuries: list[dict] = []
        for r in cases:
            cj = r.case_json or {}
            srcs = cj.get("sources") or []
            if not srcs:
                continue
            # Only flag SINGLE-source cases — multi-source cases have
            # cross-corroboration that we trust
            if len(srcs) > 1:
                continue
            first_url = srcs[0].get("url") or ""
            title = title_by_url.get(first_url) or ""
            if _has_injury_marker(title):
                name = (
                    cj.get("victim_name_ar") or cj.get("victim_name_he")
                    or cj.get("victim_name_en") or "?"
                )
                injuries.append({
                    "canonical_case_id": cj.get("canonical_case_id"),
                    "name": name,
                    "city": cj.get("city") or "",
                    "date": cj.get("incident_date") or "",
                    "title": title,
                })

        print(f"3) Single-source cases with injury-only title markers: {len(injuries)}")
        for x in injuries[:15]:
            print(
                f"  [{x['date']}] {x['city'][:20]:20s} | "
                f"{x['name'][:25]:25s} | {x['title'][:80]}"
            )
        print()

        # Write CSV combining all flagged
        out = []
        for u in unresolved:
            out.append({"category": "unresolved_city", **u})
        for u, cs in shared:
            if len(cs) < 3:
                continue
            title = title_by_url.get(u) or ""
            for c in cs:
                out.append({
                    "category": "shared_url",
                    "canonical_case_id": c["canonical_case_id"],
                    "name": c["name"],
                    "city": c["city"],
                    "date": c["date"],
                    "n_sources": "?",
                    "first_title": title,
                    "shared_url": u,
                })
        for x in injuries:
            out.append({"category": "injury_only", **x, "n_sources": 1, "first_title": x["title"]})
        csv_path = Path(f"output/year_leaks_{year}.csv")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if out:
            keys = ["category", "canonical_case_id", "name", "city", "date", "n_sources", "first_title"]
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                w.writerows(out)
            print(f"Wrote: {csv_path}")
        print()


def main() -> None:
    for year in ["2024", "2025"]:
        _scan_year(year)
        print()


if __name__ == "__main__":
    main()
