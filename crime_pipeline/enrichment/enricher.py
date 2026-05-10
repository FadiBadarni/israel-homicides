"""
Second-pass case enrichment.

Given a partially-extracted canonical case from the first pipeline pass,
this module:

1. Generates targeted search queries derived from the case's known fields
   (victim names in multiple scripts, neighborhood, suspect relation, etc.)
2. Re-runs discovery + fetch + extract on those queries
3. Merges the new findings into the existing canonical record (additive
   merge — fills nulls, extends lists, accumulates sources)

The key principle: enrichment is *additive*. It never overwrites high-confidence
existing data; it fills gaps and accumulates corroborating sources.
"""
from __future__ import annotations

import asyncio
import json
import re
import structlog
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crime_pipeline.extraction.extractor import ArticleExtractor
from crime_pipeline.scrapers import get_scraper

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def _victim_name_variants(case: dict[str, Any]) -> list[str]:
    """Return distinct victim-name strings to search on."""
    variants = []
    for key in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        v = case.get(key)
        if v and v not in variants:
            variants.append(v)
    for alias in case.get("aliases") or []:
        if alias and alias not in variants:
            variants.append(alias)
    return variants


def _city_variants(case: dict[str, Any]) -> list[str]:
    """Return distinct city-name strings."""
    out = []
    if case.get("city"):
        out.append(case["city"])
    norm = case.get("city_normalized") or {}
    for k in ("name_ar", "name_he", "name_en"):
        v = norm.get(k)
        if v and v not in out:
            out.append(v)
    return out


