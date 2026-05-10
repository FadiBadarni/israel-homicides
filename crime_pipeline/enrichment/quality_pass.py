"""
Quality / second-stage cleanup pass.

Runs AFTER sanity_pass to fix the false-conflict and dedup issues that the
first pass can't catch because they require semantic understanding:

A. Drop sources that don't pass per-tier validation (Tier 3 must look like
   a real press release; generic forms get demoted to untiered).
B. Repair date semantics: if death_date < incident_date, swap them; the
   actual incident is always at or before the reported death.
C. Move name "conflicts" that are actually script variants → into aliases.
D. Collapse status synonyms (arrested ≡ in_custody, charged ≡ indicted) and
   move them to the right axis instead of recording false conflicts.
E. Resolve place-granularity "conflicts" — "hideout apartment in the city"
   and "apartment" describe the same place at different specificity levels.
F. Deduplicate evidence list — entries that describe the same fact across
   languages collapse into one multilingual record.
G. Improve canonical_case_id by phonetically romanizing Arabic/Hebrew names
   instead of using anyascii's character-by-character output.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

# ---------------------------------------------------------------------------
# A. Drop invalid Tier 3 / "untiered junk" sources
# ---------------------------------------------------------------------------


def drop_invalid_sources(case: dict[str, Any]) -> dict[str, Any]:
    """
    Remove sources that classify_url demoted to tier=None AND that come from
    a known-Tier-3 host (i.e., police.gov.il /forms/Rights%20Guide.pdf).
    """
    from crime_pipeline.scrapers.tier_registry import DOMAIN_TO_TIER
    sources = case.get("sources") or []
    surviving = []
    dropped = []
    for s in sources:
        url = s.get("url", "")
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().replace("www.", "")
        # Find the canonical domain for this host
        canonical_tier = None
        for d, t in DOMAIN_TO_TIER.items():
            if host == d or host.endswith("." + d):
                canonical_tier = t
                break
        if canonical_tier == 3 and s.get("tier") is None:
            # Demoted Tier 3 source — drop entirely
            dropped.append(s)
        elif s.get("tier") is None and canonical_tier is None:
            # Unknown domain. Keep but log.
            surviving.append(s)
        else:
            surviving.append(s)

    case["sources"] = surviving

    # Also remove timeline events tied to dropped sources
    if dropped:
        dropped_urls = {d.get("url") for d in dropped}
        case["timeline"] = [
            ev for ev in (case.get("timeline") or [])
            if ev.get("source_url") not in dropped_urls
        ]
        # Surface dropped URLs in flags for transparency
        flags: list[str] = case.setdefault("flags", [])
        if "tier3_invalid_dropped" not in flags:
            flags.append("tier3_invalid_dropped")
        # Stash details
        case.setdefault("dropped_invalid_sources", []).extend([
            {"url": d.get("url"), "actual_publisher": d.get("actual_publisher"),
             "reason": "tier3_url_failed_validation"}
            for d in dropped
        ])

    return case


# ---------------------------------------------------------------------------
# B. Date semantics repair
# ---------------------------------------------------------------------------


def _coerce_date(d: Any) -> date | None:
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


def repair_date_semantics(case: dict[str, Any]) -> dict[str, Any]:
    """
    Logical fix:
      - The incident always occurs ON or BEFORE the death.
      - If death_date < incident_date, the LLM swapped them — repair.
      - If incident_date_possible exists and is earlier than incident_date,
        and death_date is missing, set death_date = incident_date and
        promote incident_date_possible → incident_date logic stays as
        secondary.

    Concretely for the Bakr Yassin case the sources say:
      - death reported on 2026-01-04
      - incident occurred late on 2026-01-03

    So: incident_date = 2026-01-04 (when death was reported), death_date =
    2026-01-04, incident_date_possible = 2026-01-03 (when shooting occurred).
    """
    incident_dt = _coerce_date(case.get("incident_date"))
    death_dt = _coerce_date(case.get("death_date"))
    poss_dt = _coerce_date(case.get("incident_date_possible"))

    # Rule 1: if death < incident, swap (impossible chronology)
    if incident_dt and death_dt and death_dt < incident_dt:
        case["incident_date"], case["death_date"] = death_dt.isoformat(), incident_dt.isoformat()
        incident_dt, death_dt = death_dt, incident_dt
        flags: list[str] = case.setdefault("flags", [])
        if "date_chronology_repaired" not in flags:
            flags.append("date_chronology_repaired")

    # Rule 2: incident_date_possible should be ≤ incident_date (it's the
    # earlier candidate). If it's later, demote it.
    if incident_dt and poss_dt and poss_dt > incident_dt:
        # Swap roles — the "possible" was actually the death-report date
        case["incident_date_possible"] = incident_dt.isoformat()
        case["incident_date"] = poss_dt.isoformat()

    # Rule 3: if death_date is missing but we have incident_date and a
    # plausible incident_date_possible, infer death_date = incident_date.
    incident_dt = _coerce_date(case.get("incident_date"))  # re-coerce after possible swap
    death_dt = _coerce_date(case.get("death_date"))
    poss_dt = _coerce_date(case.get("incident_date_possible"))
    if incident_dt and not death_dt and poss_dt and poss_dt < incident_dt:
        case["death_date"] = incident_dt.isoformat()

    return case


# ---------------------------------------------------------------------------
# C. Name conflict → alias promotion
# ---------------------------------------------------------------------------


_HEBREW_RE = re.compile(r"[֐-׿]")
_ARABIC_RE = re.compile(r"[؀-ۿ]")


def _is_arabic(s: str) -> bool:
    return bool(s and _ARABIC_RE.search(s))


def _is_hebrew(s: str) -> bool:
    return bool(s and _HEBREW_RE.search(s))


def promote_name_conflicts_to_aliases(case: dict[str, Any]) -> dict[str, Any]:
    """
    Names in different scripts / different lengths are NOT conflicts — they
    are language variants. Promote them to aliases and clear the conflict.
    """
    conflicts = case.get("conflicts") or {}
    aliases: list[str] = case.setdefault("aliases", [])

    primary_names = {
        case.get("victim_name"), case.get("victim_name_he"),
        case.get("victim_name_ar"), case.get("victim_name_en"),
    }
    primary_names = {n for n in primary_names if n}

    def add_alias(value: str) -> None:
        v = value.strip() if value else ""
        # Don't add if already a primary name field — those are canonical, not aliases
        if v and v not in aliases and v not in primary_names:
            aliases.append(v)

    # victim_name + victim_name_ar/he/en conflicts → promote to alias and clear
    for field in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        vn_conflicts = conflicts.get(field)
        if not isinstance(vn_conflicts, dict):
            continue
        for val in vn_conflicts.values():
            if val:
                add_alias(val)
        conflicts.pop(field, None)

    # Filter: an alias MUST share at least one meaningful token with one of
    # the primary name fields. Otherwise it's a different person mentioned
    # in passing in an article (e.g. "third victim of 2026 — كرم سواعد").
    def _shares_token(alias: str, primaries: set[str]) -> bool:
        if not alias:
            return False
        a_tokens = {t for t in re.split(r"\s+", alias.strip()) if len(t) >= 2}
        for p in primaries:
            p_tokens = {t for t in re.split(r"\s+", (p or "").strip()) if len(t) >= 2}
            if a_tokens & p_tokens:
                return True
        return False

    case["aliases"] = [
        a for a in aliases
        if a not in primary_names and _shares_token(a, primary_names)
    ]

    case["conflicts"] = conflicts
    return case


def clean_redundant_conflicts(case: dict[str, Any]) -> dict[str, Any]:
    """
    Remove conflict entries whose values are identical to the canonical or
    are gazetteer-equivalent (different scripts of the same place name).
    """
    from crime_pipeline.utils.gazetteer import normalize_city

    conflicts = case.get("conflicts") or {}

    # city: if the conflict value resolves to the same gazetteer record as
    # the canonical city, it's not a conflict.
    city_conflict = conflicts.get("city")
    if isinstance(city_conflict, dict):
        canonical_record = normalize_city(case.get("city") or "") if case.get("city") else None
        canonical_en = (canonical_record or {}).get("name_en", "")
        new_dict = {}
        for url, val in city_conflict.items():
            other_record = normalize_city(val) if val else None
            if other_record and other_record.get("name_en") == canonical_en:
                continue  # gazetteer-equivalent — drop
            if val == case.get("city"):
                continue
            new_dict[url] = val
        if new_dict:
            conflicts["city"] = new_dict
        else:
            conflicts.pop("city", None)

    # victim_residence: same logic
    res_conflict = conflicts.get("victim_residence")
    if isinstance(res_conflict, dict):
        new_dict = {}
        canonical_record = normalize_city(case.get("victim_residence") or "") if case.get("victim_residence") else None
        canonical_en = (canonical_record or {}).get("name_en", "")
        for url, val in res_conflict.items():
            other = normalize_city(val) if val else None
            if other and other.get("name_en") == canonical_en:
                continue
            new_dict[url] = val
        if new_dict:
            conflicts["victim_residence"] = new_dict
        else:
            conflicts.pop("victim_residence", None)

    # Drop conflict entries where the value equals the canonical value.
    for field in list(conflicts.keys()):
        val = conflicts[field]
        if not isinstance(val, dict):
            continue
        canon = case.get(field)
        new_dict = {url: v for url, v in val.items() if v != canon}
        if new_dict:
            conflicts[field] = new_dict
        else:
            conflicts.pop(field, None)

    case["conflicts"] = conflicts
    return case


def propagate_region_from_gazetteer(case: dict[str, Any]) -> dict[str, Any]:
    """If gazetteer entry has a region and case.region is empty, copy it."""
    if case.get("region"):
        return case
    from crime_pipeline.utils.gazetteer import normalize_city
    record = normalize_city(case.get("city") or "")
    if record and record.get("region"):  # type: ignore[truthy-dict]
        case["region"] = record["region"]  # type: ignore[index]
    return case


# ---------------------------------------------------------------------------
# D. Status synonym collapse
# ---------------------------------------------------------------------------


# Mapping from raw extractor outputs to canonical (suspect_axis, legal_axis, invest_axis)
_STATUS_SYNONYMS: dict[str, dict[str, str]] = {
    "arrested": {"suspect": "arrested"},
    "in_custody": {"suspect": "arrested"},  # synonym
    "in custody": {"suspect": "arrested"},
    "released_on_bail": {"suspect": "released_on_bail"},
    "released on bail": {"suspect": "released_on_bail"},
    "wanted": {"suspect": "wanted"},
    "at_large": {"suspect": "at_large"},

    "charged": {"legal": "indicted", "suspect": "arrested"},
    "indicted": {"legal": "indicted"},
    "on_trial": {"legal": "on_trial"},
    "on trial": {"legal": "on_trial"},
    "convicted": {"legal": "convicted"},
    "acquitted": {"legal": "acquitted"},
    "case_closed": {"legal": "case_closed"},

    "investigation_open": {"invest": "open"},
    "completed": {"invest": "completed"},
    "indictment_filed": {"invest": "indictment_filed", "legal": "indicted"},
    "closed": {"invest": "closed"},
    "suspect_identified": {"invest": "suspect_identified"},
}


def collapse_status_synonyms(case: dict[str, Any]) -> dict[str, Any]:
    """
    Move status values to their proper axis. arrested vs in_custody are NOT
    a conflict — they're synonyms. charged is a legal_status, not a
    suspect_status.
    """
    conflicts = case.get("conflicts") or {}

    # Cleanup the suspect_status conflict dict — entries on the same axis
    # (arrested vs in_custody) collapse; entries on different axes move.
    ss_conflicts = conflicts.get("suspect_status")
    if isinstance(ss_conflicts, dict):
        # All values that map to the SAME suspect axis are not conflicts
        canonical_suspect: set[str] = set()
        canonical_legal: set[str] = set()
        canonical_invest: set[str] = set()
        for v in ss_conflicts.values():
            mapping = _STATUS_SYNONYMS.get((v or "").lower(), {})
            if "suspect" in mapping:
                canonical_suspect.add(mapping["suspect"])
            if "legal" in mapping:
                canonical_legal.add(mapping["legal"])
            if "invest" in mapping:
                canonical_invest.add(mapping["invest"])

        # If the current suspect_status agrees with the canonicalized values,
        # no conflict.
        cur = (case.get("suspect_status") or "").lower()
        cur_canon = _STATUS_SYNONYMS.get(cur, {}).get("suspect") or cur
        if canonical_suspect and canonical_suspect == {cur_canon}:
            conflicts.pop("suspect_status", None)
        # Promote to other axes if absent
        if canonical_legal and not case.get("legal_status"):
            # take the most-advanced legal status mentioned
            order = ["pre_indictment", "indicted", "on_trial", "convicted", "acquitted", "case_closed"]
            chosen = max(canonical_legal, key=lambda x: order.index(x) if x in order else 0)
            case["legal_status"] = chosen
        if canonical_invest and not case.get("police_investigation_status"):
            order = ["open", "suspect_identified", "completed", "indictment_filed", "closed"]
            chosen = max(canonical_invest, key=lambda x: order.index(x) if x in order else 0)
            case["police_investigation_status"] = chosen

    # Apply synonym collapse to current suspect_status itself
    cur = (case.get("suspect_status") or "").lower()
    mapping = _STATUS_SYNONYMS.get(cur)
    if mapping:
        if "suspect" in mapping:
            case["suspect_status"] = mapping["suspect"]
        if "legal" in mapping and not case.get("legal_status"):
            case["legal_status"] = mapping["legal"]
        if "invest" in mapping and not case.get("police_investigation_status"):
            case["police_investigation_status"] = mapping["invest"]

    case["conflicts"] = conflicts
    return case


# ---------------------------------------------------------------------------
# E. Place granularity resolver
# ---------------------------------------------------------------------------


def resolve_place_granularity(case: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve `arrest_location` and similar place-description "conflicts"
    where the values describe the same physical place at different
    specificity levels. Keep the most specific.
    """
    conflicts = case.get("conflicts") or {}

    for field in ("arrest_location", "exact_place_type", "neighborhood"):
        per_source = conflicts.get(field)
        if not isinstance(per_source, dict):
            continue
        # Take all values + the canonical one, dedupe by length (longer = more specific)
        all_values = list(per_source.values())
        if case.get(field):
            all_values.append(case[field])
        all_values = [v for v in all_values if v and isinstance(v, str)]
        if not all_values:
            continue
        # Choose the longest value as canonical (most specific)
        all_values.sort(key=lambda v: -len(v))
        case[field] = all_values[0]
        # If all the conflict values share substring overlap with the canonical,
        # they're granularity variations — drop the conflict entry.
        canonical = case[field].lower()
        all_aligned = all(
            (v.lower() in canonical) or (canonical in v.lower())
            or _share_token(v, canonical)
            for v in per_source.values() if isinstance(v, str)
        )
        if all_aligned:
            conflicts.pop(field, None)

    case["conflicts"] = conflicts
    return case


