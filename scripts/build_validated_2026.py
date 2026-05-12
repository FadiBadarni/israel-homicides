"""Aggregate validated 2026 cases into a single Schema 2.0 envelope.

Pulls every per-run output JSON in ``output/`` from the validated date
windows we have (Jan 1 → Feb 16, 2026), filters to:

  - victim_outcome == 'died'
  - named (at least one of victim_name_ar / he / en is populated)
  - incident_date inside the 2026 YTD validated window
  - city not in the "non-Israel" exclusion list (Gaza, Libya, Lyon, etc.)
  - name not in the "non-Arab-society" exclusion list (a couple
    Israeli-Jewish or international victims that snuck in via
    keyword sweeps with the term רצח/مقتل)

Then runs ``reconcile_cases`` (which uses the post-fix matcher with
fuzzy per-token containment + the gazetteer additions) to collapse
cross-source duplicates.

Writes a Schema 2.0 envelope to ``output/validated_2026_ytd.json``
which the UI API picks up automatically as one more "run" to browse.

Run from project root:
    python scripts/build_validated_2026.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from crime_pipeline.enrichment.reconciler import reconcile_cases


# Date window we've actually validated against ground truth.
DATE_FROM = "2026-01-01"
DATE_TO = "2026-02-16"


# Per-run output files we trust. Includes the original Jan keyword sweeps,
# the Makan + Walla extension runs, and the Feb 1-16 sweep.
_SOURCE_RUNS = [
    # January 2026
    "kw_ar_fa494eb6_2026.json",
    "kw_ar_7612246f_2026.json",
    "kw_ar_a3f907a1_2026.json",
    "kw_ar_534efab3_2026.json",
    "kw_he_737d3b05_2026.json",
    "kw_he_757747d2_2026.json",
    "kw_he_bf66b7f1_2026.json",
    "kw_he_1ebeb661_2026.json",
    "makan_qatl_jan26.json",
    "walla_jan26_rtsh.json",
    "walla_basma.json",
    # February 1-16
    "feb26_he_ynet_1ebeb661.json",
    "feb26_he_walla_1ebeb661.json",
    "feb26_he_ynet_bf66b7f1.json",
    "feb26_he_walla_bf66b7f1.json",
    "feb26_he_ynet_757747d2.json",
    "feb26_he_walla_757747d2.json",
    "feb26_he_ynet_737d3b05.json",
    "feb26_he_walla_737d3b05.json",
    "feb26_ar_arab48_7612246f.json",
    "feb26_ar_makan_7612246f.json",
    "feb26_ar_arab48_a3f907a1.json",
    "feb26_ar_makan_a3f907a1.json",
    "feb26_ar_arab48_534efab3.json",
    "feb26_ar_makan_534efab3.json",
    "feb26_ar_arab48_fa494eb6.json",
    "feb26_ar_makan_fa494eb6.json",
]

# Names of victims who slipped through despite being non-Arab-society
# (mostly Israeli-Jewish or international cases the keyword sweeps caught
# because the article used 'רצח' generically). Hardcoded exclusion list
# rather than a content-based filter so this stays auditable.
#
# Used as EXACT-string match (full primary name equals one of these).
_NON_ARAB_SOCIETY_NAMES = {
    "גיא בן סימון",        # Israeli-Jewish, Kiryat Yam
    "קוונטין דראנק",       # Lyon, France
    "לינדה סטיבנסון",      # Wilmington, USA
    "לואי רזק נתפי",       # Motorcycle accident, not homicide
}

# Substrings in primary name that indicate a non-Arab-society foreign
# figure. Substring rather than exact match because the Hebrew/Arabic
# transliterations of foreign names vary widely (סיף אל-אסלאם קדאפי vs
# سيف الإسلام القذافي vs Saif al-Islam Gaddafi vs Gadaffi etc.).
_NON_ARAB_SOCIETY_NAME_HINTS = [
    # Libya
    "קדאפי", "אלקדאפי", "القذافي", "القذاف", "Gaddafi", "Qaddafi", "Gadaffi",
    # Iran (high-profile assassinations frequently caught by 'مقتل'/'ירי')
    "דהקאן", "دهقان", "Dehghan",
    "אנסארי בחטיאר", "أنصاري بختيار",  # Iranian dissident assassinations
    "עזיזי", "عزيزي",                  # generic Iranian — keep if not Iranian
                                       # context; safe because Arab-society
                                       # has no עזיזי-named victims.
]

# Substrings in city that mark a non-Israeli incident location.
# Hebrew/Arabic news outlets routinely report on homicides abroad — Iran,
# US, EU, Russia, Gaza, West Bank — and our keyword sweeps catch those
# with words like "רצח" / "مقتل". This is a literal-substring blocklist
# rather than a positive-allowlist because the gazetteer can never list
# every legitimate small Israeli town, but the foreign cities are finite
# and well-known.
_NON_ISRAEL_CITY_HINTS = [
    # Gaza Strip + West Bank
    "غزة", "ע'זה", "עזה", "دير البلح", "בית-לאהיא", "بيت لاهيا",
    "خان يونس", "ח'אן יונס", "رفح", "רפיח", "نابلس", "שכם",
    # Other Arab states
    "الزنتان", "ا-זנתאן", "א-זנתאן",         # Libya
    "تركيا", "טורקיה", "أنقرة", "אנקרה",     # Turkey
    "بيروت", "ביירות",                       # Lebanon
    "دمشق", "דמשק", "حلب", "חאלב",          # Syria
    "بغداد", "בגדאד",                        # Iraq
    "القاهرة", "קהיר",                       # Egypt
    "عمّان", "עמאן",                         # Jordan
    # Iran
    "אצפהאן", "אסצפהאן", "איספהאן", "اصفهان", "ספהאן",
    "טהראן", "טהרן", "طهران",
    "هرسين", "הרסין",
    "ملارد", "מלרד", "מלארד",     # Malard, Tehran province
    "كرج", "כרג'", "Karaj",
    "مشهد", "משהד",
    "شيراز", "שיראז",
    "تبريز", "תבריז",
    # Europe
    "פריז", "פאריז", "باريس",
    "לונדון", "لندن",
    "ברלין", "برلين",
    "מדריד", "مدريد",
    "רומא", "روما",
    "בודפשט", "בודאפסט", "بودابست",
    "מוסקווה", "موسكو",
    "ליון", "ליאון", "ليون",
    # North America
    "ניו יורק", "نيويورك",
    "וושינגטון", "ושינגטון", "واشنطن",
    "ووילמינגטון", "ווילמינגטון", "וילמינגטון", "ويلمنغتون",
    "שיקגו", "شيكاغو",
    "מיניאפוליס", "מיניאפולס", "مينيابوليس", "مينيابولس",
    "טורונטו", "تورنتو",
    # Other
    "פקיסטן", "باكستان",
    "אפגניסטן", "أفغانستان",
    "אוקראינה", "أوكرانيا",
    "רוסיה", "روسيا",
]


def _parse_date(raw):
    if not raw:
        return None
    try:
        from datetime import date as _date
        return _date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def _best_name(case: dict) -> str:
    return (
        case.get("victim_name_ar")
        or case.get("victim_name_he")
        or case.get("victim_name_en")
        or case.get("victim_name")
        or ""
    ).strip()


def _is_in_window(case: dict) -> bool:
    from datetime import date as _date
    from_d = _date.fromisoformat(DATE_FROM)
    to_d = _date.fromisoformat(DATE_TO)
    d = _parse_date(case.get("incident_date"))
    return d is not None and from_d <= d <= to_d


def _is_israeli(case: dict) -> bool:
    city = (case.get("city") or "").strip()
    if not city:
        return True  # null cities pass through; reconciler may clean later
    return not any(hint in city for hint in _NON_ISRAEL_CITY_HINTS)


def _is_arab_society_victim(case: dict) -> bool:
    """Exclude known non-Arab-society victims via name match.

    Two filter modes:
      • Exact match against ``_NON_ARAB_SOCIETY_NAMES`` (auditable list of
        specific people seen in prior sweeps).
      • Substring match against ``_NON_ARAB_SOCIETY_NAME_HINTS`` to
        catch transliteration variants (e.g. Gaddafi spelled four
        different ways across Hebrew, Arabic, and English).
    """
    # Check ALL name fields, not just the best one — Arabic forms may
    # appear in victim_name_ar while Hebrew is empty (Gaddafi case).
    names = [
        case.get(k) for k in
        ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
    ]
    names = [n for n in names if n]
    if not names:
        return True
    if any(n in _NON_ARAB_SOCIETY_NAMES for n in names):
        return False
    for n in names:
        for hint in _NON_ARAB_SOCIETY_NAME_HINTS:
            if hint in n:
                return False
    return True


def main() -> None:
    output_dir = Path("output")
    all_cases: list[dict] = []
    seen_files = []
    missing_files = []

    for fname in _SOURCE_RUNS:
        path = output_dir / fname
        if not path.exists():
            missing_files.append(fname)
            continue
        seen_files.append(fname)
        env = json.loads(path.read_text(encoding="utf-8"))
        all_cases.extend(env.get("cases", []))

    print(f"Loaded {len(seen_files)} run files ({len(missing_files)} missing)")
    if missing_files:
        print(f"  Missing: {missing_files[:5]}...")
    print(f"Raw cases pooled: {len(all_cases)}")

    # Filter pass 1: named + died + in date window + Israel + Arab-society
    filtered = [
        c for c in all_cases
        if c.get("victim_outcome") == "died"
        and _best_name(c)
        and _is_in_window(c)
        and _is_israeli(c)
        and _is_arab_society_victim(c)
    ]
    print(f"After filters (died/named/in-window/Israel/Arab-society): {len(filtered)}")

    # Reconcile across runs — uses the post-fix matcher.
    result = reconcile_cases(filtered, jaro_threshold=0.85)
    print(f"After cross-source reconcile: {result.cases_after} cases "
          f"({len(result.merged_pairs)} merges collapsed duplicates)")

    # Aliases cleanup: the merger's conflict resolver moves losing-name
    # victims into aliases when clusters span multi-victim siblings. The
    # quality_pass then keeps anything that "shares a token" with the
    # primary — too permissive when both victims share a family name
    # (Adham Nassar vs Nadhim Nassar — both have נסאר as their last
    # token, so "Nadhim Nassar" passes the token-share filter and
    # bleeds into Adham's aliases).
    #
    # Stricter post-build pass: keep an alias ONLY when its romanized
    # form has Jaro >= 0.85 with at least one of the case's primary
    # romanized names. Spelling variants pass; distinct-person aliases
    # are stripped.
    from crime_pipeline.dedup.name_normalizer import (
        jaro_winkler_similarity, romanize_name,
    )

    def _alias_belongs_to_primary(ar: str, primaries_rom: list[str]) -> bool:
        """Decide whether romanized alias ``ar`` is a legitimate spelling
        variant of any romanized primary name in ``primaries_rom``.

        Three pass-conditions (any one fires → keep alias):
          1. ``ar`` is identical to a primary (case-insensitive equal).
          2. Full-string Jaro ≥ 0.95 with a primary (very-near identical,
             e.g. one-char typo or missing diacritic).
          3. Token-overlap ≥ 0.85 AND first-token positional match. This
             is the same fuzzy-containment used by the verify matcher
             and reconciler — rules out the father-son pattern where
             ``נד'ים נסאר`` (Nadhim Nassar) sits at Jaro 0.87 against
             ``אדהם נסאר`` (Adham Nassar) purely because they share the
             ``נסאר`` surname.
        """
        for p in primaries_rom:
            if ar == p:
                return True
            if jaro_winkler_similarity(ar, p) >= 0.95:
                return True
            a_tokens = [t for t in ar.split() if len(t) > 1]
            p_tokens = [t for t in p.split() if len(t) > 1]
            if not a_tokens or not p_tokens:
                continue
            # First-token positional anchor: aliases sharing only the
            # family-name fail this check, killing the father↔son pattern.
            first_jaro = jaro_winkler_similarity(a_tokens[0], p_tokens[0])
            if first_jaro < 0.85:
                continue
            # All alias tokens must have a Jaro ≥ 0.85 partner in primary
            all_match = all(
                any(jaro_winkler_similarity(at, pt) >= 0.85 for pt in p_tokens)
                for at in a_tokens
            )
            if all_match:
                return True
        return False

    def _strip_cross_victim_aliases(case: dict) -> dict:
        primaries_rom = []
        for k in ("victim_name", "victim_name_ar",
                  "victim_name_he", "victim_name_en"):
            v = case.get(k)
            if v:
                primaries_rom.append(romanize_name(v))
        primaries_rom = [p for p in primaries_rom if p]
        if not primaries_rom:
            return case
        clean = []
        dropped = []
        for alias in case.get("aliases") or []:
            ar = romanize_name(alias)
            if not ar:
                continue
            if _alias_belongs_to_primary(ar, primaries_rom):
                clean.append(alias)
            else:
                dropped.append(alias)
        case["aliases"] = clean
        if dropped:
            case.setdefault("flags", []).append("aliases_cleaned")
        return case

    cleaned_cases = [_strip_cross_victim_aliases(c) for c in result.cases]
    stripped = sum(
        1 for c in cleaned_cases if "aliases_cleaned" in (c.get("flags") or [])
    )
    print(f"Aliases cleanup: stripped cross-victim aliases on {stripped} cases")

    # Sort cases by incident_date for the UI.
    def sort_key(c):
        d = _parse_date(c.get("incident_date"))
        return (d.isoformat() if d else "9999", _best_name(c))
    sorted_cases = sorted(cleaned_cases, key=sort_key)

    # Schema 2.0 envelope.
    envelope = {
        "schema_version": "2.0",
        "kind": "crime_pipeline.run",
        "pipeline_run_id": "validated_2026_ytd",
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "run": {
            "started_at": None,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
            "duration_seconds": None,
            "stages_executed": ["aggregate", "filter", "reconcile"],
        },
        "stats": {
            "source_runs_aggregated": len(seen_files),
            "raw_cases_pooled": len(all_cases),
            "after_filters": len(filtered),
            "after_reconcile": result.cases_after,
            "reconcile_merges": len(result.merged_pairs),
        },
        "case_count": len(sorted_cases),
        "cases": sorted_cases,
        "human_summary": (
            f"Validated 2026 Arab-society homicide victims, "
            f"{DATE_FROM} to {DATE_TO}. Aggregates {len(seen_files)} per-run "
            f"outputs (Ynet + Arab48 + Makan + Walla), filters to named-died-"
            f"in-Israel, then cross-source-reconciles by name + city + date."
        ),
    }

    out_path = output_dir / "validated_2026_ytd.json"
    out_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print()
    print(f"Wrote: {out_path}")
    print(f"  case_count: {envelope['case_count']}")
    print()
    print("Open the UI to validate:")
    print("  1) Terminal A:  uvicorn ui.api.main:app --reload --port 8001")
    print("  2) Terminal B:  cd ui/frontend && npm run dev")
    print("  3) Browser:     http://localhost:3000  → pipeline_run_id 'validated_2026_ytd'")


if __name__ == "__main__":
    main()
