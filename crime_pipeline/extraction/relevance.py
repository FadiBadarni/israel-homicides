"""
Relevance gate between extract and dedup.

Broad search queries (e.g. ``עראבה רצח``) routinely return tangentially
related articles — opinion pieces, year-end stats, tech news that just
mentions the city in passing. The LLM extractor is told to emit nulls for
unmentioned facts, so these articles produce mostly-null records rather
than refusing to extract. Without a relevance gate, those nulls flow all
the way through dedup → merge → export and ship as junk "cases" with no
victim, no city, no date.

This module defines a single conservative gate: keep anything that shows
*any* signal of being a specific incident, drop only the empties.

Design constraints:
- False-negative ("dropped a real homicide") is much worse than false-
  positive ("junk made it through"). Operators can flag junk; they can't
  recover dropped cases without re-running extraction.
- The gate is pure-function over the extracted dict; no I/O, no API calls.
- The gate runs once per extraction, before dedup blocking, so it must be
  cheap.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# String placeholders the LLM occasionally emits instead of null. We treat
# these as "no value" so the gate isn't fooled by a literal "unknown" in
# every field.
_PLACEHOLDER_STRINGS = frozenset({
    "", "none", "null", "n/a", "na", "unknown",
    "לא ידוע",     # "unknown" (Hebrew)
    "غير معروف",   # "unknown" (Arabic)
})


def _is_present(value: Any) -> bool:
    """True if ``value`` carries actual signal (not None, not a placeholder)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _PLACEHOLDER_STRINGS
    if isinstance(value, (list, tuple, set)):
        return any(_is_present(v) for v in value)
    return True


# Incident-type labels the LLM emits that we keep in the homicide pipeline.
# "attempted_homicide" stays in so reconcile can later promote it to a
# confirmed homicide if a follow-up source confirms death; the export
# filter (``victim_outcome=="survived"``) catches genuine non-fatal cases.
_HOMICIDE_TYPES = frozenset({"homicide", "attempted_homicide"})

# Types that are explicit non-homicide categories. We drop these by name so
# stats show *why* (e.g. ``incident_type:accident``) instead of the catch-all
# "no_homicide_signal" reason.
_NON_HOMICIDE_TYPES = frozenset({
    "accident",
    "suicide",
    "historical",
    "other_crime",
    "non_crime",
})


def is_homicide_extraction(
    extraction: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Decide whether an extraction shows enough signal to enter the dedup
    pipeline.

    Returns ``(keep, reason)`` where ``reason`` is a stable machine-readable
    label suitable for stats aggregation.

    Decision order:
    1. ``no_extraction_data`` — None / non-mapping input (parse-failed safety).
    2. ``incident_type:<X>`` — LLM tagged a non-homicide category (accident,
       suicide, historical retrospective, other_crime, non_crime). These are
       the precision wins — they wouldn't pass the field-level filter
       otherwise because they often have city/date/even outcome=died.
    3. ``no_homicide_signal`` — incident_type is missing/unknown AND every
       signal field is null/empty/placeholder. Fallback for legacy extractions
       made before the discriminator existed and for ambiguous "unknown" types.
    4. ``victim_survived`` — explicit non-fatal outcome. Export filter would
       drop this anyway; filtering here saves dedup/merge work.

    Keep rules: ``incident_type`` in {"homicide", "attempted_homicide"} OR
    (legacy: incident_type missing/"unknown" AND any signal field present).
    """
    if not isinstance(extraction, Mapping):
        return False, "no_extraction_data"

    incident_type = extraction.get("incident_type")

    # Rule 2: explicit non-homicide category from the LLM. Drop with a
    # machine-readable reason so operators can see precision wins by class.
    if incident_type in _NON_HOMICIDE_TYPES:
        return False, f"incident_type:{incident_type}"

    # Survived victims are explicitly non-fatal — drop early.
    if extraction.get("victim_outcome") == "survived":
        return False, "victim_survived"

    # Rule 4: explicit homicide category — keep, no further checks needed.
    if incident_type in _HOMICIDE_TYPES:
        return True, "kept"

    # Legacy / "unknown" path: fall back to field-level signal check so we
    # don't break old extractions and don't silently drop articles the LLM
    # couldn't categorise.
    has_victim = any(
        _is_present(extraction.get(field))
        for field in (
            "victim_name", "victim_name_ar", "victim_name_he",
            "victim_name_en", "victim_aliases",
        )
    )
    has_city = _is_present(extraction.get("city"))
    has_date = _is_present(extraction.get("incident_date")) or _is_present(
        extraction.get("death_date")
    )
    has_death_signal = extraction.get("victim_outcome") in ("died", "critical")

    if not (has_victim or has_city or has_date or has_death_signal):
        return False, "no_homicide_signal"

    return True, "kept"
