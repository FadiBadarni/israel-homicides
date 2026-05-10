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


def is_homicide_extraction(
    extraction: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Decide whether an extraction shows enough signal to enter the dedup
    pipeline.

    Returns ``(keep, reason)`` where ``reason`` is a stable machine-readable
    label suitable for stats aggregation.

    Drop rules (conservative — only obvious junk):
    1. ``no_extraction_data`` — input is None or not a mapping (parse-failed
       safety net; should not normally happen because parse-failed extractions
       aren't added to the list, but defensive).
    2. ``no_homicide_signal`` — every signal field is null/empty/placeholder.
       The LLM found nothing identifying the article as a specific incident.
    3. ``victim_survived`` — the LLM (or the lethality fixup) determined
       the victim survived. These are not homicides; the export stage
       already drops them but filtering here saves dedup/merge work.

    Keep rules: anything else, including ``victim_outcome=="critical"`` (the
    victim may die later — let merge/reconcile promote the outcome) and
    ``victim_outcome==None`` with at least one identifying field (the LLM
    sometimes leaves outcome null in arrest-report articles whose underlying
    incident is fatal).
    """
    if not isinstance(extraction, Mapping):
        return False, "no_extraction_data"

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
    outcome = extraction.get("victim_outcome")
    has_death_signal = outcome in ("died", "critical")

    # Rule 1: zero signal across every dimension.
    if not (has_victim or has_city or has_date or has_death_signal):
        return False, "no_homicide_signal"

    # Rule 2: explicitly non-fatal. Export already drops this; filtering here
    # avoids dedup/merge/media/cleanup work for articles that won't ship.
    if outcome == "survived":
        return False, "victim_survived"

    return True, "kept"
