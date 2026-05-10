"""
Post-enrichment sanity / quality pass.

Runs after every enrichment loop to fix systemic extraction bugs:
1. Date sanity (clamp to published_at year ± 2; auto-correct typos)
2. Script purity (Arabic field must be Arabic-only; Hebrew field must be Hebrew-only)
3. Source normalization (googlenews discovery → real publisher actually_publisher)
4. District vs region split (administrative district != geographic region)
5. Legal status disambiguation (suspect_status / legal_status / police_investigation_status)
6. Multi-dimensional confidence (per-category, not single rollup)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Unicode ranges (regex-style for re module)
# ---------------------------------------------------------------------------

# Arabic block: U+0600–U+06FF (incl. Arabic Presentation Forms in NFKC)
_ARABIC_LETTER = r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]"
# Hebrew block: U+0590–U+05FF
_HEBREW_LETTER = r"[֐-׿יִ-ﭏ]"
# Latin letters
_LATIN_LETTER = r"[A-Za-z]"

_ARABIC_RE = re.compile(_ARABIC_LETTER)
_HEBREW_RE = re.compile(_HEBREW_LETTER)
_LATIN_RE = re.compile(_LATIN_LETTER)

# ---------------------------------------------------------------------------
# 1. Date sanity
# ---------------------------------------------------------------------------


def _published_year(case: dict[str, Any]) -> int | None:
    """Return the modal published_at year across sources (most common wins)."""
    from collections import Counter
    years: list[int] = []
    for s in case.get("sources") or []:
        ts = s.get("published_at")
        if not ts:
            continue
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif isinstance(ts, datetime):
                dt = ts
            else:
                continue
            years.append(dt.year)
        except Exception:
            continue
    if not years:
        return None
    # Most common year; ties broken by the earliest (min)
    return min(Counter(years), key=lambda y: (-Counter(years)[y], y))


def _coerce_date(d: Any) -> date | None:
    """Best-effort date coercion."""
    if d is None or d == "":
        return None
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except Exception:
            return None
    return None


def clamp_dates_to_published_year(case: dict[str, Any]) -> dict[str, Any]:
    """
    If incident_date / death_date / incident_date_possible has a year that is
    >= 2 years off from the dominant published_at year, swap the year.
    Also corrects values stored under ``conflicts[field]`` (so the audit
    trail doesn't keep showing pre-correction dates as live conflicts).
    Adds 'date_year_corrected' flag when a correction is applied.
    """
    py = _published_year(case)
    if py is None:
        return case

    flags: list[str] = case.setdefault("flags", [])
    conflicts = case.setdefault("conflicts", {})
    corrected = False

    for field in ("incident_date", "death_date", "incident_date_possible"):
        d = _coerce_date(case.get(field))
        if d is None:
            continue
        if abs(d.year - py) >= 2:
            try:
                fixed = d.replace(year=py)
            except ValueError:
                continue
            case[field] = fixed.isoformat()
            corrected = True
            conflicts.setdefault(f"{field}_pre_correction", []).append(d.isoformat())

    # Also clean up any stale conflict values for these date fields — they
    # were recorded *before* year correction and represent the same logical
    # date, just with the wrong year.
    for field in ("incident_date", "death_date"):
        per_source = conflicts.get(field)
        if not isinstance(per_source, dict):
            continue
        survivors: dict[str, Any] = {}
        for url, value in per_source.items():
            cd = _coerce_date(value)
            if cd is None:
                survivors[url] = value
                continue
            if abs(cd.year - py) >= 2:
                # apply same year correction
                try:
                    cd = cd.replace(year=py)
                except ValueError:
                    survivors[url] = value
                    continue
            # If the corrected value matches the canonical incident_date or
            # is one day off (incident vs death), it is NOT a conflict —
            # it is a separate timeline event. Drop it from conflicts.
            canonical = _coerce_date(case.get(field))
            if canonical and abs((cd - canonical).days) <= 7:
                continue
            survivors[url] = cd.isoformat()
        if survivors:
            conflicts[field] = survivors
        else:
            conflicts.pop(field, None)

    if corrected and "date_year_corrected" not in flags:
        flags.append("date_year_corrected")
    return case


# ---------------------------------------------------------------------------
# 2. Script purity
# ---------------------------------------------------------------------------


def _script_profile(s: str) -> dict[str, int]:
    """Count letters per script in the string."""
    if not s:
        return {"arabic": 0, "hebrew": 0, "latin": 0}
    return {
        "arabic": len(_ARABIC_RE.findall(s)),
        "hebrew": len(_HEBREW_RE.findall(s)),
        "latin": len(_LATIN_RE.findall(s)),
    }


def _is_pure(s: str, expected: str) -> bool:
    """True if string contains letters of only the expected script (or none)."""
    p = _script_profile(s)
    keys = {"arabic", "hebrew", "latin"}
    if expected not in keys:
        return True
    other_letter_count = sum(p[k] for k in keys if k != expected)
    return other_letter_count == 0 and p[expected] > 0


def enforce_script_purity(case: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that victim_name_ar contains only Arabic letters, victim_name_he only
    Hebrew letters, victim_name_en only Latin letters. If a value is mixed-script,
    move it to aliases and clear the field; add a flag.
    """
    flags: list[str] = case.setdefault("flags", [])
    aliases: list[str] = case.setdefault("aliases", [])

    rules = [
        ("victim_name_ar", "arabic"),
        ("victim_name_he", "hebrew"),
        ("victim_name_en", "latin"),
    ]

    moved_any = False
    for field, expected in rules:
        v = case.get(field)
        if not v or not isinstance(v, str):
            continue
        if not _is_pure(v, expected):
            # mixed-script — quarantine
            if v not in aliases:
                aliases.append(v)
            case[field] = None
            moved_any = True

    if moved_any and "mixed_script_name_quarantined" not in flags:
        flags.append("mixed_script_name_quarantined")

    # If a name field is empty but the primary victim_name happens to be in
    # the right script, copy it across.
    primary = case.get("victim_name")
    if primary and isinstance(primary, str):
        if not case.get("victim_name_ar") and _is_pure(primary, "arabic"):
            case["victim_name_ar"] = primary
        if not case.get("victim_name_he") and _is_pure(primary, "hebrew"):
            case["victim_name_he"] = primary
        if not case.get("victim_name_en") and _is_pure(primary, "latin"):
            case["victim_name_en"] = primary

    return case


# ---------------------------------------------------------------------------
# 3. Source normalization
# ---------------------------------------------------------------------------

_DOMAIN_TO_PUBLISHER = {
    "haaretz.co.il": "Haaretz",
    "ynet.co.il": "Ynet",
    "mako.co.il": "Mako",
    "n12.co.il": "N12",
    "walla.co.il": "Walla!",
    "kan.org.il": "Kan",
    "maariv.co.il": "Maariv",
    "globes.co.il": "Globes",
    "calcalist.co.il": "Calcalist",
    "inn.co.il": "Arutz Sheva",
    "police.gov.il": "Israel Police",
    "panet.co.il": "Panet",
    "bokra.net": "Bokra",
    "arab48.com": "Arab48",
    "alarab.com": "Al-Arab",
    "emess.co.il": "EMess",
    "13tv.co.il": "Channel 13",
    "reshet.tv": "Channel 13",
    "haaretz.com": "Haaretz English",
    "timesofisrael.com": "Times of Israel",
    "jpost.com": "Jerusalem Post",
}


def _publisher_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return "Unknown"
    for domain, name in _DOMAIN_TO_PUBLISHER.items():
        if domain in host:
            return name
    return host.split(".")[0].title() if host else "Unknown"


def normalize_sources(case: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure every source has discovery_source, actual_publisher, AND tier set.
    Sources persisted by the original (pre-enrichment) pipeline have
    source_name='googlenews' which is the discovery mechanism — this function
    rewrites them to record the real publisher and assigns a tier from the
    central tier registry (1=mainstream, 2=arabic_local, 3=official).
    """
    from crime_pipeline.scrapers.tier_registry import classify_url

    sources = case.get("sources") or []
    for s in sources:
        url = s.get("url") or ""
        # Tier registry is authoritative; fall back to legacy mapping only
        # if the registry doesn't recognize the domain.
        tier, registry_publisher = classify_url(url)
        publisher = registry_publisher or _publisher_from_url(url)
        # Set discovery_source if missing (legacy rows)
        if not s.get("discovery_source"):
            existing_name = s.get("source_name") or ""
            if existing_name in {"googlenews", "ynet", "panet", "police"}:
                s["discovery_source"] = "Google News" if existing_name == "googlenews" else existing_name
            else:
                s["discovery_source"] = "Google News"
        else:
            ds = s["discovery_source"]
            if ds == "googlenews":
                s["discovery_source"] = "Google News"
        s["actual_publisher"] = publisher
        s["source_name"] = publisher
        s["tier"] = tier  # 1 / 2 / 3 / None
    case["sources"] = sources
    return case


# ---------------------------------------------------------------------------
# 4. District vs region split
# ---------------------------------------------------------------------------

# Israeli administrative districts (six)
_DISTRICTS = {
    # value -> canonical English name
    "צפון": "Northern District",
    "Northern": "Northern District",
    "Northern District": "Northern District",
    "מחוז הצפון": "Northern District",
    "الشمال": "Northern District",

    "מרכז": "Central District",
    "Central": "Central District",
    "Central District": "Central District",
    "מחוז המרכז": "Central District",

    "דרום": "Southern District",
    "Southern": "Southern District",
    "Southern District": "Southern District",
    "مחוז הדרום": "Southern District",

    "חיפה": "Haifa District",
    "Haifa": "Haifa District",
    "Haifa District": "Haifa District",

    "תל אביב": "Tel Aviv District",
    "Tel Aviv": "Tel Aviv District",

    "ירושלים": "Jerusalem District",
    "Jerusalem": "Jerusalem District",

    "יהודה ושומרון": "Judea and Samaria",
}

# Geographic regions (broader than districts; e.g. Galilee spans multiple districts)
_REGIONS = {
    "גליל": "Galilee",
    "הגליל": "Galilee",
    "גליל עליון": "Upper Galilee",
    "גליל תחתון": "Lower Galilee",
    "Galilee": "Galilee",
    "الجليل": "Galilee",

    "נגב": "Negev",
    "Negev": "Negev",
    "النقب": "Negev",

    "שרון": "Sharon",
    "Sharon": "Sharon",

    "כרמל": "Carmel",
    "Carmel": "Carmel",

    "עמק": "The Valleys",
    "Jordan Valley": "Jordan Valley",
    "בקעת הירדן": "Jordan Valley",
}


def split_district_and_region(case: dict[str, Any]) -> dict[str, Any]:
    """
    Determine whether a value in `district` is actually a region, and split.
    Examples:
      district='צפון' → district='Northern District' (admin)
      district='גליל' → district=None, region='Galilee'
      both 'צפון' and 'גליל' coexisting → district='Northern District', region='Galilee'
    """
    flags: list[str] = case.setdefault("flags", [])
    conflicts = case.setdefault("conflicts", {})

    # Pull current district and any conflicting district values
    raw_district = case.get("district")
    district_conflicts = conflicts.get("district") or {}

    # Build candidate set
    candidates: list[str] = []
    if raw_district:
        candidates.append(raw_district)
    for v in district_conflicts.values():
        if v and v not in candidates:
            candidates.append(v)

    detected_district: str | None = None
    detected_region: str | None = None

    for c in candidates:
        if c in _DISTRICTS and not detected_district:
            detected_district = _DISTRICTS[c]
        if c in _REGIONS and not detected_region:
            detected_region = _REGIONS[c]

    # If `region` already populated by extractor, prefer the existing canonical name
    if case.get("region") and case["region"] in _REGIONS:
        detected_region = _REGIONS[case["region"]]

    # Apply
    if detected_district:
        case["district"] = detected_district
    if detected_region:
        case["region"] = detected_region

    # Remove the district conflict if it was actually a district vs region
    # mismatch (not a real conflict).
    if "district" in conflicts:
        new_conflict_dict: dict[str, Any] = {}
        for url, val in conflicts["district"].items():
            if val in _DISTRICTS or val in _REGIONS:
                # benign — granularity difference, not a contradiction
                continue
            new_conflict_dict[url] = val
        if new_conflict_dict:
            conflicts["district"] = new_conflict_dict
        else:
            conflicts.pop("district", None)
            # Also drop the date_conflict_possible flag if it was triggered
            # *only* by district granularity; we cannot tell here, so leave flags alone

    return case


# ---------------------------------------------------------------------------
# 5. Legal status disambiguation
# ---------------------------------------------------------------------------

# Mapping from the legacy single-axis police_status values to the three new axes.
_LEGACY_POLICE_STATUS_MAP: dict[str, dict[str, str]] = {
    "investigation_open": {"police_investigation_status": "open"},
    "suspect_identified": {"police_investigation_status": "suspect_identified"},
    "arrested": {"suspect_status": "arrested",
                 "police_investigation_status": "suspect_identified"},
    "indictment_pending": {"police_investigation_status": "completed",
                           "legal_status": "pre_indictment"},
    "charged": {"police_investigation_status": "completed",
                "legal_status": "indicted",
                "suspect_status": "in_custody"},
    "trial": {"legal_status": "on_trial"},
    "closed_solved": {"police_investigation_status": "closed",
                      "legal_status": "convicted"},
    "closed_unsolved": {"police_investigation_status": "closed"},
}


def disambiguate_legal_status(case: dict[str, Any]) -> dict[str, Any]:
    """
    Split the legacy `police_status` (single axis) into:
      - suspect_status (physical)
      - legal_status (proceedings)
      - police_investigation_status (case state)

    Also normalizes the suspect_status values that came in from the extractor
    along the legal-process axis ('charged', 'convicted') to their proper axis.
    """
    legacy = case.get("police_status")
    if legacy and isinstance(legacy, str):
        mapping = _LEGACY_POLICE_STATUS_MAP.get(legacy.lower())
        if mapping:
            for k, v in mapping.items():
                if not case.get(k):
                    case[k] = v

    # Cross-axis correction: if suspect_status is on the legal axis, move it
    susp = case.get("suspect_status")
    if susp == "charged":
        if not case.get("legal_status"):
            case["legal_status"] = "indicted"
        case["suspect_status"] = "in_custody"
    elif susp == "convicted":
        if not case.get("legal_status"):
            case["legal_status"] = "convicted"
        case["suspect_status"] = "in_custody"

    return case


# ---------------------------------------------------------------------------
# 5b. City flag resolution
# ---------------------------------------------------------------------------


def resolve_city_flag(case: dict[str, Any]) -> dict[str, Any]:
    """
    Re-evaluate city:unknown_locality after enrichment.

    The flag is set at merge time when the city string is not in the gazetteer.
    After a new gazetteer entry is added, re-running this pass should clear the
    flag and populate city_normalized so confidence and location_detail reflect
    the improvement.
    """
    flags: list[str] = case.setdefault("flags", [])
    city = case.get("city")
    if not city:
        return case
    from crime_pipeline.utils.gazetteer import normalize_city
    record = normalize_city(city)
    if record is not None:
        if "city:unknown_locality" in flags:
            flags.remove("city:unknown_locality")
        # Populate city_normalized if missing or stale
        if not case.get("city_normalized") or not case["city_normalized"].get("name_en"):
            case["city_normalized"] = {k: v for k, v in dict(record).items() if v}
        # Backfill district and region from gazetteer when null on the case itself
        if not case.get("district") and record.get("district"):
            case["district"] = _DISTRICTS.get(record["district"], record["district"])
        if not case.get("region") and record.get("region"):
            case["region"] = _REGIONS.get(record["region"], record["region"])
    return case


# ---------------------------------------------------------------------------
# 6. Multi-dimensional confidence
# ---------------------------------------------------------------------------


def _has(case: dict[str, Any], *fields: str) -> int:
    return sum(1 for f in fields if not _empty(case.get(f)))


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def compute_category_confidence(case: dict[str, Any]) -> dict[str, float]:
    """
    Score each information category from 0.0 to 1.0 based on field coverage,
    multi-source corroboration, and outstanding flags.
    """
    sources = case.get("sources") or []
    n_sources = len(sources)
    flags = set(case.get("flags") or [])
    has_arabic_source = any(s.get("language") == "ar" for s in sources)
    tiers_present = {s.get("tier") for s in sources if s.get("tier")}
    has_t1 = 1 in tiers_present
    has_t2 = 2 in tiers_present
    has_t3 = 3 in tiers_present

    # ---- case_identity: incident exists, is identified, deduplicated ----
    ident_fields = _has(case, "incident_date", "city", "num_victims", "canonical_case_id")
    case_identity = min(1.0, ident_fields / 4.0 + (0.1 if n_sources >= 2 else 0))
    if "date_year_corrected" in flags:
        case_identity *= 0.95  # slight penalty: data needed correction

    # ---- victim_identity: who was the victim ----
    name_fields = _has(case, "victim_name_ar", "victim_name_he", "victim_name_en")
    other_fields = _has(case, "victim_age", "victim_gender", "victim_profession",
                        "victim_residence")
    aliases_count = len(case.get("aliases") or [])
    victim_identity = min(1.0, (name_fields / 3.0) * 0.5 + (other_fields / 4.0) * 0.5)
    # Boost: multilingual confirmation (Arabic AND Hebrew names both present)
    if case.get("victim_name_ar") and case.get("victim_name_he"):
        victim_identity = min(1.0, victim_identity + 0.15)
    # Boost: alias variants found (proves cross-source corroboration)
    if aliases_count >= 1:
        victim_identity = min(1.0, victim_identity + 0.05 * min(aliases_count, 3))
    if "mixed_script_name_quarantined" in flags:
        victim_identity *= 0.9

    # ---- timeline: when did this happen ----
    timeline_fields = _has(case, "incident_date", "death_date", "incident_time")
    timeline = min(1.0, timeline_fields / 3.0)
    # Penalty applies only when timeline contradiction is unresolved.
    # Once death_date and incident_date_possible are coherent (incident
    # before death), the date_conflict is informative, not contradictory.
    incident_dt_local = _coerce_date(case.get("incident_date"))
    death_dt_local = _coerce_date(case.get("death_date"))
    poss_dt_local = _coerce_date(case.get("incident_date_possible"))
    timeline_coherent = (
        incident_dt_local and death_dt_local and incident_dt_local <= death_dt_local
        and (not poss_dt_local or poss_dt_local <= incident_dt_local)
    )
    if "date_conflict_possible" in flags and not timeline_coherent:
        timeline *= 0.6
    if poss_dt_local and not timeline_coherent:
        timeline *= 0.85
    # Boost: timeline list itself is rich
    if len(case.get("timeline") or []) >= 4:
        timeline = min(1.0, timeline + 0.15)

    # ---- legal_status: where in the legal process ----
    # Multiple sources reporting an arrest is itself meaningful legal
    # information — even if legal_status (indictment level) isn't set yet.
    legal_populated = sum(1 for f in
        ("suspect_status", "legal_status", "police_investigation_status",
         "suspect_name", "arrest_location")
        if not _empty(case.get(f)))
    legal = min(1.0, legal_populated / 5.0)
    t1_count = sum(1 for s in sources if s.get("tier") == 1)
    t2_count = sum(1 for s in sources if s.get("tier") == 2)
    # Boost by source corroboration of suspect_status alone (the most
    # commonly populated legal axis). Each pair of sources adds +0.05.
    if case.get("suspect_status"):
        legal = min(1.0, legal + 0.05 * min(n_sources, 6))
    # Cross-tier confirmation boost
    if t1_count >= 2 and t2_count >= 1:
        legal = min(1.0, legal + 0.1)
    if has_t3:
        legal = min(1.0, legal + 0.10)
    elif t1_count + t2_count >= 4:
        # Strong cross-tier confirmation without Tier 3 — cap at 0.85
        legal = min(legal, 0.85)
    else:
        legal = min(legal, 0.65)

    # ---- location_detail: how granular is the location ----
    # Tier 2 (Arabic/local) is authoritative for neighborhood + community;
    # without Tier 2 we cap micro-location detail.
    loc_fields = _has(case, "city", "neighborhood", "district", "region",
                      "exact_place_type", "hospital")
    location_detail = min(1.0, loc_fields / 6.0)
    if not has_t2:
        location_detail = min(location_detail, 0.7)

    # ---- media: media inventory richness ----
    media_count = len(case.get("media") or [])
    if media_count == 0:
        media = 0.05
    elif media_count == 1:
        media = 0.4
    elif media_count <= 3:
        media = 0.7
    else:
        media = min(1.0, 0.7 + 0.05 * (media_count - 3))
    if "needs_media_extraction" in flags:
        media = min(media, 0.3)

    return {
        "case_identity": round(case_identity, 3),
        "victim_identity": round(victim_identity, 3),
        "timeline": round(timeline, 3),
        "legal_status": round(legal, 3),
        "location_detail": round(location_detail, 3),
        "media": round(media, 3),
    }


def apply_confidence(case: dict[str, Any]) -> dict[str, Any]:
    """Compute and store the per-category confidence dict + rollup score.

    Also writes the tier coverage summary and adds needs_tier_N flags so
    downstream enrichment can target the gap.
    """
    from crime_pipeline.scrapers.tier_registry import coverage_gaps

    cat = compute_category_confidence(case)
    case["confidence"] = cat
    weights = {
        "case_identity": 0.25,
        "victim_identity": 0.20,
        "timeline": 0.15,
        "legal_status": 0.15,
        "location_detail": 0.15,
        "media": 0.10,
    }
    rollup = sum(cat[k] * w for k, w in weights.items())
    case["confidence_score"] = round(min(1.0, rollup), 3)

    # ---- Tier coverage summary ----
    sources = case.get("sources") or []
    tier_breakdown: dict[str, list[str]] = {"tier_1": [], "tier_2": [], "tier_3": [], "untiered": []}
    for s in sources:
        t = s.get("tier")
        publisher = s.get("actual_publisher", "Unknown")
        if t == 1:
            tier_breakdown["tier_1"].append(publisher)
        elif t == 2:
            tier_breakdown["tier_2"].append(publisher)
        elif t == 3:
            tier_breakdown["tier_3"].append(publisher)
        else:
            tier_breakdown["untiered"].append(publisher)
    case["tier_coverage"] = tier_breakdown

    # ---- Tier-coverage flags ----
    flags: list[str] = case.setdefault("flags", [])
    # Remove any prior tier-needs flags so we recompute fresh
    flags[:] = [f for f in flags if not f.startswith("needs_tier_")]
    for gap in coverage_gaps(case):
        flags.append(gap)

    return case


# ---------------------------------------------------------------------------
# 7. Timeline construction
# ---------------------------------------------------------------------------


def _add_event(events: list[dict[str, Any]], seen: set[tuple[str, str]],
               date_str: str, label: str, source_url: str | None = None,
               confidence: str = "high") -> None:
    key = (date_str, label)
    if key in seen:
        return
    seen.add(key)
    event = {"date": date_str, "event": label, "confidence": confidence}
    if source_url:
        event["source_url"] = source_url
    events.append(event)


def build_timeline(case: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Synthesize a chronological timeline of events from the case fields.

    Sources of timeline events:
      - incident_date_possible (most-likely actual incident date)
      - incident_date (when reported / death notice)
      - death_date (if distinct)
      - sources[].published_at + actual_publisher  → "Reported in X"
      - legal_status transitions ("indicted" → indictment event)
    """
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    incident_dt = _coerce_date(case.get("incident_date"))
    poss_dt = _coerce_date(case.get("incident_date_possible"))
    death_dt = _coerce_date(case.get("death_date"))

    # 1. Best-known incident date — prefer incident_date_possible if it is
    # earlier than incident_date (incident often precedes the news cycle).
    earliest_incident = None
    if poss_dt and incident_dt:
        earliest_incident = min(poss_dt, incident_dt)
    else:
        earliest_incident = poss_dt or incident_dt

    if earliest_incident:
        _add_event(events, seen, earliest_incident.isoformat(),
                   "Incident occurred", confidence="medium" if poss_dt else "high")

    # 2. Death reported (distinct from the incident if dates differ)
    if death_dt and death_dt != earliest_incident:
        _add_event(events, seen, death_dt.isoformat(), "Victim death reported")
    elif incident_dt and earliest_incident and incident_dt != earliest_incident:
        _add_event(events, seen, incident_dt.isoformat(),
                   "Victim death reported")

    # 3. Article publications — one event per unique published_at
    sources_by_date: dict[str, list[dict[str, Any]]] = {}
    for s in case.get("sources") or []:
        pub = s.get("published_at")
        if not pub:
            continue
        try:
            d = pub[:10] if isinstance(pub, str) else pub.isoformat()[:10]
        except Exception:
            continue
        sources_by_date.setdefault(d, []).append(s)

    legal_emitted = False
    for d_str, srcs in sorted(sources_by_date.items()):
        for s in srcs:
            publisher = s.get("actual_publisher") or s.get("source_name") or "media"
            label = f"Reported in {publisher}"
            _add_event(events, seen, d_str, label, source_url=s.get("url"))

            # If this is the latest source AND a legal_status indicating
            # indictment is set, attribute the indictment event to this source.
            if (case.get("legal_status") in ("indicted", "on_trial", "convicted")
                    and not legal_emitted and d_str == max(sources_by_date.keys())):
                legal_label_map = {
                    "indicted": "Suspect indicted",
                    "on_trial": "Trial began",
                    "convicted": "Conviction reported",
                }
                _add_event(
                    events, seen, d_str,
                    legal_label_map[case["legal_status"]],
                    source_url=s.get("url"),
                )
                legal_emitted = True

    # 4. Sort chronologically
    events.sort(key=lambda e: e["date"])
    return events


def apply_timeline(case: dict[str, Any]) -> dict[str, Any]:
    """Compute and store the timeline list on the case."""
    case["timeline"] = build_timeline(case)
    return case


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def run_sanity_pass(case: dict[str, Any]) -> dict[str, Any]:
    """Apply all sanity passes in dependency order."""
    case = normalize_sources(case)
    case = clamp_dates_to_published_year(case)
    case = enforce_script_purity(case)
    case = split_district_and_region(case)
    case = disambiguate_legal_status(case)
    case = resolve_city_flag(case)
    case = apply_timeline(case)
    case = apply_confidence(case)
    return case
