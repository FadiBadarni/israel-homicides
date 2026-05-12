"""Explode an ExtractedArticleData into N+1 per-victim virtual records.

Why this is its own module: the explode is a pure function of the
extraction JSON. Keeping it out of pipeline.py lets us unit-test it
without spinning up the orchestrator, the DB, or any LLM client.

Design notes:

* The output is a list of "virtual" extraction dicts that LOOK like
  single-victim extractions to the downstream dedup + merge stages. The
  primary fields (victim_name_ar/he/en, city, incident_date, victim_age,
  victim_gender, victim_outcome) are swapped to the additional victim's
  values for index > 0. Everything else (suspect, motive, evidence,
  media, source attribution) is shared — they describe the article,
  not the individual victim.

* Each virtual record is tagged with ``victim_index`` (0 = primary).
  The composite record ID used downstream is ``f"{ext_id}#{index}"``.

* Single-victim articles produce exactly ONE virtual record (index 0).
  This preserves existing single-victim behavior — the explode step is
  a no-op for articles where additional_victims is empty.

* additional_victims is the SOURCE OF TRUTH. We trust the LLM's
  enumeration. We DO NOT try to infer extra victims by re-parsing the
  body — that's the LLM's job and changing it would silently change
  recall across the existing dataset.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Fields on the parent extraction that an AdditionalVictim record
# OVERRIDES when building a virtual record for index > 0. Anything not
# in this set carries through unchanged from the parent.
_PER_VICTIM_FIELDS = (
    "victim_name",
    "victim_name_ar",
    "victim_name_he",
    "victim_name_en",
    "victim_age",
    "victim_gender",
    "city",
    "incident_date",
    "victim_outcome",
)


def explode_extraction(extracted_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of virtual per-victim dicts.

    Args:
        extracted_json: the dict form of an ExtractedArticleData (as
          stored in extracted_records.extracted_json).

    Returns:
        A list of length 1 + len(additional_victims). Each element is a
        deepcopy of the parent extraction with per-victim fields
        possibly overridden and ``victim_index`` injected. The primary
        victim is always at index 0.

    Edge cases:
        - Missing/None ``additional_victims`` → single record (index 0).
        - Empty ``additional_victims`` list → single record (index 0).
        - additional_victims contains an entry with ALL name fields null
          → SKIPPED. The LLM occasionally emits ``null`` placeholders
          (observed: ``[null, null, null, {...}]``) when it's uncertain
          about victim count. These null entries would otherwise become
          name-less virtual records that the dedup ``either_name_missing``
          rule merges with anything on cosine alone — bridging unrelated
          victim clusters and silently collapsing them.
    """
    additional = extracted_json.get("additional_victims") or []
    if not isinstance(additional, list):
        additional = []

    virtuals: list[dict[str, Any]] = []

    # Index 0 — primary victim. Just a copy with the victim_index tag.
    primary = deepcopy(extracted_json)
    # additional_victims itself is part of the parent payload but isn't
    # meaningful on a per-victim view — strip to keep virtual records
    # tidy and prevent accidental double-explode downstream.
    primary.pop("additional_victims", None)
    primary["victim_index"] = 0
    virtuals.append(primary)

    # Index 1..N — each additional victim. Start from a parent copy and
    # OVERRIDE the per-victim fields. We deliberately do NOT clear
    # fields that aren't in _PER_VICTIM_FIELDS — suspect/motive/evidence/
    # media are article-level and apply to every victim in the article.
    #
    # We SKIP additional_victim entries whose name fields are all null.
    # The LLM occasionally emits `null` placeholders inside the
    # additional_victims array when it's unsure of count vs identity
    # (observed: ``[null, null, null, {...}, {...}]``). Letting those
    # through creates name-less virtual records that the dedup stage's
    # ``either_name_missing`` rule merges with anything on cosine alone
    # — bridging otherwise-distinct victim clusters and silently
    # collapsing them into one canonical case.
    idx = 0
    for av in additional:
        if not isinstance(av, dict):
            continue
        # All name fields must be empty/null to be considered worthless.
        # If at least one name is provided, we keep it.
        has_name = any(
            (av.get(k) or "").strip() if isinstance(av.get(k), str) else av.get(k)
            for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
        )
        if not has_name:
            continue
        idx += 1
        virtual = deepcopy(extracted_json)
        virtual.pop("additional_victims", None)
        for field in _PER_VICTIM_FIELDS:
            if field in av:
                virtual[field] = av[field]
        # Aliases on the parent are for the primary victim's name
        # variants, not the additional's. Clear them so we don't bleed
        # the wrong aliases onto a secondary victim.
        virtual["victim_aliases"] = []
        virtual["victim_index"] = idx
        virtuals.append(virtual)

    return virtuals


def victim_count(extracted_json: dict[str, Any]) -> int:
    """Number of victim records this extraction will explode into.

    Always ≥ 1. Used for funnel/stats accounting where "1 article =
    multiple victims" needs to be visible.
    """
    additional = extracted_json.get("additional_victims") or []
    if not isinstance(additional, list):
        return 1
    return 1 + sum(1 for av in additional if isinstance(av, dict))
