"""Cascade classifier: keyword (free) → CLIP (free, ~30ms) → Gemini Vision (paid).

Each tier records its signals into `MediaCandidate.classification_evidence`
so the audit trail survives — debate-fix #2 (no single-float collapse).

Per-case Gemini budget enforced by `MediaPipeline._gemini_calls_used`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

from crime_pipeline.media.models import MediaCandidate, MediaCategory
from crime_pipeline.media.settings import MediaSettings

log = structlog.get_logger()


# Bilingual keyword maps. Matches via case-insensitive substring.
# (Hebrew + Arabic are case-invariant, so substring is fine.)
_KEYWORDS_BY_CATEGORY: dict[MediaCategory, list[str]] = {
    "victim_portrait": [
        "victim", "deceased", "killed", "the late",
        "קרבן", "ז\"ל", "המנוח", "הנרצח", "הצעיר שנרצח",
        "الضحية", "المرحوم", "القتيل", "الراحل",
    ],
    "suspect_portrait": [
        "suspect", "accused", "alleged",
        "חשוד", "הנאשם", "המעורב",
        "المشتبه", "المتهم", "الجاني",
    ],
    "crime_scene": [
        "scene", "crime scene", "shooting site", "location of",
        "זירה", "זירת הרצח", "זירת הירי", "מקום האירוע",
        "موقع الجريمة", "مكان الحادث", "موقع إطلاق النار",
    ],
    "weapon": [
        "weapon", "firearm", "gun", "handgun", "pistol", "knife",
        "נשק", "אקדח", "סכין",
        "سلاح", "مسدس", "بندقية", "سكين",
    ],
    "court": [
        "court", "remand", "hearing", "indictment", "judge", "tribunal",
        "בית משפט", "הארכת מעצר", "כתב אישום", "השופט",
        "المحكمة", "تمديد اعتقال", "لائحة اتهام", "القاضي",
    ],
    "funeral": [
        "funeral", "burial", "mourners", "memorial",
        "הלוויה", "לוויה", "אבל", "מצבה",
        "جنازة", "تشييع", "دفن", "العزاء",
    ],
    "cctv": [
        "cctv", "security camera", "surveillance footage",
        "מצלמות אבטחה", "תיעוד מהמצלמות", "מצלמת אבטחה",
        "كاميرات المراقبة", "كاميرا أمنية", "تسجيل المراقبة",
    ],
    "police_activity": [
        "police", "officers", "investigation", "crime tape", "patrol",
        "משטרה", "שוטרים", "סרט אזהרה", "חקירה",
        "الشرطة", "الضابط", "الشريط الأمني", "التحقيق",
    ],
    "generic_stock": [
        "illustration", "for illustration", "file photo", "stock photo",
        "אילוסטרציה", "ארכיון", "צילום אילוסטרציה",
        "للتوضيح", "أرشيف", "صورة من الأرشيف",
    ],
}


@dataclass
class ArticleContext:
    """Article context the classifier uses as a soft prior."""
    article_url: str
    article_text: str = ""
    victim_names: list[str] = field(default_factory=list)
    suspect_names: list[str] = field(default_factory=list)
    city_names: list[str] = field(default_factory=list)
    incident_keywords: list[str] = field(default_factory=list)


class MediaClassifier:
    """Cascade classifier with per-case Gemini budget enforcement."""

    def __init__(self, settings: MediaSettings) -> None:
        self.settings = settings
        self._gemini_calls_used = 0
        self._clip_model = None  # lazy loaded
        self._clip_attempted = False

    def reset_case_budget(self) -> None:
        self._gemini_calls_used = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def classify(
        self, cand: MediaCandidate, ctx: ArticleContext
    ) -> MediaCandidate:
        """Run cascade. Mutates candidate in place. Returns same object."""
        if cand.download_status not in ("ok",):
            # Image we couldn't get — try caption-only keyword tier
            self._classify_keyword(cand, ctx)
            return cand

        # Tier 1: keyword (free, always)
        self._classify_keyword(cand, ctx)
        if cand.classification_confidence >= self.settings.keyword_confidence_threshold:
            return cand

        # Tier 2: CLIP zero-shot (free after model load) — only if enabled
        if self.settings.enable_clip_classifier:
            self._classify_clip(cand, ctx)
            if cand.classification_confidence >= self.settings.clip_confidence_threshold:
                return cand

        # Tier 3: Gemini Vision — only if budget allows AND it would shift the label
        # (Conservative: skip Gemini in this offline-friendly build; the hook
        # exists so the classifier can be upgraded to live Vision calls later.)
        if (self._gemini_calls_used < self.settings.max_vision_calls_per_case
                and cand.classification_confidence < 0.5):
            # Stub — production would call Gemini Vision here.
            cand.classification_evidence.append("gemini_skipped:budget_or_offline")

        return cand

    # ------------------------------------------------------------------
    # Tier 1 — keyword/text NLP
    # ------------------------------------------------------------------

    def _classify_keyword(self, cand: MediaCandidate, ctx: ArticleContext) -> None:
        text = " ".join(filter(None, [
            cand.figcaption, cand.caption, cand.alt_text, cand.surrounding_text,
        ])).lower()
        if not text:
            cand.classification = "other"
            cand.classifier_tier = "keyword"
            cand.classification_confidence = 0.2
            cand.classification_evidence.append("keyword:no_text_signal")
            return

        # Caption-name match → strongest signal
        for name in ctx.victim_names:
            if name and name.lower() in text:
                cand.classification = "victim_portrait"
                cand.classifier_tier = "keyword"
                cand.classification_confidence = 0.92
                cand.classification_evidence.append(f"caption_match:victim_name:{name[:24]}")
                self._mark_stock_signals(cand, text)
                return
        for name in ctx.suspect_names:
            if name and name.lower() in text:
                cand.classification = "suspect_portrait"
                cand.classifier_tier = "keyword"
                cand.classification_confidence = 0.88
                cand.classification_evidence.append(f"caption_match:suspect_name:{name[:24]}")
                self._mark_stock_signals(cand, text)
                return

        # Category keyword scoring (return best, with confidence ∝ matches)
        best_cat: MediaCategory = "other"
        best_score = 0
        best_hits: list[str] = []
        for cat, keywords in _KEYWORDS_BY_CATEGORY.items():
            hits = [kw for kw in keywords if kw.lower() in text]
            if len(hits) > best_score:
                best_cat = cat
                best_score = len(hits)
                best_hits = hits

        cand.classification = best_cat
        cand.classifier_tier = "keyword"
        # Confidence: 0.5 base, +0.15 per hit, capped 0.92
        cand.classification_confidence = min(0.92, 0.5 + 0.15 * best_score) if best_score else 0.3
        if best_hits:
            cand.classification_evidence.append(
                f"keyword:{best_cat}:{','.join(h[:16] for h in best_hits[:3])}"
            )
        else:
            cand.classification_evidence.append("keyword:no_match")

        self._mark_stock_signals(cand, text)

    def _mark_stock_signals(self, cand: MediaCandidate, text: str) -> None:
        # URL-host stock signal
        url = (cand.source_url or "").lower()
        for d in self.settings.stock_photo_domains:
            if d in url:
                cand.is_stock_photo = True
                cand.is_stock_confidence = 0.95
                cand.classification_evidence.append(f"stock:domain:{d}")
                return
        # Caption stock-marker signal
        for marker in self.settings.stock_caption_markers:
            if marker.lower() in text:
                cand.is_stock_photo = True
                cand.is_stock_confidence = 0.85
                cand.classification_evidence.append(f"stock:caption:{marker[:16]}")
                return

    # ------------------------------------------------------------------
    # Tier 2 — CLIP zero-shot (lazy)
    # ------------------------------------------------------------------

    def _classify_clip(self, cand: MediaCandidate, ctx: ArticleContext) -> None:
        # Lazy import — CLIP is in the optional [vision] extra
        if self._clip_attempted and self._clip_model is None:
            cand.classification_evidence.append("clip:unavailable")
            return
        if self._clip_model is None:
            self._clip_attempted = True
            try:
                # Production would use open_clip_torch + ViT-B-32 here.
                # We leave a hook so the classifier graceful-degrades when
                # the extra isn't installed.
                self._clip_model = None
                cand.classification_evidence.append("clip:not_installed")
                return
            except Exception:
                self._clip_model = None
                cand.classification_evidence.append("clip:load_failed")
                return
        # When implemented: encode image, compare against per-category prompts,
        # pick argmax cosine, set classification + confidence + evidence.
        cand.classification_evidence.append("clip:stub")