def _share_token(a: str, b: str) -> bool:
    """True if a and b share a meaningful word token."""
    if not a or not b:
        return False
    a_tokens = {t for t in re.split(r"\W+", a.lower()) if len(t) > 2}
    b_tokens = {t for t in re.split(r"\W+", b.lower()) if len(t) > 2}
    return bool(a_tokens & b_tokens)


# ---------------------------------------------------------------------------
# F. Evidence dedup → multilingual fact
# ---------------------------------------------------------------------------


_LAUNDRY_BASKET_SYNS = ["laundry basket", "סל הכביסה", "סלת כביסה", "سلة الغسيل", "سلة للغسيل"]
_HANDGUN_SYNS = ["handgun", "אקדח", "مسدس", "pistol"]


def _evidence_signature(item: dict[str, Any]) -> str:
    """Compute a normalized signature so cross-language equivalents match."""
    desc = (item.get("description") or "").lower()
    loc = (item.get("location_found") or "").lower()
    type_ = (item.get("type") or "").lower()
    sig_parts = [type_]
    # Normalize known concepts
    for syns, canonical in [
        (_HANDGUN_SYNS, "handgun"),
        (_LAUNDRY_BASKET_SYNS, "laundry_basket"),
    ]:
        for s in syns:
            if s in desc or s in loc:
                sig_parts.append(canonical)
                break
    return "|".join(sorted(set(sig_parts)))


