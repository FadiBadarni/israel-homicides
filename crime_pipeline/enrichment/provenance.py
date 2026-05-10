"""
Per-field fact provenance.

For every canonical fact in the case (victim_name_ar, suspect_age, weapon_type,
etc.) we record:
    - the chosen value
    - per-field confidence
    - the list of source URLs that support that value
    - which sources DISSENT (have a different value)

Each source also gets a `roles` list summarising what facts it contributed —
useful at a glance for editorial review:
    initial_report | death_confirmation | indictment_update | arabic_name |
    hebrew_name | suspect_detail | evidence | community_context | ...

Provenance is built from two inputs:
    1. The raw extraction → `merge_extraction_into_case` records contributions
       in `case["_provenance_raw"]` as merging happens.
    2. Post-hoc inference from `case.conflicts` (sources NOT in the conflict
       dict for a field are assumed to have agreed with the canonical value).
"""
from __future__ import annotations

from typing import Any

# Fields tracked in provenance
_TRACKED_FIELDS = [
    # Identity
    "victim_name_he", "victim_name_ar", "victim_name_en",
    "victim_age", "victim_gender", "victim_profession", "victim_residence",
    # Incident
    "incident_date", "death_date", "incident_date_possible", "incident_time",
    "city", "neighborhood", "district", "region",
    "exact_place_type", "hospital",
    "weapon_type", "weapon_subtype", "num_victims",
    # Suspect
    "suspect_name", "suspect_age", "suspect_relation", "suspect_profession",
    "suspect_status", "legal_status", "police_investigation_status",
    "arrest_location",
    # Context
    "motive", "organized_crime", "family_dispute", "community_context",
]

# HEADLINE fields: virtually every news article about the incident will
# mention these. Best-effort attribution to all non-dissenting sources is
# reasonable.
_HEADLINE_FIELDS = {
    "victim_name", "victim_name_he", "victim_name_ar",
    "victim_age", "victim_gender",
    "incident_date", "death_date",
    "city", "district",
    "weapon_type", "num_victims",
    "suspect_status",  # arrest is headline-grade news
    "family_dispute",  # usually framed in the headline
}

# DETAIL fields: only some sources go into this depth. Without raw
# extraction tracking we can't say WHICH source mentioned them, so we
# mark them as `attribution: unspecific` and DON'T list all sources.
_DETAIL_FIELDS = {
    "victim_name_en", "victim_profession", "victim_residence",
    "incident_date_possible", "incident_time",
    "region",  # geographic vs administrative — derived from gazetteer, not a source
    "neighborhood", "exact_place_type", "hospital",
    "weapon_subtype",
    "suspect_name", "suspect_age", "suspect_relation", "suspect_profession",
    "legal_status", "police_investigation_status",
    "arrest_location",
    "motive", "organized_crime", "community_context",
}

# Map field → role tag a source earns by contributing to it
_FIELD_TO_ROLE = {
    "victim_name_ar": "arabic_name",
    "victim_name_he": "hebrew_name",
    "victim_name_en": "english_name",
    "victim_age": "victim_detail",
    "victim_gender": "victim_detail",
    "victim_profession": "victim_detail",
    "victim_residence": "victim_detail",
    "incident_date": "incident_timing",
    "death_date": "death_confirmation",
    "incident_date_possible": "incident_timing",
    "incident_time": "incident_timing",
    "city": "location",
    "neighborhood": "location_detail",
    "district": "location",
    "region": "location",
    "hospital": "hospital_confirmation",
    "exact_place_type": "scene_detail",
    "weapon_type": "weapon_detail",
    "weapon_subtype": "weapon_detail",
    "suspect_name": "suspect_detail",
    "suspect_age": "suspect_detail",
    "suspect_relation": "suspect_detail",
    "suspect_profession": "suspect_detail",
    "suspect_status": "arrest_status",
    "legal_status": "indictment_update",
    "police_investigation_status": "investigation_update",
    "arrest_location": "arrest_detail",
    "motive": "motive_explanation",
    "organized_crime": "context_classification",
    "family_dispute": "context_classification",
    "community_context": "community_context",
}


