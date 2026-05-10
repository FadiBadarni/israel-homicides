"""
Conflict resolution rules for merging multiple article extractions into a canonical case.

Each resolver function takes a list of (value, source_name, confidence) tuples and returns
(resolved_value, flag_or_None). A flag indicates the merger should record a non-fatal
disagreement on the canonical record (the "flags, not failures" principle).

Source priority (lower = higher priority):
    police (0) > ynet (1) > panet (2)

Suspect status forward-only state machine:
    unknown (0) -> wanted (1) -> arrested (2)
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

# Lower number = higher priority
SOURCE_PRIORITY: dict[str, int] = {"police": 0, "ynet": 1, "panet": 2}
SUSPECT_STATUS_ORDER: dict[str, int] = {"unknown": 0, "wanted": 1, "arrested": 2}


def get_source_priority(source: str) -> int:
    """Return numeric priority for a source name; unknown sources sort last."""
    return SOURCE_PRIORITY.get(source, 99)


def resolve_by_priority(
    values: list[tuple[Any, str, float]],
) -> tuple[Any, Optional[str]]:
    """
    Pick the value from the highest-priority source.

    Args:
        values: list of (value, source_name, confidence_score) tuples. Null values
            are filtered out before resolution.

    Returns:
        (resolved_value, flag) where flag is "field_conflict" if non-null sources
        disagreed, otherwise None. Returns (None, None) when no non-null values exist.
    """
    non_null = [(v, s, c) for v, s, c in values if v is not None]
    if not non_null:
        return None, None

    # Sort by source priority (asc), then by confidence (desc)
    sorted_vals = sorted(non_null, key=lambda x: (get_source_priority(x[1]), -x[2]))
    chosen = sorted_vals[0][0]

    # Detect disagreement among non-null values
    distinct = {repr(v) for v, _, _ in non_null}
    if len(distinct) > 1:
        return chosen, "field_conflict"
    return chosen, None


def resolve_age(
    values: list[tuple[Any, str, float]],
) -> tuple[Optional[int], Optional[str]]:
    """
    Resolve victim age across sources.

    Strategy:
      - If the spread between min and max ages exceeds 5 years, prefer the police
        value (if present) and flag the conflict; otherwise return any value and flag.
      - Otherwise return the modal (most-common) value with no flag.
    """
    age_tuples = [(v, s) for v, s, _ in values if v is not None]
    if not age_tuples:
        return None, None

    ages = [v for v, _ in age_tuples]
    if max(ages) - min(ages) > 5:
        # Prefer police source if available
        police_ages = [v for v, s in age_tuples if s == "police"]
        if police_ages:
            return police_ages[0], "age_conflict"
        return ages[0], "age_conflict"

    # Mode (most common); ties broken by first occurrence
    most_common = Counter(ages).most_common(1)[0][0]
    return most_common, None


def resolve_status(
    values: list[tuple[Any, str, float]],
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve suspect_status using a forward-only state machine.

    Always returns the maximum status on the order scale (unknown < wanted < arrested).
    If sources reported different statuses, a "status_regression" flag is set so
    downstream review can confirm the progression.
    """
    statuses = [v for v, _, _ in values if v is not None]
    if not statuses:
        return None, None

    chosen = max(statuses, key=lambda s: SUSPECT_STATUS_ORDER.get(s, -1))
    distinct_orders = {SUSPECT_STATUS_ORDER.get(s, -1) for s in statuses}
    if len(distinct_orders) > 1:
        return chosen, "status_regression"
    return chosen, None


def resolve_boolean_or(
    values: list[tuple[Any, str, float]],
) -> tuple[Optional[bool], Optional[str]]:
    """
    Resolve a boolean context flag (e.g. organized_crime, family_dispute).

    Uses OR-semantics: returns True if any source reported True. Returns False if all
    non-null sources reported False, and None if no source reported a value.
    """
    booleans = [v for v, _, _ in values if v is not None]
    if not booleans:
        return None, None
    if any(booleans):
        return True, None
    return False, None


VICTIM_OUTCOME_ORDER: dict[str, int] = {
    "survived": 0,
    "critical": 1,
    "unknown": 2,
    "died": 3,
}


def resolve_victim_outcome(
    values: list[tuple[Any, str, float]],
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve victim_outcome using fatal-first semantics.

    If any source confirms "died", the incident is a homicide — that wins.
    Otherwise the most severe reported outcome is returned.
    Returns "outcome_conflict" flag when sources disagree.
    """
    outcomes = [v for v, _, _ in values if v is not None]
    if not outcomes:
        return None, None

    distinct = set(outcomes)
    if "died" in distinct:
        flag = "outcome_conflict" if len(distinct) > 1 else None
        return "died", flag

    chosen = max(outcomes, key=lambda o: VICTIM_OUTCOME_ORDER.get(o, -1))
    flag = "outcome_conflict" if len(distinct) > 1 else None
    return chosen, flag


def resolve_count(
    values: list[tuple[Any, str, float]],
) -> tuple[int, Optional[str]]:
    """
    Resolve num_victims.

    The highest reported count wins (later updates typically increase the count as
    additional victims are confirmed). Disagreement among sources sets a
    "victim_count_conflict" flag. Defaults to 1 when no source reported a count.
    """
    counts = [v for v, _, _ in values if v is not None and v > 0]
    if not counts:
        return 1, None
    if len(set(counts)) > 1:
        return max(counts), "victim_count_conflict"
    return counts[0], None
