"""One-off: insert canonical_cases row for غالب بلقيس.

The Feb 18 2026 Haifa double-murder produced 2 ExtractedRecord rows
that named Ghalib as primary victim (alongside Suheil Abu Jabal). Dedup
collapsed both virtual records into Suheil's cluster, leaving Ghalib
absent from canonical_cases. This script builds a standalone canonical
row for Ghalib using ext a65c2dc9 as the source of truth and Suheil's
4-publisher source list (same incident, same coverage) so the media
demo can harvest cross-source.
"""
from __future__ import annotations

import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase, ExtractedRecord, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


CANON_ID = "IL-HOMICIDE-2026-HAIFA-GHLB-BLQYS"
RUN_ID = "canonical_2026-01-01_2026-05-15"
SUHEIL_CANON_ID = "IL-HOMICIDE-2026-HAIFA-SHYL-ABU-JBL"


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    with db_module.SessionLocal() as sess:
        # Already exists?
        existing = list(sess.scalars(
            select(CanonicalCase).where(CanonicalCase.pipeline_run_id == RUN_ID)
        ))
        for r in existing:
            if (r.case_json or {}).get("canonical_case_id") == CANON_ID:
                print(f"already exists at row.id={r.id}")
                return

        # Primary extraction (dedicated article)
        ext = sess.scalar(
            select(ExtractedRecord).where(ExtractedRecord.id.like("a65c2dc9%"))
        )
        if ext is None:
            print("ERROR: primary extraction not found")
            return
        ej = ext.extracted_json

        # Suheil's sources — same incident, gives Ghalib the multi-publisher
        # coverage the dedup bug stripped from him.
        suheil = next(
            (r for r in existing
             if (r.case_json or {}).get("canonical_case_id") == SUHEIL_CANON_ID),
            None,
        )
        sources = list(suheil.case_json.get("sources") or []) if suheil else []
        if not sources:
            # Fallback: just the dedicated article
            art = sess.get(RawArticle, ext.article_id)
            sources = [{
                "url": art.url,
                "source_name": "arab48",
                "domain": "arab48.com",
                "published_at": (
                    art.published_at.isoformat() if art.published_at else None
                ),
                "title": art.title,
            }]

    city_rec = gazetteer.normalize_city(ej.get("city") or "")
    city_norm = None
    if city_rec:
        city_norm = {
            "name_ar": city_rec.get("name_ar"),
            "name_he": city_rec.get("name_he"),
            "name_en": city_rec.get("name_en"),
            "district": city_rec.get("district"),
            "lat": city_rec.get("lat"),
            "lng": city_rec.get("lng"),
        }

    case_json = {
        "canonical_case_id": CANON_ID,
        "incident_geography": "israel_arab_society",
        "victim_name": ej.get("victim_name"),
        "victim_name_ar": ej.get("victim_name_ar"),
        "victim_name_he": None,
        "victim_name_en": None,
        "aliases": [],
        "name_transliterations": [],
        "victim_age": ej.get("victim_age"),
        "victim_gender": ej.get("victim_gender"),
        "victim_profession": ej.get("victim_profession"),
        "victim_residence": ej.get("victim_residence"),
        "death_date": ej.get("death_date"),
        "incident_date": ej.get("incident_date"),
        "incident_date_possible": None,
        "incident_time": ej.get("incident_time"),
        "city": ej.get("city"),
        "city_normalized": city_norm,
        "neighborhood": ej.get("neighborhood"),
        "exact_place_type": ej.get("exact_place_type"),
        "district": ej.get("district"),
        "region": ej.get("region"),
        "hospital": ej.get("hospital"),
        "weapon_type": ej.get("weapon_type"),
        "weapon_subtype": ej.get("weapon_subtype"),
        "num_victims": ej.get("num_victims") or 2,
        "suspect_name": ej.get("suspect_name"),
        "suspect_age": ej.get("suspect_age"),
        "suspect_relation": ej.get("suspect_relation"),
        "suspect_profession": ej.get("suspect_profession"),
        "suspect_profession_conflict": [],
        "suspect_status": ej.get("suspect_status"),
        "legal_status": ej.get("legal_status"),
        "police_investigation_status": ej.get("police_investigation_status"),
        "arrest_location": ej.get("arrest_location"),
        "police_status": ej.get("police_status"),
        "evidence": [],
        "media": [],
        "media_evidence": [],
        "motive": ej.get("motive"),
        "motive_translations": {},
        "organized_crime": ej.get("organized_crime"),
        "family_dispute": ej.get("family_dispute"),
        "community_context": ej.get("community_context"),
        "victim_outcome": ej.get("victim_outcome") or "died",
        "confidence_score": ej.get("confidence_score") or 0.85,
        "review_status": "auto",
        "flags": [],
        "sources": sources,
        "tier_coverage": {},
        "timeline": [],
        "case_narrative_ar": None,
        "case_narrative_he": None,
        "case_narrative_en": None,
        "rejected_unrelated_articles": [],
        "dropped_invalid_sources": [],
        "enrichment_passes": [],
        "conflicts": {},
        "reconciliation_provenance": {},
        "pipeline_run_id": RUN_ID,
    }

    new_row = CanonicalCase(
        id=str(uuid.uuid4()),
        pipeline_run_id=RUN_ID,
        case_json=case_json,
        sources_merged=[s.get("url") for s in sources if s.get("url")],
        confidence_score=case_json.get("confidence_score", 0.85),
        flags=[],
        review_status="auto",
        updated_at=datetime.now(timezone.utc),
    )
    with db_module.SessionLocal() as sess:
        sess.add(new_row)
        sess.commit()

    print(f"inserted canonical row id={new_row.id}")
    print(f"  canonical_case_id: {CANON_ID}")
    print(f"  victim: غالب بلقيس (age {ej.get('victim_age')}) — {ej.get('city')}, {ej.get('incident_date')}")
    print(f"  sources: {len(sources)}")
    for s in sources:
        print(f"    {s.get('url')}")


if __name__ == "__main__":
    main()