def record_field_contribution(case: dict[str, Any], field: str,
                              value: Any, source_url: str) -> None:
    """
    Called by merge_extraction_into_case BEFORE merging a field, so we know
    the contributing source even if a conflict resolves the value to a
    different value than this source's.
    """
    if value is None or value == "" or value == [] or value == {}:
        return
    raw = case.setdefault("_provenance_raw", {})
    field_entry = raw.setdefault(field, {})
    field_entry.setdefault(str(value), [])
    if source_url not in field_entry[str(value)]:
        field_entry[str(value)].append(source_url)


def _confidence_for_supporting_sources(
    supporting: list[dict[str, Any]], dissenting: list[str], best_effort: bool = False
) -> float:
    """
    Confidence in a field's value from the number/tier of supporting sources.
    Tier diversity matters; dissent reduces confidence.
    `best_effort=True` (no raw provenance available) caps the result at 0.85
    so we don't over-claim attribution.
    """
    if not supporting:
        return 0.5
    tiers = {s.get("tier") for s in supporting if s.get("tier")}
    n = len(supporting)
    has_t1 = 1 in tiers
    has_t2 = 2 in tiers
    has_t3 = 3 in tiers

    base = min(0.6 + 0.07 * n, 0.90)
    if has_t3:
        base = min(1.0, base + 0.10)
    if has_t1 and has_t2:
        base = min(1.0, base + 0.05)
    if dissenting:
        base = max(0.4, base - 0.10 * len(dissenting))
    if best_effort:
        base = min(base, 0.85)
    return round(base, 3)


def build_provenance(case: dict[str, Any]) -> dict[str, Any]:
    """
    Build the provenance block for the canonical case.

    Strategy:
    - For each tracked field that has a value, the supporting sources are
      ALL sources that don't appear in conflicts[field].
    - Sources in conflicts[field] are dissenters (with their own value).
    - If `_provenance_raw` was populated by merge_extraction_into_case, we
      use that for more accurate per-source attribution.
    """
    sources = case.get("sources") or []
    sources_by_url = {s.get("url"): s for s in sources}
    conflicts = case.get("conflicts") or {}
    raw = case.get("_provenance_raw") or {}

    provenance: dict[str, dict[str, Any]] = {}

    for field in _TRACKED_FIELDS:
        value = case.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue

        # Dissenting URLs from the conflict dict
        conflict_dict = conflicts.get(field)
        dissenting_urls: list[str] = []
        if isinstance(conflict_dict, dict):
            dissenting_urls = list(conflict_dict.keys())

        # Supporting URLs — ONLY use raw provenance recorded at extraction time.
        # We do NOT fall back to "every non-dissenting source supports this"
        # because that conflates "source is associated with the case" with
        # "source explicitly asserted this field" — the user/Codex critique.
        is_best_effort = False
        is_unspecific = False
        if field in raw and isinstance(raw[field], dict):
            supporting_urls = list(raw[field].get(str(value), []))
            # If raw provenance was recorded for this field but doesn't list
            # any URLs for the canonical value, mark unspecific too.
            if not supporting_urls:
                is_unspecific = True
        else:
            # No raw tracking for this field — admit we don't know which
            # source(s) mentioned it. Don't fabricate attribution.
            is_unspecific = True
            supporting_urls = []

        supporting_source_objs = [
            sources_by_url[u] for u in supporting_urls if u in sources_by_url
        ]

        if is_unspecific:
            attribution_quality = "unspecific"
            confidence = 0.6
            attribution_note = (
                "Source attribution unknown — this field's raw provenance "
                "was not captured at extraction time. Value present but "
                "we cannot point to which source(s) explicitly asserted it. "
                "Re-run enrichment to populate per-field attribution."
            )
        else:
            attribution_quality = "verified"
            confidence = _confidence_for_supporting_sources(
                supporting_source_objs, dissenting_urls, best_effort=False
            )
            attribution_note = None

        entry = {
            "value": value,
            "confidence": confidence,
            "attribution_quality": attribution_quality,
            "supporting_source_count": len(supporting_urls),
            "supporting_sources": [
                {
                    "url": u,
                    "publisher": sources_by_url[u].get("actual_publisher"),
                    "tier": sources_by_url[u].get("tier"),
                    "language": sources_by_url[u].get("language"),
                }
                for u in supporting_urls if u in sources_by_url
            ],
            "dissenting_sources": [
                {
                    "url": u,
                    "publisher": sources_by_url.get(u, {}).get("actual_publisher"),
                    "value": (conflict_dict or {}).get(u),
                }
                for u in dissenting_urls
            ] if dissenting_urls else [],
        }
        if is_unspecific:
            # For unspecific detail fields, list the candidate publishers
            # (those that COULD have mentioned it) so a reviewer knows where
            # to look — without claiming any of them did.
            entry["candidate_sources"] = [
                {
                    "publisher": s.get("actual_publisher"),
                    "tier": s.get("tier"),
                    "language": s.get("language"),
                }
                for s in sources if s.get("url") not in dissenting_urls
            ]
            entry["attribution_note"] = attribution_note
        provenance[field] = entry

    return provenance