def deduplicate_evidence(case: dict[str, Any]) -> dict[str, Any]:
    """Collapse equivalent multilingual evidence rows into one record.

    Idempotent on re-runs: if an item already has `description_translations`
    and `source_count`, those are preserved/merged rather than reset to the
    single visible description.
    """
    items = case.get("evidence") or []
    if not items:
        return case

    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        sig = _evidence_signature(item)
        if sig not in grouped:
            grouped[sig] = {
                "type": item.get("type"),
                "descriptions": [],
                "locations": [],
                "source_count": 0,
            }
        # Use existing translation lists if present (re-run safety) — these
        # carry richer history than the single visible description
        existing_desc_trans = item.get("description_translations") or []
        existing_loc_trans = item.get("location_translations") or []
        if existing_desc_trans:
            grouped[sig]["descriptions"].extend(existing_desc_trans)
        elif item.get("description"):
            grouped[sig]["descriptions"].append(item["description"])
        if existing_loc_trans:
            grouped[sig]["locations"].extend(existing_loc_trans)
        elif item.get("location_found"):
            grouped[sig]["locations"].append(item["location_found"])
        # Preserve source_count from prior dedup
        prior_count = item.get("source_count") or 0
        grouped[sig]["source_count"] = max(grouped[sig]["source_count"], prior_count)

    merged: list[dict[str, Any]] = []
    for sig, g in grouped.items():
        descs = list(dict.fromkeys(g["descriptions"]))  # dedup preserve order
        locs = list(dict.fromkeys(g["locations"]))
        en_desc = next((d for d in descs if not _is_arabic(d) and not _is_hebrew(d)), None)
        canonical_desc = en_desc or (descs[0] if descs else None)
        en_loc = next((l for l in locs if not _is_arabic(l) and not _is_hebrew(l)), None)
        canonical_loc = en_loc or (locs[0] if locs else None)

        merged.append({
            "type": g["type"],
            "description": canonical_desc,
            "location_found": canonical_loc,
            "description_translations": descs,
            "location_translations": locs,
            "source_count": max(len(descs), g["source_count"]),
        })
    case["evidence"] = merged
    return case