def generate_enrichment_queries(case: dict[str, Any], max_queries: int = 8) -> list[str]:
    """
    Build a prioritised list of targeted search queries from a canonical case.

    Strategy:
    - Tier 1: victim name + city (in each script)
    - Tier 2: victim name + crime keyword
    - Tier 3: contextual queries (neighborhood, suspect relation + profession)
    - Tier 4: investigation/follow-up queries

    Returns at most ``max_queries`` queries, deduplicated.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if q and q not in seen and len(q) < 200:
            queries.append(q)
            seen.add(q)

    names = _victim_name_variants(case)
    cities = _city_variants(case)

    # Tier 1: victim name + city, per language
    for name in names:
        is_arabic = bool(re.search(r"[؀-ۿ]", name))
        is_hebrew = bool(re.search(r"[֐-׿]", name))
        for city in cities:
            city_arabic = bool(re.search(r"[؀-ۿ]", city))
            city_hebrew = bool(re.search(r"[֐-׿]", city))
            # Prefer same-script pairing
            if (is_arabic and city_arabic) or (is_hebrew and city_hebrew) or \
               (not is_arabic and not is_hebrew):
                add(f'"{name}" {city}')

    # Tier 2: victim name + crime keyword (Hebrew/Arabic)
    for name in names:
        is_arabic = bool(re.search(r"[؀-ۿ]", name))
        is_hebrew = bool(re.search(r"[֐-׿]", name))
        if is_arabic:
            add(f'"{name}" قتل')
            add(f'"{name}" جريمة')
        elif is_hebrew:
            add(f'"{name}" רצח')
            add(f'"{name}" חשד לרצח')

    # Tier 3: neighborhood-anchored queries
    if case.get("neighborhood"):
        for city in cities:
            add(f'{case["neighborhood"]} {city}')

    # Tier 4: suspect-relation contextual queries
    relation = case.get("suspect_relation")
    profession = case.get("suspect_profession")
    if relation or profession:
        for city in cities:
            city_arabic = bool(re.search(r"[؀-ۿ]", city))
            city_hebrew = bool(re.search(r"[֐-׿]", city))
            if relation == "brother":
                if city_hebrew:
                    add(f'רצח אחיו {city}')
                if city_arabic:
                    add(f'قتل شقيقه {city}')
            if profession:
                profession_lower = profession.lower()
                if "doctor" in profession_lower or "רופא" in profession or "طبيب" in profession:
                    if city_hebrew:
                        add(f'רופא רצח אחיו {city}')
                    if city_arabic:
                        add(f'طبيب قتل شقيقه {city}')

    # Tier 5: investigation follow-up
    for name in names:
        is_hebrew = bool(re.search(r"[֐-׿]", name))
        is_arabic = bool(re.search(r"[؀-ۿ]", name))
        if is_hebrew:
            add(f'"{name}" כתב אישום')
            add(f'"{name}" משטרה חקירה')
        if is_arabic:
            add(f'"{name}" لائحة اتهام')

    return queries[:max_queries]


def generate_arabic_enrichment_queries(case: dict[str, Any], max_queries: int = 8) -> list[str]:
    """
    Generate Arabic-only queries that target fields the Hebrew sources didn't fill:
    victim_name_ar, neighborhood, hospital, community_context, victim_portrait,
    funeral_details. Uses the suspect/incident facts known so far to seed the
    Arabic search.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if q and q not in seen and len(q) < 200:
            queries.append(q)
            seen.add(q)

    # Arabic city variants
    norm = case.get("city_normalized") or {}
    city_ar = norm.get("name_ar") or ""
    city_en = norm.get("name_en") or ""
    region = case.get("region") or ""

    # Hebrew name (we have this) — Arabic sources transliterate it back
    he_name = case.get("victim_name_he") or case.get("victim_name") or ""

    incident_year = None
    if case.get("incident_date"):
        try:
            incident_year = case["incident_date"][:4]
        except Exception:
            incident_year = None

    # Tier 1: city + crime keywords + year (find the case in Arabic media)
    if city_ar:
        if incident_year:
            add(f'جريمة قتل {city_ar} {incident_year}')
            add(f'مقتل {city_ar} {incident_year}')
        add(f'إطلاق نار {city_ar}')
        add(f'ضحية {city_ar}')

    # Tier 2: suspect/relation context (in Arabic) — narrows to the right case
    relation = case.get("suspect_relation") or ""
    profession = case.get("suspect_profession") or ""
    if relation in ("אח", "brother") or "أخ" in relation:
        if city_ar:
            add(f'قتل شقيقه {city_ar}')
            add(f'جريمة شقيقين {city_ar}')
    if profession in ("רופא", "doctor") or "طبيب" in profession:
        if city_ar:
            add(f'طبيب قتل شقيقه {city_ar}')
            add(f'الطبيب من {city_ar}')

    # Tier 3: funeral / memorial / community-impact queries
    if city_ar:
        add(f'جنازة {city_ar}')
        add(f'تشييع {city_ar}')
        if region == "Galilee":
            add(f'ضحايا الجريمة في المجتمع العربي {city_ar}')
        add(f'وادي {city_ar}')  # neighborhood probe
    if city_en:
        # The English name is sometimes used in Arabic-language headlines
        add(f'{city_en} ضحية')

    # Tier 4: legal-process Arabic terms
    if case.get("legal_status") == "indicted" and city_ar:
        add(f'لائحة اتهام {city_ar}')
        add(f'تقديم للمحاكمة {city_ar}')

    return queries[:max_queries]


def generate_tier3_official_queries(case: dict[str, Any], max_queries: int = 6) -> list[str]:
    """
    Tier 3 — official sources (police.gov.il, courts).
    Built using site: prefixes so Google News surfaces only Tier 3 hosts.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if q and q not in seen and len(q) < 200:
            queries.append(q)
            seen.add(q)

    he_name = case.get("victim_name_he") or ""
    ar_name = case.get("victim_name_ar") or ""
    norm = case.get("city_normalized") or {}
    city_he = norm.get("name_he") or case.get("city") or ""
    city_ar = norm.get("name_ar") or ""

    # Direct domain queries — Google News supports site: filters
    if he_name and city_he:
        add(f'site:police.gov.il רצח "{he_name}"')
        add(f'site:police.gov.il "{city_he}" רצח')
        add(f'site:gov.il "{he_name}"')
    if ar_name and city_ar:
        add(f'site:police.gov.il {city_ar} {ar_name}')

    # Court / indictment queries
    if he_name:
        add(f'site:court.gov.il "{he_name}"')
        add(f'"{he_name}" כתב אישום משטרה')

    return queries[:max_queries]


def generate_tier1_mainstream_queries(case: dict[str, Any], max_queries: int = 6) -> list[str]:
    """
    Tier 1 — mainstream news. Hebrew-language confirmed-fact queries that
    bias toward Ynet / Mako / Haaretz / Walla / Israel Hayom / Kan via
    site: filters.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if q and q not in seen and len(q) < 200:
            queries.append(q)
            seen.add(q)

    he_name = case.get("victim_name_he") or case.get("victim_name") or ""
    norm = case.get("city_normalized") or {}
    city_he = norm.get("name_he") or case.get("city") or ""

    if he_name:
        for site in ("ynet.co.il", "mako.co.il", "haaretz.co.il",
                     "walla.co.il", "israelhayom.co.il", "n12.co.il", "kan.org.il"):
            add(f'site:{site} "{he_name}"')

    if city_he and case.get("incident_date"):
        year = case["incident_date"][:4]
        add(f'"{city_he}" רצח {year}')

    return queries[:max_queries]


