"""MediaPipeline orchestrator — wires the media subsystem end-to-end.

Per-case flow:
    1. Harvest candidates from each article's raw_html.
    2. Cap at max_images_per_case to bound bandwidth + classifier work.
    3. Download (with on-disk URL cache for cross-source same-URL hits).
    4. Classify (cascade: keyword → CLIP → Gemini Vision per settings).
    5. Within-article sha256 collapse, then cross-source phash/CLIP clustering.
    6. Fold each cluster into one CanonicalMedia (mirror_urls, source_article_urls,
       appearance_count).
    7. Run media-vs-media_evidence splitter on cluster representatives.
    8. Return (media, media_evidence) lists of CanonicalMedia ready for the
       canonical case JSON.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote, urlparse

import structlog

from crime_pipeline.media.classifier import ArticleContext, MediaClassifier
from crime_pipeline.media.dedup import (
    cluster_across_sources,
    dedup_within_article,
    media_id_for,
    select_canonical,
)
from crime_pipeline.media.downloader import MediaDownloader
from crime_pipeline.media.harvester import MediaHarvester
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings
from crime_pipeline.media.splitter import split_media
from crime_pipeline.models import CanonicalMedia

log = structlog.get_logger()


_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u0590-\u05FF\u0600-\u06FF]+", re.UNICODE)

_PERSON_MARKERS = (
    "victim", "deceased", "the late", "killed",
    "המנוח", "הנרצח", "הקורבן", "ז״ל", 'ז"ל',
    "المرحوم", "المغدور", "الضحية", "ضحية", "الشهيد", "الراحل",
    "القتيل", "قتيل", "ضحيّتا", "ضحيتا",
)

_LOCATION_MARKERS = (
    "crime scene", "shooting scene", "shooting site", "scene of the crime",
    "זירת הרצח", "זירת הירי", "מקום האירוע", "זירה",
    "موقع الجريمة", "مكان الجريمة", "موقع إطلاق النار", "مكان الحادث",
    "مسرح الجريمة",
)


class MediaPipeline:
    """End-to-end media orchestrator. One instance per case is fine."""

    def __init__(self, settings: Optional[MediaSettings] = None) -> None:
        self.settings = settings or MediaSettings()
        self.harvester = MediaHarvester(self.settings)
        self.downloader = MediaDownloader(self.settings)
        self.classifier = MediaClassifier(self.settings)

    async def run_for_case(
        self,
        articles: list[dict],
        ctx: ArticleContext,
    ) -> tuple[list[CanonicalMedia], list[CanonicalMedia]]:
        """Process all articles for one case → (media, media_evidence).

        ``articles`` is a list of dicts shaped like::

            {"raw_html": str, "url": str, "article_text": str | None}

        Returns two lists of CanonicalMedia (decorative vs evidentiary).
        """
        if not self.settings.enabled or not articles:
            return [], []

        self.classifier.reset_case_budget()

        # ── 1. Harvest from each article ──────────────────────────────
        all_cands: list[MediaCandidate] = []
        for art in articles:
            html = art.get("raw_html") or ""
            base_url = art.get("url") or ""
            if not html or not base_url:
                continue
            try:
                cands = self.harvester.harvest(html, base_url, ctx)
            except Exception as e:
                log.warning("media_harvest_error", url=base_url, error=str(e))
                continue
            all_cands.extend(cands)

        if not all_cands:
            return [], []

        # ── 2. Cap per case ───────────────────────────────────────────
        if self.settings.precision_mode:
            before = len(all_cands)
            all_cands = self._drop_explicit_mismatches(all_cands, ctx)
            dropped = before - len(all_cands)
            if dropped:
                log.info("media_precision_prefilter", dropped=dropped, kept=len(all_cands))
            if not all_cands:
                return [], []

        if len(all_cands) > self.settings.max_images_per_case:
            log.info(
                "media_cap_applied",
                discovered=len(all_cands),
                cap=self.settings.max_images_per_case,
            )
            all_cands = all_cands[: self.settings.max_images_per_case]

        # ── 3. Download (bounded concurrency, shared cache) ───────────
        try:
            all_cands = await self.downloader.fetch_many(all_cands)
        except Exception as e:
            log.warning("media_download_batch_error", error=str(e))

        # Only downloaded, hashable images are useful for the canonical media
        # gallery. Keeping failed URLs here creates broken UI tiles and noisy
        # singleton media records.
        failed_downloads = [c for c in all_cands if c.download_status != "ok"]
        if failed_downloads:
            log.info(
                "media_downloads_dropped",
                count=len(failed_downloads),
                statuses=sorted({c.download_status for c in failed_downloads}),
            )
        all_cands = [c for c in all_cands if c.download_status == "ok"]
        if not all_cands:
            return [], []

        # ── 4. Classify ───────────────────────────────────────────────
        for cand in all_cands:
            try:
                await self.classifier.classify(cand, ctx)
            except Exception as e:
                # Classifier failures must not break the case; record + continue.
                cand.classification = cand.classification or "other"
                cand.classifier_tier = cand.classifier_tier or "keyword"
                cand.classification_evidence.append(f"classify_error:{str(e)[:80]}")

        # ── 5. Dedup: within-article (sha256), then cross-source ──────
        # Group by source_article_url so within-article sha256 collapse does
        # NOT swallow byte-identical images shared across publishers (which
        # would erase the appearance_count signal we rely on downstream).
        by_article: dict[str, list[MediaCandidate]] = {}
        for cand in all_cands:
            by_article.setdefault(cand.source_article_url, []).append(cand)
        deduped: list[MediaCandidate] = []
        for article_cands in by_article.values():
            deduped.extend(dedup_within_article(article_cands, self.settings))
        all_cands = deduped
        clusters = cluster_across_sources(all_cands, self.settings)

        # ── 6 + 7. Pick rep, split, fold to CanonicalMedia ────────────
        reps_with_clusters: list[tuple[MediaCandidate, list[MediaCandidate]]] = [
            (select_canonical(cluster), cluster) for cluster in clusters
        ]
        # Promote cluster-level provenance signals onto each rep so the
        # splitter's "og_image_lead" rule survives canonical-rep selection.
        # Without this, a cluster containing a head-meta og:image AND a
        # higher-resolution body figure of the same image picks the figure
        # as rep (larger area), erasing the lead-image origin signal.
        for rep, cluster in reps_with_clusters:
            cluster_selectors = {c.discovery_selector for c in cluster}
            if any(s.startswith("meta:og:image") for s in cluster_selectors):
                rep.discovery_selector = "meta:og:image"
            elif any(s.startswith("meta:twitter:image") for s in cluster_selectors):
                rep.discovery_selector = "meta:twitter:image"
        reps = [rep for rep, _ in reps_with_clusters]
        # split_media mutates each rep's is_evidence + evidence_reason in place.
        split_media(reps, ctx, self.settings)

        # Corroboration check: og:image-only evidence from a SINGLE publisher
        # is unreliable — many news sites emit a column hero image, byline
        # photo, or section banner as og:image. Without independent
        # corroboration (caption-name match, cross-publisher mirroring), we
        # demote it to decorative. Caption-name-matched og:images keep their
        # "caption_match:victim:..." reason and pass through unchanged.
        for rep, cluster in reps_with_clusters:
            if rep.evidence_reason == "og_image_lead":
                publishers = self._distinct_publishers(cluster)
                if len(publishers) < 2:
                    rep.is_evidence = False
                    rep.evidence_reason = "og_image_lead:single_publisher_unverified"

        media_canon: list[CanonicalMedia] = []
        evidence_canon: list[CanonicalMedia] = []
        for rep, cluster in reps_with_clusters:
            if self.settings.precision_mode:
                if not self._keep_precise_media(rep, cluster, ctx):
                    continue
                self._suppress_captionless_lead_type(rep)
            cm = self._build_canonical(rep, cluster)
            if rep.is_evidence:
                evidence_canon.append(cm)
            else:
                media_canon.append(cm)

        log.info(
            "media_finalize",
            harvested=sum(1 for _ in all_cands),
            clusters=len(clusters),
            media=len(media_canon),
            evidence=len(evidence_canon),
        )
        return media_canon, evidence_canon

    # ------------------------------------------------------------------
    # Precision filters
    # ------------------------------------------------------------------

    def _drop_explicit_mismatches(
        self,
        candidates: list[MediaCandidate],
        ctx: ArticleContext,
    ) -> list[MediaCandidate]:
        """Drop candidates whose own caption/alt clearly points elsewhere."""
        out: list[MediaCandidate] = []
        for cand in candidates:
            reason = self._mismatch_reason(cand, ctx)
            if reason:
                log.debug(
                    "media_precision_drop",
                    reason=reason,
                    source_url=cand.source_url,
                    article_url=cand.source_article_url,
                )
                continue
            out.append(cand)
        return out

    def _keep_precise_media(
        self,
        rep: MediaCandidate,
        cluster: list[MediaCandidate],
        ctx: ArticleContext,
    ) -> bool:
        """Final precision gate before persisting CanonicalMedia.

        Evidence items survive. Decorative media must still carry some
        case-specific text signal; otherwise roundup/related-article images
        enter every exploded victim case.
        """
        if self._mismatch_reason(rep, ctx):
            return False
        if rep.is_evidence:
            return True
        if rep.classification == "generic_stock":
            return False

        text = self._candidate_text(rep)
        if not text:
            return self._is_captionless_case_named_lead(rep, cluster, ctx)

        text_norm = self._normalise(text)
        if self._contains_case_signal(text_norm, ctx):
            return True

        # Unverified lead images are intentionally not persisted in precision
        # mode; they are often publisher chrome or roundup thumbnails.
        if rep.evidence_reason == "og_image_lead:single_publisher_unverified":
            return False

        # A meaningful category without any victim/city signal is still too
        # loose for canonical output.
        return False

    def _suppress_captionless_lead_type(self, rep: MediaCandidate) -> None:
        """Avoid over-labeling source-approved leads with no image caption."""
        if self._candidate_text(rep):
            return
        if rep.is_evidence:
            return
        if not rep.discovery_selector.startswith("arab48:case_named_lead"):
            return
        if rep.classification in (None, "other", "generic_stock"):
            return
        rep.classification_evidence.append(
            f"precision:captionless_lead_type_suppressed:{rep.classification}"
        )
        rep.classification = "other"
        rep.classification_confidence = min(rep.classification_confidence, 0.35)

    def _mismatch_reason(self, cand: MediaCandidate, ctx: ArticleContext) -> str | None:
        text = self._candidate_text(cand)
        if not text:
            return None
        text_norm = self._normalise(text)

        if self._has_marker(text_norm, _PERSON_MARKERS) and ctx.victim_names:
            if not self._contains_identity_signal(text_norm, ctx):
                return "caption_names_other_victim"

        city_tokens = self._tokens(ctx.city_names)
        if self._has_marker(text_norm, _LOCATION_MARKERS) and city_tokens:
            if not self._contains_any(text_norm, city_tokens):
                return "caption_mentions_other_location"

        return None

    def _is_captionless_case_named_lead(
        self,
        rep: MediaCandidate,
        cluster: list[MediaCandidate],
        ctx: ArticleContext,
    ) -> bool:
        if self._candidate_text(rep):
            return False
        if rep.classification == "generic_stock":
            return False
        if rep.discovery_selector.startswith("arab48:case_named_lead"):
            return True

        if not rep.discovery_selector.startswith(("meta:og:image", "meta:twitter:image")):
            return False

        for cand in cluster:
            article_norm = self._normalise(unquote(cand.source_article_url or ""))
            if self._contains_identity_signal(article_norm, ctx):
                return True
        return False

    @staticmethod
    def _candidate_text(cand: MediaCandidate) -> str:
        return " ".join(
            part for part in (
                cand.figcaption, cand.caption, cand.alt_text, cand.surrounding_text,
            )
            if part
        ).strip()

    @classmethod
    def _contains_case_signal(cls, text_norm: str, ctx: ArticleContext) -> bool:
        return (
            cls._contains_identity_signal(text_norm, ctx)
            or cls._contains_any(text_norm, cls._tokens(ctx.city_names))
        )

    @classmethod
    def _contains_identity_signal(cls, text_norm: str, ctx: ArticleContext) -> bool:
        for name in ctx.victim_names + ctx.suspect_names:
            tokens = cls._tokens([name])
            if not tokens:
                continue
            matched = sum(1 for token in tokens if token in text_norm)
            required = 1 if len(tokens) == 1 else 2
            if matched >= required:
                return True
        return False

    @classmethod
    def _tokens(cls, values: list[str]) -> set[str]:
        tokens: set[str] = set()
        for value in values:
            value_norm = cls._normalise(value)
            for token in _TOKEN_RE.findall(value_norm):
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    @classmethod
    def _contains_any(cls, text_norm: str, tokens: set[str]) -> bool:
        return any(token in text_norm for token in tokens)

    @classmethod
    def _has_marker(cls, text_norm: str, markers: tuple[str, ...]) -> bool:
        return any(cls._normalise(marker) in text_norm for marker in markers)

    @staticmethod
    def _normalise(value: str) -> str:
        out = _ARABIC_DIACRITICS_RE.sub("", value.lower())
        return (
            out.replace("أ", "ا")
            .replace("إ", "ا")
            .replace("آ", "ا")
            .replace("ى", "ي")
            .replace("ة", "ه")
            .replace("ؤ", "و")
            .replace("ئ", "ي")
        )

    # ------------------------------------------------------------------
    # Cluster → CanonicalMedia
    # ------------------------------------------------------------------

    @staticmethod
    def _distinct_publishers(cluster: list[MediaCandidate]) -> set[str]:
        """Return the set of distinct publisher hosts (e.g. 'haaretz.co.il',
        'mako.co.il') that the cluster's source articles span. Used as a
        corroboration signal for evidence promotion — a single-publisher
        og:image is too noisy to treat as evidentiary on its own.
        """
        out: set[str] = set()
        for c in cluster:
            if not c.source_article_url:
                continue
            try:
                host = urlparse(c.source_article_url).netloc.lower()
            except Exception:
                continue
            if host.startswith("www."):
                host = host[4:]
            if host:
                out.add(host)
        return out

    def _build_canonical(
        self, rep: MediaCandidate, cluster: list[MediaCandidate]
    ) -> CanonicalMedia:
        """Fold a dedup cluster into one persisted CanonicalMedia record."""
        rep_url = rep.final_url or rep.source_url
        # Mirror URLs = every distinct image URL in the cluster except the rep.
        mirror_urls = sorted({
            (c.final_url or c.source_url)
            for c in cluster
            if (c.final_url or c.source_url) and (c.final_url or c.source_url) != rep_url
        })
        # Source article URLs = every distinct host article that carried this image.
        source_article_urls = sorted({
            c.source_article_url for c in cluster if c.source_article_url
        })
        # appearance_count = distinct ARTICLES that carried this image (NOT
        # cluster size, which double-counts responsive variants emitted by the
        # same article). Falls back to cluster size only when no article URLs
        # were captured at all.
        appearance_count = len(source_article_urls) or len(cluster)
        return CanonicalMedia(
            media_id=media_id_for(cluster),
            type=rep.classification or "other",  # type: ignore[arg-type]
            status="available" if rep.download_status == "ok" else "unavailable",
            primary_url=rep_url,
            mirror_urls=mirror_urls,
            source_article_urls=source_article_urls,
            caption=rep.figcaption or rep.caption,
            alt_text=rep.alt_text,
            width=rep.width,
            height=rep.height,
            mime_type=rep.mime_type,
            sha256=rep.sha256,
            phash=rep.phash,
            classifier_tier=rep.classifier_tier or "keyword",
            confidence=rep.classification_confidence,
            classification_evidence=list(rep.classification_evidence),
            is_stock_photo=rep.is_stock_photo,
            is_evidence=bool(rep.is_evidence),
            evidence_reason=rep.evidence_reason,
            appearance_count=appearance_count,
        )
