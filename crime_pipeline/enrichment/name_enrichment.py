"""Post-merge fill-in of missing language fields on canonical cases.

Runs AFTER the reconcile stage so cross-source merging has had every
opportunity to fill ``victim_name_*`` from real sources. For any field
still empty, generate a transliteration and write it to
``name_transliterations`` (NOT into the source-of-truth field).

Contract:
  • Never overwrites a source-attested ``victim_name_*`` value.
  • Every inferred value carries provenance (target script, source
    script, method, source value).
  • Empty input → no entries added (the list stays empty).
  • Idempotent: re-running on a case adds nothing new because the
    existing transliterations already cover the gaps.
"""
from __future__ import annotations

from typing import Any

from crime_pipeline.enrichment.transliterator import transliterate


# We fill ar/he/en. ``victim_name`` (the bare primary) is left alone —
# it's a display field, not a language-specific one.
_TARGET_SCRIPTS = ("ar", "he", "en")


def _present(case: dict[str, Any], field: str) -> bool:
    """True iff the case already has a non-empty value for the field."""
    v = case.get(field)
    return bool(v and str(v).strip())


def _existing_transliteration_keys(case: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Set of (target_script, source_script, value) already in
    ``name_transliterations``. Used for idempotency."""
    out: set[tuple[str, str, str]] = set()
    for t in case.get("name_transliterations") or []:
        if isinstance(t, dict):
            out.add((
                t.get("target_script"),
                t.get("source_script"),
                t.get("value"),
            ))
    return out


def enrich_case_with_transliterations(case: dict[str, Any]) -> dict[str, Any]:
    """Append transliterated forms for any missing ``victim_name_*`` slot.

    Mutates and returns ``case`` for chaining. Idempotent — re-running
    on an already-enriched case adds no duplicate entries.
    """
    field_for_script = {
        "ar": "victim_name_ar",
        "he": "victim_name_he",
        "en": "victim_name_en",
    }

    # Which scripts have a source-attested value?
    attested: dict[str, str] = {}
    for sc in _TARGET_SCRIPTS:
        if _present(case, field_for_script[sc]):
            attested[sc] = case[field_for_script[sc]].strip()

    if not attested:
        return case  # nothing to transliterate from

    # Pick the best source for each missing target.
    # Priority: prefer Arabic source (most source data is Arabic),
    # then Hebrew, then English. The transliterator handles ar→he,
    # he→ar, ar→en, he→en.
    source_priority = ["ar", "he", "en"]

    transliterations = list(case.get("name_transliterations") or [])
    existing_keys = _existing_transliteration_keys(case)

    for target_script in _TARGET_SCRIPTS:
        if target_script in attested:
            continue  # already source-attested; never overwrite

        # Find the best available source script.
        source_value = None
        source_script = None
        for sc in source_priority:
            if sc == target_script:
                continue
            if sc in attested:
                source_value = attested[sc]
                source_script = sc
                break

        if not source_value or not source_script:
            continue

        result = transliterate(source_value, source_script, target_script)
        if result is None:
            continue
        value, method = result
        if not value:
            continue

        key = (target_script, source_script, value)
        if key in existing_keys:
            continue   # idempotent

        transliterations.append({
            "value": value,
            "target_script": target_script,
            "source_script": source_script,
            "method": method,
            "source_value": source_value,
        })
        existing_keys.add(key)

    case["name_transliterations"] = transliterations
    return case


def enrich_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch helper. Returns the same list with each case enriched in-place."""
    for c in cases:
        enrich_case_with_transliterations(c)
    return cases