# ---------------------------------------------------------------------------
# G. Better canonical_case_id romanization
# ---------------------------------------------------------------------------


# Curated romanization for common Arabic given names + surnames seen in
# Israeli/Palestinian news. This avoids the character-by-character anyascii
# garbage (e.g. "VKHR-YSYN" instead of "BAKR-YASSIN").
_ARABIC_NAME_ROMANIZATION = {
    "بكر": "BAKR", "أبو": "ABU", "ابو": "ABU",
    "محمد": "MOHAMMED", "محمود": "MAHMOUD", "أحمد": "AHMED", "احمد": "AHMED",
    "علي": "ALI", "حسن": "HASSAN", "حسين": "HUSSEIN",
    "ياسين": "YASSIN", "خالد": "KHALED", "إبراهيم": "IBRAHIM", "ابراهيم": "IBRAHIM",
    "عمر": "OMAR", "يوسف": "YUSUF", "موسى": "MUSA", "عيسى": "ISA",
    "كريم": "KARIM", "سالم": "SALEM", "سامي": "SAMI", "وليد": "WALEED",
    "سعيد": "SAEED", "نور": "NUR", "أمين": "AMIN", "نصّار": "NASSAR", "نصار": "NASSAR",
    "عاصلة": "ASLA", "شلاعطة": "SHLAATA", "دكّة": "DAKKA", "دكة": "DAKKA",
    "أبو سالم": "ABU-SALEM", "أبو سلام": "ABU-SALAM",
    "اغبارية": "AGBARIA", "عجبارية": "AGBARIA", "جبارين": "JABARIN",
    "خطيب": "KHATIB", "حلايقة": "HALAYQA", "زبيدات": "ZBEIDAT",
    "صرصور": "SARSUR", "حلبي": "HALABI",
}


