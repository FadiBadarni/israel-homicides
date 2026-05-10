"""media vs media_evidence splitter.

Rule-based decision applied AFTER classification.

is_evidence = True if (any):
  1. caption/alt mentions victim_name OR suspect_name (any script)
  2. category ∈ {crime_scene, cctv, funeral} AND caption mentions case city/neighborhood
  3. category == court AND article was published within ±14 days of incident
  4. category == weapon AND caption contains "evidence" markers
  5. og:image of the article AND no stock signal

is_evidence = False if (any):
  1. is_stock_photo == True with confidence ≥ stock_demotion_min_confidence
  2. category == generic_stock
  3. URL path contains stock-host markers

Debate-fix #5: low-confidence stock detection KEEPS image in evidence with a
`low_confidence_stock` evidence_reason instead of demoting silently.
"""
from __future__ import annotations

from typing import Iterable

from crime_pipeline.media.classifier import ArticleContext
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings


_EVIDENCE_KEYWORDS = (
    "evidence", "seized", "ראיה", "נשק שנתפס", "السلاح المضبوط", "دليل",
)


def split_media(
    candidates: Iterable[MediaCandidate],
    ctx: ArticleContext,
    settings: MediaSettings,
) -> tuple[list[MediaCandidate], list[MediaCandidate]]:
    """Returns (media, media_evidence)."""
    media: list[MediaCandidate] = []
    media_evidence: list[MediaCandidate] = []
    for cand in candidates:
        is_ev, reason = _decide_evidence(cand, ctx, settings)
        cand.is_evidence = is_ev
        cand.evidence_reason = reason
        if is_ev:
            media_evidence.append(cand)
        else:
            media.append(cand)
    return media, media_evidence


def _decide_evidence(
    cand: MediaCandidate, ctx: ArticleContext, settings: MediaSettings
) -> tuple[bool, str]:
    text = " ".join(filter(None, [
        cand.figcaption, cand.caption, cand.alt_text, cand.surrounding_text,
    ])).lower()

    # Stock-photo demotion (debate-fix #5: only demote if confident)
    if cand.is_stock_photo and cand.is_stock_confidence >= settings.stock_demotion_min_confidence:
        return False, f"stock_photo:conf={cand.is_stock_confidence:.2f}"

    if cand.classification == "generic_stock":
        return False, "category:generic_stock"

    # Rule 1: caption-name match
    for name in ctx.victim_names:
        if name and name.lower() in text:
            return True, f"caption_match:victim:{name[:24]}"
    for name in ctx.suspect_names:
        if name and name.lower() in text:
            return True, f"caption_match:suspect:{name[:24]}"

    # Rule 2: scene/cctv/funeral with city mention
    if cand.classification in ("crime_scene", "cctv", "funeral"):
        for city in ctx.city_names:
            if city and city.lower() in text:
                return True, f"category:{cand.classification}+city:{city[:24]}"

    # Rule 4: weapon with evidence keyword
    if cand.classification == "weapon":
        for kw in _EVIDENCE_KEYWORDS:
            if kw.lower() in text:
                return True, f"category:weapon+evidence_kw:{kw[:16]}"

    # Rule 5: og:image lead image (case-specific by editorial convention)
    if cand.discovery_selector.startswith("meta:og:image") and not cand.is_stock_photo:
        return True, "og_image_lead"

    # Low-confidence stock = keep in evidence with warning
    if cand.is_stock_photo and cand.is_stock_confidence < settings.stock_demotion_min_confidence:
        return True, f"low_confidence_stock:conf={cand.is_stock_confidence:.2f}"

    # Default: not evidence (decorative / contextual)
    return False, "default:no_evidence_signal"
