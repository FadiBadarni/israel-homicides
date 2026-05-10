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

from typing import Optional
from urllib.parse import urlparse

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
                cands = self.harvester.harvest(html, base_url)
            except Exception as e:
                log.warning("media_harvest_error", url=base_url, error=str(e))
                continue
            all_cands.extend(cands)

        if not all_cands:
            return [], []

        # ── 2. Cap per case ───────────────────────────────────────────
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