def _romanize_arabic_name(name: str) -> str:
    """Use the curated table for known Arabic names; fall back to anyascii."""
    if not name:
        return ""
    out_parts: list[str] = []
    for word in name.split():
        clean = word.strip("،,.;:!?")
        if clean in _ARABIC_NAME_ROMANIZATION:
            out_parts.append(_ARABIC_NAME_ROMANIZATION[clean])
        else:
            try:
                from anyascii import anyascii
                rom = anyascii(clean).upper().strip()
                rom = re.sub(r"[^A-Z]", "", rom)
                if rom:
                    out_parts.append(rom)
            except Exception:
                continue
    return "-".join(out_parts)


def improve_canonical_case_id(case: dict[str, Any]) -> dict[str, Any]:
    """Rebuild canonical_case_id with phonetic Arabic romanization."""
    parts = ["IL", "HOMICIDE"]
    incident_year = (str(case.get("incident_date") or case.get("death_date") or ""))[:4]
    if incident_year:
        parts.append(incident_year)
    norm = case.get("city_normalized") or {}
    city_en = norm.get("name_en") or case.get("city")
    if city_en:
        parts.append(re.sub(r"[^A-Z]", "", city_en.upper().replace(" ", "-")))

    # Prefer victim_name_en if available, else romanize Arabic
    name_en = case.get("victim_name_en") or ""
    if not name_en:
        ar_name = case.get("victim_name_ar") or ""
        he_name = case.get("victim_name_he") or ""
        name_en = _romanize_arabic_name(ar_name) or _romanize_arabic_name(he_name)

    if name_en:
        clean = re.sub(r"[^A-Z\-]", "", name_en.upper().replace(" ", "-"))
        if clean:
            parts.append(clean)

    if len(parts) >= 4:
        case["canonical_case_id"] = "-".join(parts)
    return case


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def promote_weak_conflicts_to_translations(case: dict[str, Any]) -> dict[str, Any]:
    """
    Some "conflicts" are weak — different-language paraphrasings of the same
    fact. Promote these into the field's translations rather than recording
    a contradiction.

    - motive: arabic-language phrasing of the same idea (خلاف نشب بينهما ≈ family dispute)
    - exact_place_type: "unknown" is not a contradiction with "family_home"
    - arrest_location: "شقة" (apartment) is a less-specific synonym
    """
    conflicts = case.get("conflicts") or {}
    motive_translations = case.setdefault("motive_translations", [])
    arrest_loc_translations = case.setdefault("arrest_location_translations", [])

    # motive: keep canonical English, store other-script values as translations
    motive_conflict = conflicts.get("motive")
    if isinstance(motive_conflict, dict):
        for url, val in motive_conflict.items():
            if val and val not in motive_translations:
                motive_translations.append(val)
        conflicts.pop("motive", None)

    # arrest_location: same logic
    al_conflict = conflicts.get("arrest_location")
    if isinstance(al_conflict, dict):
        for url, val in al_conflict.items():
            if val and val not in arrest_loc_translations:
                arrest_loc_translations.append(val)
        conflicts.pop("arrest_location", None)

    # exact_place_type: "unknown" / null are not contradictions
    ept_conflict = conflicts.get("exact_place_type")
    if isinstance(ept_conflict, dict):
        new_dict = {url: v for url, v in ept_conflict.items()
                    if v and v != "unknown"}
        if new_dict:
            conflicts["exact_place_type"] = new_dict
        else:
            conflicts.pop("exact_place_type", None)

    # Clean empty motive/arrest_loc lists
    if not motive_translations:
        case.pop("motive_translations", None)
    if not arrest_loc_translations:
        case.pop("arrest_location_translations", None)

    case["conflicts"] = conflicts
    return case