def generate_tier2_arabic_local_queries(case: dict[str, Any], max_queries: int = 6) -> list[str]:
    """
    Tier 2 — Arabic / local press, biased via site: filters to surface
    Arab48 / Panet / Kul al-Arab / Bokra. These are the richest source
    of victim detail (full name, neighborhood, family, funeral).
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if q and q not in seen and len(q) < 200:
            queries.append(q)
            seen.add(q)

    ar_name = case.get("victim_name_ar") or ""
    norm = case.get("city_normalized") or {}
    city_ar = norm.get("name_ar") or ""

    if ar_name:
        for site in ("arab48.com", "panet.com", "kul-alarab.com",
                     "bokra.net", "alarab.com"):
            add(f'site:{site} "{ar_name}"')
    if city_ar:
        add(f'site:arab48.com "{city_ar}" قتل')
        add(f'site:panet.com "{city_ar}" جريمة')
        if ar_name:
            add(f'site:kul-alarab.com {ar_name} {city_ar}')
            add(f'site:arab48.com {ar_name} جنازة')

    return queries[:max_queries]


def generate_queries_for_tier(
    case: dict[str, Any], tier: int, max_queries: int = 6
) -> list[str]:
    """Dispatcher: return queries targeted at the given tier."""
    if tier == 1:
        return generate_tier1_mainstream_queries(case, max_queries)
    if tier == 2:
        return generate_tier2_arabic_local_queries(case, max_queries)
    if tier == 3:
        return generate_tier3_official_queries(case, max_queries)
    # Fallback: default mixed-strategy
    return generate_enrichment_queries(case, max_queries)


# ---------------------------------------------------------------------------
# Additive merge logic
# ---------------------------------------------------------------------------


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _merge_value(existing: Any, new: Any, field: str, conflicts: dict[str, Any],
                 source_url: str) -> Any:
    """
    Additive single-field merge.
    - If existing is empty, take new.
    - If new is empty, keep existing.
    - If both present and equal, keep existing.
    - If both present and different, keep existing but record in conflicts dict.
    """
    if _is_empty(new):
        return existing
    if _is_empty(existing):
        return new
    if existing == new:
        return existing
    # Conflict — record it
    conflicts.setdefault(field, {})
    conflicts[field][source_url] = new
    return existing


def _extend_unique(target: list, items: list) -> list:
    """Extend a list with new items, preserving order and uniqueness."""
    if not items:
        return target
    out = list(target)
    seen = set()
    for x in out:
        try:
            seen.add(json.dumps(x, sort_keys=True, ensure_ascii=False))
        except Exception:
            seen.add(str(x))
    for item in items:
        try:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        except Exception:
            key = str(item)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _infer_publisher(url: str) -> str:
    """Infer the actual publisher from a URL host."""
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return "unknown"
    domain_map = {
        "haaretz.co.il": "haaretz",
        "ynet.co.il": "ynet",
        "mako.co.il": "mako",
        "n12.co.il": "n12",
        "walla.co.il": "walla",
        "kan.org.il": "kan",
        "maariv.co.il": "maariv",
        "globes.co.il": "globes",
        "calcalist.co.il": "calcalist",
        "inn.co.il": "arutz7",
        "police.gov.il": "police",
        "panet.com": "panet",
        "bokra.net": "bokra",
        "arab48.com": "arab48",
        "alarab.com": "alarab",
        "emess.co.il": "emess",
        "13tv.co.il": "channel13",
        "reshet.tv": "channel13",
    }
    for domain, name in domain_map.items():
        if domain in host:
            return name
    return host.split(".")[0] if host else "unknown"


def _build_canonical_id(case: dict[str, Any]) -> str | None:
    """Build the canonical case ID per the user's spec."""
    parts = ["IL", "HOMICIDE"]
    if case.get("incident_date") or case.get("death_date"):
        d = str(case.get("incident_date") or case.get("death_date"))
        parts.append(d[:4])  # year
    norm = case.get("city_normalized") or {}
    city_en = norm.get("name_en") or case.get("city")
    if city_en:
        parts.append(city_en.upper().replace(" ", "-"))
    if case.get("incident_date") or case.get("death_date"):
        parts.append(str(case.get("incident_date") or case.get("death_date")))
    name_en = case.get("victim_name_en") or _romanize_for_id(
        case.get("victim_name_he") or case.get("victim_name_ar") or case.get("victim_name") or ""
    )
    if name_en:
        parts.append(name_en.upper().replace(" ", "-"))
    return "-".join(parts) if len(parts) >= 4 else None


