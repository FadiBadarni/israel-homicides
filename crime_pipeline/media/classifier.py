"""Cascade classifier: keyword (free) → CLIP (free, ~30ms) → Gemini Vision (paid).

Each tier records its signals into `MediaCandidate.classification_evidence`
so the audit trail survives — debate-fix #2 (no single-float collapse).

Per-case Gemini budget enforced by `MediaPipeline._gemini_calls_used`.
"""
from __future__ import annotations

import importlib
import re
import threading

# Matches photo-credit / byline attribution prefixes that appear before a
# person's name in captions/alt-text.  When the ONLY occurrence of a victim or
# suspect name in a caption is immediately after one of these markers, the
# image depicts the photographer/author, not the named person.
#
# Examples that should NOT trigger caption-name classification:
#   "צילום: סמיר חסן"        (Photo: Samir Hassan — journalist credit)
#   "Photo by Ali Yassin"     (photographer byline)
#   "© Mako / כתב: רן לוי"   (reporter credit)
_PHOTO_CREDIT_RE = re.compile(
    r"(?:"
    r"photo(?:graph(?:er)?)?(?:\s+by)?[\s:]*"  # Photo by / Photograph:
    r"|צילום\s*[:\-]?"                          # Hebrew: Photography
    r"|תצלום\s*[:\-]?"                          # Hebrew variant
    r"|תמונה\s*[:\-]?"                          # Hebrew: Image
    r"|כתב\s*[:\-]"                             # Hebrew: Reporter:
    r"|מאת\s*[:\-]?"                            # Hebrew: By
    r"|כתבת?\s*[:\-]"                           # Hebrew: Correspondent:
    r"|תחקיר\s*[:\-]"                           # Hebrew: Investigation:
    r"|تصوير\s*[:\-]?"                          # Arabic: Photography
    r"|مصور\s*[:\-]?"                           # Arabic: Photographer
    r"|كاميرا\s*[:\-]?"                         # Arabic: Camera
    r"|©\s*"                                    # copyright symbol
    r"|by\s+"                                   # English "by [name]"
    r"|reporter\s*[:\-]?"                       # English: Reporter
    r")",
    re.VERBOSE | re.IGNORECASE | re.UNICODE,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from crime_pipeline.media.models import MediaCandidate, MediaCategory
from crime_pipeline.media.settings import MediaSettings

log = structlog.get_logger()


# Image bytes arrive from untrusted news sources. PIL is a frequent target of
# decompression-bomb and parser CVEs (CVE-2023-50447 class). We cap pixel
# count and restrict to a small allow-list of common web image formats — the
# downloader already bounds file size, but a 5 MB PNG can still decode to
# gigapixels without this guard.
_CLIP_MAX_IMAGE_PIXELS = 50_000_000
_CLIP_ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")


class _ClipImageDecodeError(Exception):
    """Raised by _ClipRuntime.classify when image bytes can't be safely decoded."""


@dataclass
class _ClipRuntime:
    model: object
    preprocess: object
    tokenizer: object

    def classify(
        self,
        image_path: str,
        prompts: dict[MediaCategory, str],
    ) -> tuple[MediaCategory, float, list[float]]:
        pil_image = importlib.import_module("PIL.Image")
        torch = importlib.import_module("torch")

        # Apply pixel-count cap — module-level setting on PIL.Image, idempotent.
        pil_image.MAX_IMAGE_PIXELS = _CLIP_MAX_IMAGE_PIXELS
        try:
            with pil_image.open(image_path) as img:
                if img.format not in _CLIP_ALLOWED_FORMATS:
                    raise _ClipImageDecodeError(f"unsupported_format:{img.format}")
                image = img.convert("RGB")
                image_tensor = self.preprocess(image).unsqueeze(0)
        except _ClipImageDecodeError:
            raise
        except (
            pil_image.DecompressionBombError,
            pil_image.UnidentifiedImageError,
            OSError,
            ValueError,
        ) as exc:
            raise _ClipImageDecodeError(f"{type(exc).__name__}:{str(exc)[:64]}") from exc

        text_tensor = self.tokenizer(list(prompts.values()))

        # inference_mode is stricter than no_grad — disables both autograd
        # tracking AND view-tracking, eliminating accidental gradient retention.
        with torch.inference_mode():
            image_features = self.model.encode_image(image_tensor)
            text_features = self.model.encode_text(text_tensor)

        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        similarities = (image_features @ text_features.T)[0]
        best_index = int(similarities.argmax().item())
        best_label = list(prompts.keys())[best_index]
        best_score = float(similarities[best_index].item())
        embedding = image_features[0].tolist()
        return best_label, best_score, embedding


# Bilingual keyword maps. Matches via case-insensitive substring.
# (Hebrew + Arabic are case-invariant, so substring is fine.)
_KEYWORDS_BY_CATEGORY: dict[MediaCategory, list[str]] = {
    "victim_portrait": [
        "victim", "deceased", "killed", "the late",
        "קרבן", "ז\"ל", "המנוח", "הנרצח", "הצעיר שנרצח",
        "الضحية", "المرحوم", "القتيل", "الراحل",
        # Bare forms (without definite article ال) — captions often say
        # "ضحية الجريمة X" (victim of the crime) rather than "الضحية X".
        "ضحية", "قتيل", "شهيد", "مغدور",
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
        "مكان الجريمة", "مسرح الجريمة", "من مكان",
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


# Family-relation phrases that, when followed by a victim/suspect keyword,
# indicate the IMAGE depicts a relative — not the victim themselves.
# e.g. "جد القتيل خالد" = "the grandfather of the deceased Khaled" → the
# photo is of the grandfather, not Khaled. Without this guard the keyword
# scorer matches "القتيل" and falsely classifies relatives as victim_portrait.
_FAMILY_RELATIONS_AR = (
    "جد", "جدة", "والد", "والدة", "أب", "أم",
    "أخ", "أخت", "شقيق", "شقيقة",
    "ابن", "ابنة", "نجل", "نجلة",
    "زوج", "زوجة", "أرملة",
    "عم", "عمة", "خال", "خالة",
)
_VICTIM_KEYWORDS_FOR_RELATION_AR = (
    "القتيل", "الضحية", "المرحوم", "الراحل", "الشهيد",
    "قتيل", "ضحية", "شهيد", "مغدور",
)
_FAMILY_RELATION_PHRASES_AR = tuple(
    f"{rel} {kw}"
    for rel in _FAMILY_RELATIONS_AR
    for kw in _VICTIM_KEYWORDS_FOR_RELATION_AR
)


def _caption_is_relative_of_victim(text: str) -> bool:
    """Return True when the caption names the image subject as a family
    member OF the victim (rather than the victim themselves)."""
    return any(phrase in text for phrase in _FAMILY_RELATION_PHRASES_AR)


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
        self._clip_model: Optional[_ClipRuntime] = None  # lazy loaded
        self._clip_attempted = False
        # Guards lazy CLIP-runtime init against concurrent first-use. Even if
        # MediaPipeline currently classifies sequentially per case, a future
        # batch-encoder path could load the ~600MB model twice without this.
        self._clip_load_lock = threading.Lock()

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

        # Family-relation guard: captions like "جد القتيل خالد عاصلة" name
        # the victim possessively but the IMAGE is of the relative. Skip
        # the victim/suspect classifiers entirely so neither the name-match
        # nor the keyword-match falsely promote the photo to victim_portrait.
        if _caption_is_relative_of_victim(text):
            cand.classification = "other"
            cand.classifier_tier = "keyword"
            cand.classification_confidence = 0.3
            cand.classification_evidence.append("keyword:family_relation_of_victim")
            self._mark_stock_signals(cand, text)
            return

        # Caption-name match → strongest signal.
        # Guard: skip names that appear ONLY in photo-credit / byline context
        # (e.g. "צילום: סמיר" = photographer credit, not subject caption).
        for name in ctx.victim_names:
            if name and name.lower() in text:
                if self._name_only_in_credit(name, text):
                    cand.classification_evidence.append(
                        f"caption_credit_skip:victim:{name[:24]}"
                    )
                    continue
                cand.classification = "victim_portrait"
                cand.classifier_tier = "keyword"
                cand.classification_confidence = 0.92
                cand.classification_evidence.append(f"caption_match:victim_name:{name[:24]}")
                self._mark_stock_signals(cand, text)
                return
        for name in ctx.suspect_names:
            if name and name.lower() in text:
                if self._name_only_in_credit(name, text):
                    cand.classification_evidence.append(
                        f"caption_credit_skip:suspect:{name[:24]}"
                    )
                    continue
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

    @staticmethod
    def _name_only_in_credit(name: str, text: str) -> bool:
        """Return True when every occurrence of *name* in *text* is preceded by a
        photo-credit / byline attribution marker (צילום:, Photo by, ©, כתב:, …).

        This guards against classifying journalist headshots as victim/suspect
        portraits when the journalist's name happens to match the case subject.
        If the name does not appear in text at all, returns False.
        """
        name_l = name.lower()
        pos = 0
        found = False
        while True:
            idx = text.find(name_l, pos)
            if idx == -1:
                break
            found = True
            # Check the 50-character window immediately before this occurrence.
            window = text[max(0, idx - 50):idx]
            if not _PHOTO_CREDIT_RE.search(window):
                return False  # At least one occurrence is NOT a credit → subject mention
            pos = idx + 1
        return found  # True only if found AND every occurrence was a credit

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
        if not cand.bytes_ref:
            cand.classification_evidence.append("clip:no_bytes_ref")
            return
        if not Path(cand.bytes_ref).exists():
            cand.classification_evidence.append("clip:missing_bytes_ref")
            return

        # Lazy import — CLIP is in the optional [vision] extra. Lock guards
        # against a future concurrent first-use loading the model twice.
        if self._clip_attempted and self._clip_model is None:
            cand.classification_evidence.append("clip:unavailable")
            return
        if self._clip_model is None:
            with self._clip_load_lock:
                # Re-check inside the lock — another thread may have raced us.
                if self._clip_model is None and not self._clip_attempted:
                    self._clip_attempted = True
                    try:
                        self._clip_model = self._load_clip_runtime()
                    except ModuleNotFoundError:
                        self._clip_model = None
                        cand.classification_evidence.append("clip:not_installed")
                        return
                    except Exception as exc:
                        self._clip_model = None
                        log.warning("clip_load_failed", error=str(exc)[:200])
                        cand.classification_evidence.append("clip:load_failed")
                        return
                elif self._clip_model is None:
                    cand.classification_evidence.append("clip:unavailable")
                    return

        try:
            label, score, embedding = self._clip_model.classify(
                cand.bytes_ref,
                self._clip_prompts(ctx),
            )
        except _ClipImageDecodeError as exc:
            # Untrusted bytes from a news site — corrupt, truncated, or a
            # decompression bomb. Degrade gracefully, keep the audit trail.
            cand.classification_evidence.append(f"clip:image_decode_failed:{str(exc)[:48]}")
            return
        cand.clip_embedding = embedding
        cand.classification_evidence.append(f"clip:{label}:{score:.2f}")
        # CLIP only overrides when more confident than the keyword tier.
        # Caption-name match (keyword tier 0.92) should beat CLIP's typical
        # softmax cosines (0.20–0.40) — that's intentional. CLIP's role is
        # to fire when keyword evidence is weak (no caption / no name match).
        if score > cand.classification_confidence:
            cand.classification = label
            cand.classifier_tier = "clip"
            cand.classification_confidence = score

    def _load_clip_runtime(self) -> _ClipRuntime:
        open_clip = importlib.import_module("open_clip")
        model_name = "ViT-B-32"
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained="laion2b_s34b_b79k",
        )
        # eval mode disables dropout/batchnorm-train behavior — matters for
        # embedding stability across runs (otherwise the same image can yield
        # slightly different vectors, breaking phash-style dedup invariants).
        if hasattr(model, "eval"):
            model.eval()
        tokenizer = open_clip.get_tokenizer(model_name)
        return _ClipRuntime(model=model, preprocess=preprocess, tokenizer=tokenizer)

    def _clip_prompts(self, ctx: ArticleContext) -> dict[MediaCategory, str]:
        victim_name = next((name for name in ctx.victim_names if name), "").strip()
        suspect_name = next((name for name in ctx.suspect_names if name), "").strip()
        city_name = next((name for name in ctx.city_names if name), "").strip()

        victim_suffix = f" featuring {victim_name}" if victim_name else ""
        suspect_suffix = f" featuring {suspect_name}" if suspect_name else ""
        city_suffix = f" in {city_name}" if city_name else ""

        return {
            # Victim portraits are typically family snapshots, passport photos, or
            # social-media profile pictures — NOT professional press headshots.
            "victim_portrait": (
                f"family snapshot, passport photo, or social media profile picture "
                f"of a homicide victim displayed in a news memorial{victim_suffix}"
            ),
            # Suspect images are typically mugshots, ID photos, or CCTV stills —
            # not clean professional portraits.
            "suspect_portrait": (
                f"police mugshot, ID photo, or surveillance still of a crime "
                f"suspect published in a news article{suspect_suffix}"
            ),
            "crime_scene": f"news photo of a crime scene with police tape or forensic investigation{city_suffix}",
            "weapon": "close-up news photo of a weapon — firearm, knife, or object used in a violent crime",
            "court": "news photo inside a courtroom showing a defendant in the dock, judges, or lawyers",
            "funeral": "news photo of a funeral procession, mourners in black, or burial ceremony for a crime victim",
            "cctv": "low-resolution security camera still frame or surveillance footage screenshot",
            "police_activity": "uniformed police officers securing a scene, crime tape, or investigators at work",
            # Explicitly includes journalist byline headshots so CLIP can score
            # them away from victim/suspect portrait categories.
            "generic_stock": (
                "generic stock photo, illustration, or professional reporter headshot "
                "used as a news website byline or author avatar photo"
            ),
            "infographic": "news infographic, data chart, diagram, or map graphic",
            "video": "video thumbnail or still frame from a news broadcast or footage",
            "other": "irrelevant, decorative, or unclear image unrelated to the crime",
        }