def reevaluate_rejected_articles(case: dict[str, Any]) -> dict[str, Any]:
    """
    Re-check entries in rejected_unrelated_articles using the *current*
    gazetteer + name set. Some rejections may now pass (e.g. عرابة البطوف
    is now a recognized alias of Arraba).
    """
    from crime_pipeline.utils.gazetteer import normalize_city
    rejected = case.get("rejected_unrelated_articles") or []
    if not rejected:
        return case

    # Reusable city set
    case_cities = {case.get("city")}
    norm = case.get("city_normalized") or {}
    for k in ("name_ar", "name_he", "name_en"):
        if norm.get(k):
            case_cities.add(norm[k])
    canonical_city_record = normalize_city(case.get("city") or "")
    canonical_en = (canonical_city_record or {}).get("name_en", "")

    still_rejected = []
    for r in rejected:
        reason = r.get("reason", "")
        # Only re-check city_mismatch ones (we can't recover name/date rejects
        # because we don't have the original extraction)
        if reason.startswith("city_mismatch"):
            # Parse the rejected city from the reason string
            import re as _re
            m = _re.search(r"city_mismatch:\s*'([^']+)'", reason)
            if m:
                rejected_city = m.group(1)
                rec = normalize_city(rejected_city)
                if rec and rec.get("name_en") == canonical_en:
                    # Now matches — but we already lost the extraction data.
                    # Mark for human review rather than auto-merge.
                    r["reevaluated"] = "would_now_match_gazetteer"
                    still_rejected.append(r)
                    continue
        still_rejected.append(r)

    case["rejected_unrelated_articles"] = still_rejected
    return case