def _romanize_for_id(name: str) -> str:
    try:
        from anyascii import anyascii
        return re.sub(r"[^A-Za-z\s]", "", anyascii(name)).strip()
    except Exception:
        return ""


def is_same_incident(
    case: dict[str, Any], extraction: dict[str, Any]
) -> tuple[bool, str]:
    """
    Decide whether a new extraction describes the SAME incident as the
    canonical case. Used as a gate before additive merging to prevent
    cross-incident contamination (e.g. multiple Arraba 2026 homicides).

    Returns (matches, reason). reason is empty on match, descriptive on reject.

    Rules:
    - City MUST match (via name set comparison across scripts) — always true
      for our enrichment because we search by city, but defensive.
    - Victim name MUST match if both have one: Jaro-Winkler ≥ 0.70 on
      romanized form, OR the new name appears in case.aliases, OR same
      first-OR-last name token.
    - Incident date MUST be within ±5 days of case.incident_date if both have one.
    """
    from crime_pipeline.dedup.name_normalizer import (
        jaro_winkler_similarity, romanize_name,
    )

    # ---- City check ----
    case_cities: set[str] = set()
    if case.get("city"):
        case_cities.add(case["city"])
    norm = case.get("city_normalized") or {}
    for k in ("name_ar", "name_he", "name_en"):
        v = norm.get(k)
        if v:
            case_cities.add(v)
    new_city = extraction.get("city")
    if new_city and case_cities and new_city not in case_cities:
        # 1. Gazetteer check: if both new and existing resolve to the same
        # canonical city record, they're the same place at different
        # specificity (e.g. "عرابة البطوف" → Arraba).
        from crime_pipeline.utils.gazetteer import normalize_city
        new_record = normalize_city(new_city)
        case_records = {
            (r.get("name_en") or "") for r in
            (normalize_city(c) or {} for c in case_cities)
            if r
        }
        if new_record and (new_record.get("name_en") in case_records):
            pass  # gazetteer-validated match
        else:
            # 2. Fall back to romanized comparison
            rom_new = romanize_name(new_city)
            rom_case = {romanize_name(c) for c in case_cities}
            if rom_new and rom_new not in rom_case:
                return False, f"city_mismatch: '{new_city}' vs {case_cities}"

    # ---- Name check ----
    case_names = [
        case.get("victim_name"),
        case.get("victim_name_ar"),
        case.get("victim_name_he"),
        case.get("victim_name_en"),
    ]
    case_names = [n for n in case_names if n]
    new_name = (
        extraction.get("victim_name")
        or extraction.get("victim_name_ar")
        or extraction.get("victim_name_he")
        or extraction.get("victim_name_en")
    )

    if new_name and case_names:
        # Check against aliases
        aliases = case.get("aliases") or []
        if new_name in aliases:
            pass  # match via alias
        else:
            # Compute max Jaro-Winkler across all known names
            scores = [jaro_winkler_similarity(new_name, n) for n in case_names]
            best = max(scores) if scores else 0.0
            if best < 0.70:
                # Last-chance: token overlap on romanized form
                rom_new_tokens = set(romanize_name(new_name).split())
                rom_case_tokens: set[str] = set()
                for n in case_names:
                    rom_case_tokens.update(romanize_name(n).split())
                # require at least one shared token of length >= 3
                shared = {t for t in rom_new_tokens & rom_case_tokens if len(t) >= 3}
                if not shared:
                    return False, (
                        f"name_mismatch: '{new_name}' vs {case_names} "
                        f"(jaro_max={best:.2f}, no shared tokens)"
                    )

    # ---- Date check ----
    case_dt = _coerce_date(
        case.get("incident_date") or case.get("incident_date_possible")
    )
    new_dt = _coerce_date(extraction.get("incident_date"))
    if case_dt and new_dt:
        # Apply the same year-correction that sanity_pass would apply post-merge.
        # The LLM occasionally outputs the wrong year for relative dates;
        # if the year is way off, swap to the case's year before comparing.
        if abs(new_dt.year - case_dt.year) >= 2:
            try:
                new_dt = new_dt.replace(year=case_dt.year)
            except ValueError:
                pass
        delta = abs((new_dt - case_dt).days)
        if delta > 30:  # generous window for follow-up reports
            return False, f"date_mismatch: {new_dt} vs {case_dt} ({delta} days apart)"

    return True, ""