def assign_source_roles(case: dict[str, Any]) -> None:
    """
    Assign roles to each source based ONLY on raw provenance contributions
    recorded at merge time (case["_provenance_raw"]). When raw provenance
    isn't available, we fall back to assigning roles based on tier and
    publication timing — never the "every source supports every field"
    fallback (which over-attributes).
    """
    from datetime import datetime
    sources = case.get("sources") or []
    raw = case.get("_provenance_raw") or {}

    url_to_roles: dict[str, set[str]] = {s.get("url"): set() for s in sources if s.get("url")}

    # Build roles from RAW (precise) provenance first
    for field, value_to_urls in raw.items():
        role = _FIELD_TO_ROLE.get(field)
        if not role:
            continue
        if not isinstance(value_to_urls, dict):
            continue
        for urls in value_to_urls.values():
            for url in urls:
                if url in url_to_roles:
                    url_to_roles[url].add(role)

    death = None
    if case.get("death_date"):
        try:
            death = datetime.fromisoformat(str(case["death_date"])[:10])
        except Exception:
            pass

    # Conservative fallback per-source heuristics (no RAW data path):
    # - Sources published within 2 days of the death → initial_report
    # - Tier 2 (Arabic) sources → arabic_name (they're the canonical Arabic source)
    # - Tier 3 sources → official_confirmation
    # - Sources published > 2 weeks after death → follow_up
    for s in sources:
        url = s.get("url")
        if not url:
            continue
        pub = s.get("published_at")
        pub_dt = None
        if pub:
            try:
                pub_dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            except Exception:
                pass
        if pub_dt and death:
            delta_days = (pub_dt.date() - death.date()).days
            if delta_days <= 2:
                url_to_roles[url].add("initial_report")
            elif delta_days >= 14:
                url_to_roles[url].add("follow_up")
            else:
                url_to_roles[url].add("update_report")
        if s.get("language") == "ar" and s.get("tier") == 2:
            url_to_roles[url].add("arabic_local_coverage")
        if s.get("tier") == 3:
            url_to_roles[url].add("official_confirmation")

    # Indictment update: when legal_status is set and this source was published
    # >=14 days after the incident, it counts as covering the indictment news.
    if case.get("legal_status") in ("indicted", "on_trial", "convicted"):
        from datetime import datetime as _dt
        incident = case.get("incident_date") or case.get("death_date")
        if incident:
            try:
                incident_dt = _dt.fromisoformat(str(incident)[:10])
            except Exception:
                incident_dt = None
            if incident_dt:
                for s in sources:
                    url = s.get("url")
                    pub = s.get("published_at")
                    if not (url and pub):
                        continue
                    try:
                        pub_dt = _dt.fromisoformat(str(pub).replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if (pub_dt.date() - incident_dt.date()).days >= 14:
                        url_to_roles[url].add("indictment_update")

    for s in sources:
        url = s.get("url")
        if url and url in url_to_roles:
            s["roles"] = sorted(url_to_roles[url])


def apply_provenance(case: dict[str, Any]) -> dict[str, Any]:
    """Top-level entry: build provenance dict and tag sources with roles."""
    case["provenance"] = build_provenance(case)
    assign_source_roles(case)
    return case