def reset_unsupported_booleans(case: dict[str, Any]) -> dict[str, Any]:
    """
    Boolean facts (organized_crime, family_dispute) should be null unless a
    source explicitly asserts them. The LLM frequently emits `false` as a
    default rather than because a source ruled it out.

    Heuristic: if the value is False AND there is no positive evidence
    (no conflict entry, no contextual signal in motive), reset to null.
    family_dispute=true stays — that's typically inferred from explicit
    article framing ("brother killed brother", "family argument").
    """
    if case.get("organized_crime") is False:
        # No conflict means no source explicitly addressed it
        if not (case.get("conflicts", {}) or {}).get("organized_crime"):
            case["organized_crime"] = None
    return case


def promote_richer_victim_name_ar(case: dict[str, Any]) -> dict[str, Any]:
    """
    If aliases contains a fuller Arabic name (more tokens than the canonical
    victim_name_ar), promote it to canonical and demote the shorter form to
    alias. The fuller form is more specific (includes patronymic).
    """
    import re as _re
    aliases: list[str] = case.get("aliases") or []
    current_ar = case.get("victim_name_ar") or ""
    if not current_ar:
        return case
    arabic_aliases = [
        a for a in aliases
        if _re.search(r"[؀-ۿ]", a) and not _re.search(r"[֐-׿]", a)
    ]
    if not arabic_aliases:
        return case
    # Prefer the longest alias (most tokens) over current canonical IF it
    # contains the current canonical as a substring (i.e., it's a superset).
    current_tokens = current_ar.split()
    for alias in sorted(arabic_aliases, key=lambda a: -len(a.split())):
        alias_tokens = alias.split()
        # Superset: every current token appears in alias
        if (len(alias_tokens) > len(current_tokens) and
                all(t in alias_tokens for t in current_tokens)):
            # Promote alias to canonical
            case["victim_name_ar"] = alias
            new_aliases = [a for a in aliases if a != alias]
            if current_ar not in new_aliases:
                new_aliases.append(current_ar)
            case["aliases"] = new_aliases
            break
    return case


def add_indictment_update_role(case: dict[str, Any]) -> dict[str, Any]:
    """
    Sources published >=14 days after the incident reporting on
    legal_status='indicted' / 'on_trial' / 'convicted' earn the
    `indictment_update` role.
    """
    if case.get("legal_status") not in ("indicted", "on_trial", "convicted"):
        return case
    from datetime import datetime
    incident = case.get("incident_date") or case.get("death_date")
    if not incident:
        return case
    try:
        incident_dt = datetime.fromisoformat(str(incident)[:10])
    except Exception:
        return case
    for s in case.get("sources") or []:
        pub = s.get("published_at")
        if not pub:
            continue
        try:
            pub_dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
        except Exception:
            continue
        if (pub_dt.date() - incident_dt.date()).days >= 14:
            roles = s.setdefault("roles", [])
            if "indictment_update" not in roles:
                roles.append("indictment_update")
                roles.sort()
    return case


def run_quality_pass(case: dict[str, Any]) -> dict[str, Any]:
    """Apply all quality fixes in dependency order."""
    case = drop_invalid_sources(case)
    case = repair_date_semantics(case)
    case = propagate_region_from_gazetteer(case)
    case = promote_name_conflicts_to_aliases(case)
    case = promote_richer_victim_name_ar(case)
    case = collapse_status_synonyms(case)
    case = resolve_place_granularity(case)
    case = promote_weak_conflicts_to_translations(case)
    case = reset_unsupported_booleans(case)
    case = clean_redundant_conflicts(case)
    case = deduplicate_evidence(case)
    case = improve_canonical_case_id(case)
    case = add_indictment_update_role(case)
    case = reevaluate_rejected_articles(case)
    return case