def _coerce_date(d: Any) -> Any:
    """Local re-export of sanity_pass._coerce_date to avoid an import cycle."""
    from datetime import date as _date
    if d is None or d == "":
        return None
    if isinstance(d, _date):
        return d
    if isinstance(d, str):
        try:
            return _date.fromisoformat(d[:10])
        except Exception:
            return None
    return None


def merge_extraction_into_case(
    case: dict[str, Any],
    extraction: dict[str, Any],
    source_url: str,
    discovery_source: str,
    language: str,
    published_at: datetime | None,
    paywalled: bool = False,
    body_extracted: bool = True,
) -> dict[str, Any]:
    """Additively merge a single new extraction into the canonical case dict.

    Gated by ``is_same_incident()`` — extractions that don't pass the identity
    check are recorded under ``rejected_unrelated_articles`` and DO NOT
    contribute to the merged record.
    """
    matches, reason = is_same_incident(case, extraction)
    if not matches:
        rejected = case.setdefault("rejected_unrelated_articles", [])
        rejected.append({
            "url": source_url,
            "reason": reason,
            "victim_name_extracted": (
                extraction.get("victim_name")
                or extraction.get("victim_name_ar")
                or extraction.get("victim_name_he")
            ),
            "incident_date_extracted": str(extraction.get("incident_date") or ""),
            "discovery_source": discovery_source,
            "language": language,
        })
        log.info(
            "merge_rejected_unrelated_incident",
            url=source_url, reason=reason,
        )
        return case

    conflicts = case.setdefault("conflicts", {})
    flags = case.setdefault("flags", [])

    # Set up raw provenance recording — every field this extraction populates
    # gets attributed to source_url in case["_provenance_raw"][field][value].
    from crime_pipeline.enrichment.provenance import record_field_contribution

    def _record(field: str, value: Any) -> None:
        if value is not None and value != "" and value != [] and value != {}:
            record_field_contribution(case, field, value, source_url)

    # ---- Multilingual names ----
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        new_val = extraction.get(k)
        case[k] = _merge_value(case.get(k), new_val, k, conflicts, source_url)
        _record(k, new_val)

    # ---- Aliases (additive) ----
    new_aliases = list(extraction.get("victim_aliases") or [])
    # Also pull names that don't match the existing ones into aliases
    existing_names = {case.get(k) for k in ("victim_name", "victim_name_ar",
                                             "victim_name_he", "victim_name_en")
                       if case.get(k)}
    for nm in (extraction.get("victim_name"), extraction.get("victim_name_ar"),
               extraction.get("victim_name_he"), extraction.get("victim_name_en")):
        if nm and nm not in existing_names:
            new_aliases.append(nm)
    case["aliases"] = _extend_unique(case.get("aliases") or [], new_aliases)

    # ---- Scalar fields (priority merge + provenance record) ----
    for k in ("victim_age", "victim_gender", "victim_profession", "victim_residence",
              "death_date", "incident_date", "incident_time",
              "city", "neighborhood", "exact_place_type", "district", "region", "hospital",
              "weapon_type", "weapon_subtype",
              "suspect_name", "suspect_age", "suspect_relation", "suspect_status",
              "legal_status", "police_investigation_status",
              "arrest_location", "police_status",
              "motive", "organized_crime", "family_dispute", "community_context"):
        new_val = extraction.get(k)
        case[k] = _merge_value(case.get(k), new_val, k, conflicts, source_url)
        _record(k, new_val)

    # ---- num_victims: max ----
    if extraction.get("num_victims") and (
        not case.get("num_victims") or extraction["num_victims"] > case["num_victims"]
    ):
        case["num_victims"] = extraction["num_victims"]
    _record("num_victims", extraction.get("num_victims"))

    # ---- Suspect profession with conflict tracking ----
    sp_new = extraction.get("suspect_profession")
    sp_existing = case.get("suspect_profession")
    sp_conflicts: list[str] = case.setdefault("suspect_profession_conflict", [])
    if sp_new:
        if not sp_existing:
            case["suspect_profession"] = sp_new
            if sp_new not in sp_conflicts:
                sp_conflicts.append(sp_new)
        elif sp_new != sp_existing:
            if sp_existing not in sp_conflicts:
                sp_conflicts.append(sp_existing)
            if sp_new not in sp_conflicts:
                sp_conflicts.append(sp_new)

    # ---- Evidence + media (additive lists) ----
    case["evidence"] = _extend_unique(
        case.get("evidence") or [],
        extraction.get("evidence_items") or [],
    )
    case["media"] = _extend_unique(
        case.get("media") or [],
        extraction.get("media_items") or [],
    )

    # ---- Date precision: incident vs death ----
    if extraction.get("death_date") and case.get("incident_date"):
        if str(extraction["death_date"]) != str(case["incident_date"]):
            # The new article gives a death date that differs from the existing
            # incident date — this is information, not a conflict
            case["death_date"] = case.get("death_date") or extraction["death_date"]
            if "date_conflict_possible" not in flags:
                flags.append("date_conflict_possible")
    if extraction.get("incident_date") and case.get("incident_date") and \
       str(extraction["incident_date"]) != str(case["incident_date"]):
        case.setdefault("incident_date_possible", extraction["incident_date"])
        if "date_conflict_possible" not in flags:
            flags.append("date_conflict_possible")

    # ---- Source ref ----
    from crime_pipeline.scrapers.tier_registry import classify_url
    tier, registry_publisher = classify_url(source_url)
    publisher = registry_publisher or _infer_publisher(source_url)
    new_source = {
        "url": source_url,
        "discovery_source": discovery_source,
        "actual_publisher": publisher,
        "source_name": publisher,  # legacy
        "language": language,
        "tier": tier,
        "published_at": published_at.isoformat() if published_at else None,
        "confidence_score": float(extraction.get("confidence_score") or 0.5),
        "paywalled": paywalled,
        "body_extracted": body_extracted,
    }
    sources = case.setdefault("sources", [])
    existing_urls = {s.get("url") for s in sources}
    if source_url not in existing_urls:
        sources.append(new_source)

    # ---- Confidence rebuild ----
    if sources:
        # Weighted by source priority (police=3, ynet=2, panet=1, others=1)
        weights = {"police": 3, "ynet": 2, "kan": 2, "haaretz": 2, "panet": 1, "bokra": 1}
        total_w, total_score = 0, 0.0
        for s in sources:
            w = weights.get(s.get("actual_publisher") or "", 1)
            total_w += w
            total_score += w * s.get("confidence_score", 0.5)
        case["confidence_score"] = round(min(1.0, total_score / max(total_w, 1)), 3)
        # Lift the single-source cap once we have 2+ sources
        if len(sources) >= 2 and "single_source" in flags:
            flags.remove("single_source")
        if len(sources) >= 2:
            case["confidence_score"] = min(1.0, case["confidence_score"])
        else:
            case["confidence_score"] = min(case["confidence_score"], 0.60)

    # ---- Coverage flags ----
    if paywalled and "premium_source" not in flags:
        flags.append("premium_source")
    if not body_extracted and "needs_full_body_source" not in flags:
        flags.append("needs_full_body_source")

    # ---- Refresh canonical_case_id when key fields populate ----
    if not case.get("canonical_case_id"):
        cid = _build_canonical_id(case)
        if cid:
            case["canonical_case_id"] = cid

    return case


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class CaseEnricher:
    """Run a second-pass enrichment loop on a canonical case JSON file."""

    def __init__(
        self,
        gemini_api_key: str,
        llm_model: str = "gemini-2.5-flash",
        max_tokens: int = 1024,
        concurrency: int = 2,
        request_delay: float = 1.5,
        respect_robots: bool = True,
        max_queries: int = 6,
        max_articles_per_query: int = 4,
        rate_limit_delay: float = 13.0,  # ~5 RPM safe (12s) + buffer
        locale: str = "he",
        query_strategy: str = "default",  # "default" | "arabic_only" | "tier1" | "tier2" | "tier3"
        target_tier: int | None = None,
    ) -> None:
        self.extractor = ArticleExtractor(
            api_key=gemini_api_key,
            model=llm_model,
            max_tokens=max_tokens,
            concurrency=concurrency,
        )
        self.scraper = get_scraper(
            "googlenews",
            request_delay=request_delay,
            respect_robots=respect_robots,
            locale=locale,
        )
        self.max_queries = max_queries
        self.max_articles_per_query = max_articles_per_query
        self.rate_limit_delay = rate_limit_delay
        self.query_strategy = query_strategy
        self.locale = locale
        self.target_tier = target_tier

    async def enrich(self, case_path: Path, weak_only: bool = False) -> dict[str, Any]:
        """Run a full enrichment pass and write back the enriched case.

        Args:
            case_path:  Path to the output JSON file.
            weak_only:  When True, skip cases that already have both
                        victim_name and victim_outcome populated.
        """
        case_path = Path(case_path)
        with case_path.open("r", encoding="utf-8") as f:
            envelope = json.load(f)

        # Support both single-case and multi-case envelope shapes
        if "cases" in envelope and isinstance(envelope["cases"], list):
            cases = envelope["cases"]
        else:
            cases = [envelope]

        from crime_pipeline.enrichment.sanity_pass import run_sanity_pass
        from crime_pipeline.enrichment.quality_pass import run_quality_pass
        from crime_pipeline.enrichment.provenance import apply_provenance

        skipped = 0
        for case in cases:
            if weak_only and case.get("victim_name") and case.get("victim_outcome"):
                skipped += 1
                log.info("enrich_skipped_complete",
                         victim=ascii(case.get("victim_name")),
                         outcome=case.get("victim_outcome"))
                continue
            # Pre-pass: normalize sources/dates/scripts.
            run_sanity_pass(case)
            run_quality_pass(case)
            await self._enrich_one(case)
            # Post-pass: sanity → quality → sanity (so dropped sources update
            # tier_coverage) → provenance (built last so it sees the final
            # values + final source list).
            run_sanity_pass(case)
            run_quality_pass(case)
            run_sanity_pass(case)
            apply_provenance(case)
            case["enrichment_passes"] = (case.get("enrichment_passes") or 0) + 1

        # Write back
        envelope["cases"] = cases if "cases" in envelope else None
        envelope["last_enriched_at"] = datetime.now(timezone.utc).isoformat()
        with case_path.open("w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)
        log.info("enrichment_written", path=str(case_path), cases=len(cases))
        return envelope

    async def _enrich_one(self, case: dict[str, Any]) -> None:
        # Build canonical_case_id up-front from whatever fields we already have
        if not case.get("canonical_case_id"):
            cid = _build_canonical_id(case)
            if cid:
                case["canonical_case_id"] = cid

        # Backfill multilingual name fields from the city_normalized lookup,
        # and from the primary victim_name (script detection).
        primary = case.get("victim_name")
        if primary:
            if re.search(r"[؀-ۿ]", primary) and not case.get("victim_name_ar"):
                case["victim_name_ar"] = primary
            elif re.search(r"[֐-׿]", primary) and not case.get("victim_name_he"):
                case["victim_name_he"] = primary

        if self.target_tier in (1, 2, 3):
            queries = generate_queries_for_tier(
                case, self.target_tier, max_queries=self.max_queries
            )
            log.info("enrichment_queries", strategy=f"tier_{self.target_tier}",
                     count=len(queries), locale=self.locale,
                     victim=case.get("victim_name"))
        elif self.query_strategy == "arabic_only":
            queries = generate_arabic_enrichment_queries(case, max_queries=self.max_queries)
            log.info("enrichment_queries", strategy="arabic_only",
                     count=len(queries), locale=self.locale,
                     victim=case.get("victim_name"))
        else:
            queries = generate_enrichment_queries(case, max_queries=self.max_queries)
            log.info("enrichment_queries", strategy="default",
                     count=len(queries), victim=case.get("victim_name"))
        for q in queries:
            log.info("enrichment_query_run", query=q)

        existing_urls = {s.get("url") for s in case.get("sources") or []}
        new_extraction_count = 0

        for query in queries:
            try:
                urls = await self.scraper.discover(
                    query=query,
                    date_from="2025-01-01",
                    date_to="2027-12-31",
                    max_results=self.max_articles_per_query,
                )
            except Exception as e:
                log.warning("enrichment_discover_error", query=query, error=str(e))
                continue
            log.info("enrichment_discovered", query=query, count=len(urls))

            for du in urls:
                if du.url in existing_urls:
                    continue
                existing_urls.add(du.url)

                try:
                    article = await self.scraper.fetch(du.url)
                except Exception as e:
                    log.warning("enrichment_fetch_error", url=du.url, error=str(e))
                    continue
                if article.fetch_status != "success" or not article.article_text:
                    continue
                paywalled = article.content_type == "partial"
                body_extracted = article.content_type == "article"

                # Throttle to stay within free-tier RPM
                await asyncio.sleep(self.rate_limit_delay)

                ext = await self.extractor.extract(
                    article_text=article.article_text,
                    language=article.language,
                    published_date=(
                        article.published_at.isoformat() if article.published_at else None
                    ),
                    source=du.source,
                )
                if ext["status"] != "success" or not ext["extracted_data"]:
                    log.warning("enrichment_extraction_failed",
                                url=du.url, status=ext["status"], error=ext.get("error"))
                    continue

                merge_extraction_into_case(
                    case=case,
                    extraction=ext["extracted_data"],
                    source_url=article.final_url or du.url,
                    discovery_source=du.source,
                    language=article.language,
                    published_at=article.published_at or du.published_at,
                    paywalled=paywalled,
                    body_extracted=body_extracted,
                )
                new_extraction_count += 1

        # Final flag housekeeping
        flags = case.setdefault("flags", [])
        for needs_flag in ("needs_arabic_sources", "needs_hebrew_investigation_sources"):
            if needs_flag in flags:
                # If we now have a source in that language/category, remove
                if needs_flag == "needs_arabic_sources":
                    if any(s.get("language") == "ar" for s in case.get("sources") or []):
                        flags.remove(needs_flag)
                elif needs_flag == "needs_hebrew_investigation_sources":
                    he_sources = [s for s in case.get("sources") or []
                                  if s.get("language") == "he"]
                    if len(he_sources) >= 2:
                        flags.remove(needs_flag)

        # Add coverage gap flags if still missing
        if not any(s.get("language") == "ar" for s in case.get("sources") or []):
            if "needs_arabic_sources" not in flags:
                flags.append("needs_arabic_sources")
        if not case.get("media"):
            if "needs_media_extraction" not in flags:
                flags.append("needs_media_extraction")

        log.info("enrichment_pass_complete",
                 victim=case.get("victim_name"),
                 new_sources=new_extraction_count,
                 total_sources=len(case.get("sources") or []),
                 confidence=case.get("confidence_score"),
                 flags=case.get("flags"))
