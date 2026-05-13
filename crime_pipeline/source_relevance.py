"""Deterministic source-to-case relevance gates.

The embedding deduper is intentionally recall-oriented: if two articles are
semantically close, it may cluster them even when one is only a roundup or a
nearby homicide. This module applies a stricter identity check before a source
article is allowed to contribute to a canonical case.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

import structlog

from crime_pipeline.dedup.name_normalizer import (
    jaro_winkler_similarity,
    romanize_name,
)
from crime_pipeline.utils.gazetteer import normalize_city

log = structlog.get_logger()

_VICTIM_NAME_FIELDS = (
    "victim_name_ar",
    "victim_name_he",
    "victim_name_en",
    "victim_name",
)
_NAME_DESCRIPTOR_ROMAN_TOKENS = {
    "lshb",      # الشاب
    "lfty",      # الفتى
    "lmrhwm",    # المرحوم
    "lmghdwr",   # المغدور
    "ldhyh",     # الضحية
    "lqtyl",     # القتيل
}
_TOKEN_MATCH_THRESHOLD = 0.72


def best_victim_name(data: Mapping[str, Any]) -> str | None:
    """Return the strongest victim-name field available on an extraction."""
    for field in _VICTIM_NAME_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_weak_body_only_virtual(
    virtual: Mapping[str, Any],
    primary: Mapping[str, Any],
    *,
    article_title: str | None,
    article_url: str | None,
) -> tuple[bool, str]:
    """Detect additional-victim records that are only roundup/body mentions.

    ``explode_extraction`` emits one virtual record per victim. That is right
    for true multi-victim incidents, but Arab48 roundup articles often list
    prior unrelated victims in the body. If an additional victim is absent from
    the page identity and does not share the primary article's incident anchor,
    it should not become a source for that victim's canonical case.
    """
    victim_index = int(virtual.get("victim_index") or 0)
    if victim_index == 0:
        return False, "primary_virtual"

    victim_name = best_victim_name(virtual)
    if not victim_name:
        return False, "no_victim_name"

    identity = " ".join(part for part in (article_title, article_url) if part)
    if _text_mentions_name(identity, victim_name):
        return False, "page_identity_mentions_victim"

    if _shares_primary_incident_anchor(virtual, primary):
        return False, "shares_primary_incident_anchor"

    return True, "body_only_additional_victim"


def refine_source_groups(
    groups: Iterable[list[str]],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[list[str]]:
    """Split dedup groups whose members fail source-to-case relevance.

    Named records drive the split. Unnamed records are attached only when they
    unambiguously match a single named component; otherwise they remain their
    own singleton so they cannot bridge unrelated victims.
    """
    refined: list[list[str]] = []
    split_count = 0
    for group in groups:
        parts = _refine_one_group(group, records_by_id)
        refined.extend(parts)
        if len(parts) > 1:
            split_count += 1
            log.info(
                "source_relevance_group_split",
                original_size=len(group),
                refined_sizes=[len(p) for p in parts],
            )
    if split_count:
        log.info("source_relevance_refined", split_groups=split_count)
    return refined


def records_same_case(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> tuple[bool, str]:
    """Return whether two extracted article records describe the same case."""
    left_name = _clean(left.get("victim_name"))
    right_name = _clean(right.get("victim_name"))
    if left_name and right_name:
        ok, reason = victim_names_compatible(left_name, right_name)
        if not ok:
            return False, reason
        # A strong name match can survive follow-up date gaps and small place
        # disagreements, because legal updates often restate the incident from
        # a different angle. Still reject impossible same-name collisions when
        # both city and date are clearly different.
        if _cities_conflict(left.get("city"), right.get("city")):
            left_dt = _coerce_date(left.get("incident_date"))
            right_dt = _coerce_date(right.get("incident_date"))
            if left_dt and right_dt and abs((left_dt - right_dt).days) > 30:
                return False, "city_and_date_mismatch"
        return True, "victim_name_match"

    # If only one side has a name, require that name to appear in the other
    # article identity text; otherwise same-city breaking blurbs can bridge
    # unrelated victims.
    known_name = left_name or right_name
    unknown = right if left_name else left
    if known_name and _text_mentions_name(_identity_text(unknown), known_name):
        return _city_date_gate(left, right, loose=True)

    if not known_name:
        return _city_date_gate(left, right, loose=False)

    return False, "missing_name_without_identity_signal"


def text_mentions_victim(text: str, victim_name: str) -> bool:
    """Public wrapper for tests and callers that need page-identity checks."""
    return _text_mentions_name(text, victim_name)


def victim_names_compatible(left: str, right: str) -> tuple[bool, str]:
    """Strict victim-name compatibility for source membership."""
    if left == right:
        return True, "name_exact"

    left_tokens = _roman_tokens(left)
    right_tokens = _roman_tokens(right)
    if left_tokens and left_tokens == right_tokens:
        return True, "name_tokens_exact"

    score = jaro_winkler_similarity(left, right)
    if score >= 0.88:
        return True, f"name_jaro:{score:.2f}"

    if not left_tokens or not right_tokens:
        return False, f"name_mismatch:jaro:{score:.2f}"

    shared = _shared_token_count(left_tokens, right_tokens)
    if shared >= 2 and _edge_tokens_align(left_tokens, right_tokens, shared):
        return True, "name_shared_edge_tokens"

    return False, f"name_mismatch:jaro:{score:.2f}"


def _refine_one_group(
    group: list[str],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[list[str]]:
    if len(group) <= 1:
        return [group]

    named = [
        rid for rid in group
        if _clean(records_by_id.get(rid, {}).get("victim_name"))
    ]
    unnamed = [rid for rid in group if rid not in named]

    if not named:
        return _connected_components(group, records_by_id)

    components = _connected_components(named, records_by_id)

    for rid in unnamed:
        record = records_by_id.get(rid, {})
        matches: list[int] = []
        for idx, component in enumerate(components):
            if any(records_same_case(record, records_by_id[cid])[0] for cid in component):
                matches.append(idx)
        if len(matches) == 1:
            components[matches[0]].append(rid)
        else:
            components.append([rid])

    return components


def _connected_components(
    ids: list[str],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[list[str]]:
    parent = {rid: rid for rid in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, left_id in enumerate(ids):
        for right_id in ids[i + 1:]:
            left = records_by_id.get(left_id, {})
            right = records_by_id.get(right_id, {})
            if records_same_case(left, right)[0]:
                union(left_id, right_id)

    out: dict[str, list[str]] = {}
    for rid in ids:
        out.setdefault(find(rid), []).append(rid)
    return list(out.values())


def _city_date_gate(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    loose: bool,
) -> tuple[bool, str]:
    if _cities_conflict(left.get("city"), right.get("city")):
        return False, "city_mismatch"

    left_dt = _coerce_date(left.get("incident_date"))
    right_dt = _coerce_date(right.get("incident_date"))
    if left_dt and right_dt:
        max_days = 14 if loose else 3
        delta = abs((left_dt - right_dt).days)
        if delta > max_days:
            return False, f"date_mismatch:{delta}d"

    if left.get("city") or right.get("city") or left_dt or right_dt:
        return True, "city_date_match"
    return False, "no_identity_signal"


def _shares_primary_incident_anchor(
    virtual: Mapping[str, Any],
    primary: Mapping[str, Any],
) -> bool:
    virtual_city = virtual.get("city")
    primary_city = primary.get("city")
    if virtual_city and primary_city and not _cities_conflict(virtual_city, primary_city):
        return True

    virtual_dt = _coerce_date(virtual.get("incident_date"))
    primary_dt = _coerce_date(primary.get("incident_date"))
    if virtual_dt and primary_dt and abs((virtual_dt - primary_dt).days) <= 1:
        return True

    return False


def _cities_conflict(left: Any, right: Any) -> bool:
    left_s = _clean(left)
    right_s = _clean(right)
    if not left_s or not right_s:
        return False

    left_norm = normalize_city(left_s)
    right_norm = normalize_city(right_s)
    if left_norm and right_norm:
        return left_norm.get("name_en") != right_norm.get("name_en")

    return romanize_name(left_s) != romanize_name(right_s)


def _text_mentions_name(text: str, name: str) -> bool:
    text_rom = romanize_name(text)
    if not text_rom:
        return False
    tokens = _roman_tokens(name)
    if not tokens:
        return False
    required = 1 if len(tokens) == 1 else 2
    return sum(1 for token in tokens if token in text_rom) >= required


def _identity_text(record: Mapping[str, Any]) -> str:
    parts = [
        record.get("url"),
        record.get("title"),
        record.get("article_text"),
    ]
    return " ".join(str(part) for part in parts if part)


def _roman_tokens(value: str) -> list[str]:
    return [
        t for t in romanize_name(value).split()
        if len(t) >= 2 and t not in _NAME_DESCRIPTOR_ROMAN_TOKENS
    ]


def _edge_tokens_align(
    left_tokens: list[str],
    right_tokens: list[str],
    shared_count: int,
) -> bool:
    if not left_tokens or not right_tokens:
        return False
    return (
        _tokens_match(left_tokens[0], right_tokens[0])
        or _tokens_match(left_tokens[-1], right_tokens[-1]) and shared_count >= 3
    )


def _shared_token_count(left_tokens: list[str], right_tokens: list[str]) -> int:
    remaining = list(right_tokens)
    count = 0
    for left in left_tokens:
        for idx, right in enumerate(remaining):
            if _tokens_match(left, right):
                count += 1
                remaining.pop(idx)
                break
    return count


def _tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        import jellyfish

        return jellyfish.jaro_winkler_similarity(left, right) >= _TOKEN_MATCH_THRESHOLD
    except Exception:
        return False


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _clean(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
