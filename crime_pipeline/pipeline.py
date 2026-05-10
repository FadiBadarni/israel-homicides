"""
Nine-stage homicide news scraping pipeline orchestrator.

Stages: Discover -> Fetch -> Extract -> Dedup -> Merge -> Sanity -> Quality
        -> Reconcile -> Export

The last three (Sanity, Quality, Reconcile) are deterministic, zero-API-cost
cleanup passes that previously only ran inside ``--enrich-case`` mode. They
were silently skipped on default pipeline runs, leaving exports without
script-purity correction, three-axis legal-status splitting, date repair,
or per-category confidence calibration. They now run inline by default.

Each stage checkpoints to SQLite. The pipeline is resumable via ``--stage``
flags from the CLI: any stage that is skipped will pull its inputs from
previously persisted database rows (or, for the new cleanup stages, simply
no-op).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from crime_pipeline.config import Settings
from crime_pipeline.dedup.deduplicator import Deduplicator
from crime_pipeline.export.json_exporter import JSONExporter
from crime_pipeline.extraction.extractor import ArticleExtractor
from crime_pipeline.media import ArticleContext, MediaPipeline, MediaSettings
from crime_pipeline.merging.merger import CaseMerger
from crime_pipeline.models import ExtractedArticleData
from crime_pipeline.scrapers import get_scraper
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.storage.repository import (
    get_all_extractions,
    get_articles_by_status,
    save_article,
    save_canonical_case,
    save_extraction,
)

log = structlog.get_logger()


class Pipeline:
    """Top-level orchestrator wiring the six pipeline stages together."""

    def __init__(self, settings: Settings, run_id: str | None = None) -> None:
        self.settings = settings
        self.run_id = run_id or str(uuid.uuid4())[:12]
        # Make sure the parent directory for the SQLite file exists.
        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = init_db(str(settings.db_path))
        self.stats: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "discovered": 0,
            "fetched": 0,
            "fetch_failed": 0,
            "extracted": 0,
            "extraction_failed": 0,
            "clusters": 0,
            "singletons": 0,
            "review_pairs": 0,
            "extraction_drop_in_merge": 0,
            "sanity_applied": 0,
            "quality_applied": 0,
            "reconcile_merged": 0,
            "reconcile_audit_path": None,
            "cases_exported": 0,
            "non_fatal_excluded": 0,
            "media_canonical": 0,
            "media_evidence_canonical": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self._media_settings = MediaSettings()
        # Single MediaPipeline (and MediaClassifier) for the whole run so the
        # CLIP model (~600 MB) is loaded once, not once per merged case.
        self._media_pipeline = MediaPipeline(self._media_settings)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        query: str,
        sources: list[str],
        date_from: str,
        date_to: str,
        max_per_source: int = 50,
        stages: set[str] | None = None,
    ) -> dict[str, Any]:
        """Execute the full pipeline (or a subset of stages) and return stats."""
        stages = stages or {
            "discover", "fetch", "extract", "dedup", "merge",
            "sanity", "quality", "reconcile", "export",
        }
        log.info(
            "pipeline_start",
            run_id=self.run_id,
            query=query,
            sources=sources,
            stages=sorted(stages),
            date_from=date_from,
            date_to=date_to,
        )

        discovered: list = []
        articles: list = []
        extractions: list = []
        cases: list = []

        # ── Stage 1: Discover ─────────────────────────────────────────
        if "discover" in stages:
            discovered = await self._discover(
                query, sources, date_from, date_to, max_per_source
            )

        # ── Stage 2: Fetch ────────────────────────────────────────────
        if "fetch" in stages:
            articles = await self._fetch(discovered, sources)
        else:
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                articles = list(get_articles_by_status(session, "success"))

        # ── Stage 3: Extract ──────────────────────────────────────────
        if "extract" in stages:
            extractions = await self._extract(articles)
        else:
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                extractions = list(get_all_extractions(session))

        # ── Stages 4 + 5: Dedup + Merge ───────────────────────────────
        if "dedup" in stages or "merge" in stages:
            cases = await self._dedup_and_merge(extractions, articles)

        # ── Stages 6–8: Sanity → Quality → Reconcile ─────────────────
        # All three are deterministic, zero-API-cost transforms over the
        # in-memory case list. They were silently skipped before this work;
        # any one can still be opted out of via --stage exclusion.
        if any(s in stages for s in ("sanity", "quality", "reconcile")):
            cases = await asyncio.to_thread(self._run_cleanup, cases, stages)

        # Stamp finished_at BEFORE export so the consolidated JSON's run
        # block carries an accurate end timestamp + duration_seconds.
        self.stats["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.stats["stages_executed"] = sorted(stages)

        # ── Stage 9: Export ───────────────────────────────────────────
        if "export" in stages:
            await self._export(cases)

        log.info("pipeline_complete", **self.stats)
        return self.stats

    # ------------------------------------------------------------------
    # Stage 1: Discover
    # ------------------------------------------------------------------

    async def _discover(
        self,
        query: str,
        sources: list[str],
        date_from: str,
        date_to: str,
        max_per_source: int,
    ) -> list:
        """Find candidate URLs across each source and dedupe."""
        all_urls: list = []
        for source in sources:
            try:
                scraper = get_scraper(
                    source,
                    request_delay=self.settings.request_delay_seconds,
                    respect_robots=self.settings.robots_txt_respect,
                )
                urls = await scraper.discover(
                    query, date_from, date_to, max_results=max_per_source
                )
                log.info("discovered", source=source, count=len(urls))
                all_urls.extend(urls)
            except Exception as e:  # pragma: no cover - defensive
                log.error("discover_error", source=source, error=str(e))

        # Dedup URLs across sources (first occurrence wins).
        seen: set[str] = set()
        unique = []
        for u in all_urls:
            if u.url not in seen:
                seen.add(u.url)
                unique.append(u)

        self.stats["discovered"] = len(unique)
        log.info("discover_complete", unique_urls=len(unique), total_raw=len(all_urls))
        return unique

    # ------------------------------------------------------------------
    # Stage 2: Fetch
    # ------------------------------------------------------------------

    async def _fetch(self, discovered_urls: Iterable, sources: list[str]) -> list:
        """Fetch each candidate URL, persist to ``raw_articles`` and return successes."""
        articles: list = []
        scrapers: dict = {}
        for source in sources:
            try:
                scrapers[source] = get_scraper(
                    source,
                    request_delay=self.settings.request_delay_seconds,
                    respect_robots=self.settings.robots_txt_respect,
                )
            except Exception as e:  # pragma: no cover - defensive
                log.error("scraper_init_error", source=source, error=str(e))

        for du in discovered_urls:
            scraper = scrapers.get(du.source)
            if scraper is None:
                log.warning("no_scraper_for_source", source=du.source, url=du.url)
                continue
            try:
                result = await scraper.fetch(du.url)
                with db_module.SessionLocal() as session:  # type: ignore[misc]
                    article_dict = {
                        "source": result.source,
                        "url": result.url,
                        "final_url": result.final_url,
                        "language": result.language,
                        "title": result.title,
                        "published_at": result.published_at or du.published_at,
                        "raw_html": result.raw_html,
                        "article_text": result.article_text,
                        "content_type": result.content_type,
                        "fetch_status": result.fetch_status,
                        "error_message": result.error_message,
                    }
                    saved = save_article(session, article_dict)
                    session.commit()
                    if result.fetch_status == "success":
                        articles.append(saved)
                        self.stats["fetched"] += 1
                    else:
                        self.stats["fetch_failed"] += 1
                log.info(
                    "fetched",
                    url=du.url,
                    status=result.fetch_status,
                    content_type=result.content_type,
                )
            except Exception as e:
                log.error("fetch_error", url=du.url, error=str(e))
                self.stats["fetch_failed"] += 1

        log.info(
            "fetch_complete",
            fetched=self.stats["fetched"],
            failed=self.stats["fetch_failed"],
        )
        return articles

    # ------------------------------------------------------------------
    # Stage 3: Extract
    # ------------------------------------------------------------------

    async def _extract(self, articles: list) -> list:
        """Run LLM extraction on each successful article and persist results."""
        if not articles:
            log.warning("no_articles_to_extract")
            return []

        extractor = ArticleExtractor(
            api_key=self.settings.gemini_api_key,
            model=self.settings.llm_model,
            max_tokens=self.settings.llm_max_tokens,
            concurrency=self.settings.llm_concurrency,
        )

        article_inputs = [
            {
                "article_id": a.id,
                "article_text": a.article_text,
                "language": a.language,
                "source": a.source,
                "published_at": (
                    a.published_at.isoformat() if a.published_at else None
                ),
            }
            for a in articles
            if a.fetch_status == "success" and a.article_text
        ]

        if not article_inputs:
            log.warning("no_extractable_articles")
            return []

        results = await extractor.extract_batch(article_inputs)

        extractions: list = []
        with db_module.SessionLocal() as session:  # type: ignore[misc]
            for inp, res in zip(article_inputs, results):
                self.stats["total_input_tokens"] += res.get("input_tokens", 0) or 0
                self.stats["total_output_tokens"] += res.get("output_tokens", 0) or 0
                if res.get("status") == "success" and res.get("extracted_data"):
                    extraction_data = {
                        "article_id": inp["article_id"],
                        "extracted_json": res["extracted_data"],
                        "validation_status": "valid",
                        "llm_model": self.settings.llm_model,
                        "input_tokens": res.get("input_tokens", 0),
                        "output_tokens": res.get("output_tokens", 0),
                        "cache_hit": res.get("cache_hit", False),
                        "latency_ms": res.get("latency_ms", 0),
                        "extraction_status": "success",
                    }
                    saved = save_extraction(
                        session, inp["article_id"], extraction_data
                    )
                    extractions.append(saved)
                    self.stats["extracted"] += 1
                else:
                    self.stats["extraction_failed"] += 1
                    log.warning(
                        "extraction_failed",
                        article_id=inp["article_id"][:8],
                        error=res.get("error"),
                        status=res.get("status"),
                    )
            session.commit()

        log.info(
            "extract_complete",
            extracted=self.stats["extracted"],
            failed=self.stats["extraction_failed"],
            input_tokens=self.stats["total_input_tokens"],
            output_tokens=self.stats["total_output_tokens"],
        )
        return extractions

    # ------------------------------------------------------------------
    # Stages 4 + 5: Dedup + Merge
    # ------------------------------------------------------------------

    async def _dedup_and_merge(
        self, extractions: list, articles: list
    ) -> list:
        """Cluster extractions then merge each cluster (and singleton) into a case."""
        if not extractions:
            log.warning("no_extractions_to_dedup")
            return []

        # Build an ID->article lookup; if the upstream stage was skipped, refetch.
        article_lookup: dict[str, Any] = {a.id: a for a in articles} if articles else {}
        if not article_lookup:
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                article_lookup = {
                    a.id: a for a in get_articles_by_status(session, "success")
                }

        # Construct dedup-input records.
        dedup_records: list[dict[str, Any]] = []
        for ext in extractions:
            article = article_lookup.get(ext.article_id)
            if article is None:
                continue
            data = ext.extracted_json or {}
            dedup_records.append(
                {
                    "id": ext.id,
                    "article_id": ext.article_id,
                    "victim_name": data.get("victim_name"),
                    "incident_date": data.get("incident_date"),
                    "city": data.get("city"),
                    # Truncate to keep embedding latency bounded; multilingual
                    # encoders typically use the first 512 tokens anyway.
                    "article_text": (article.article_text or "")[:2000],
                    "source": article.source,
                    "url": article.url,
                    "confidence_score": data.get("confidence_score", 0.5),
                }
            )

        if not dedup_records:
            log.warning("no_dedup_records_built")
            return []

        # Run dedup. The dedup.run() call is sync and CPU-bound (embeddings +
        # all-pairs comparison) — wrap in asyncio.to_thread so we don't block
        # the event loop on large batches.
        deduplicator = Deduplicator(
            jaro_threshold=self.settings.jaro_threshold,
            cosine_threshold=self.settings.cosine_threshold,
        )
        try:
            result = await asyncio.to_thread(deduplicator.run, dedup_records)
        finally:
            try:
                deduplicator.close()
            except Exception:  # pragma: no cover - cleanup best-effort
                pass

        rec_lookup = {r["id"]: r for r in dedup_records}
        ext_lookup = {e.id: e for e in extractions}

        self.stats["clusters"] = len(result["clusters"])
        self.stats["singletons"] = len(result["singletons"])
        self.stats["review_pairs"] = len(result["review_pairs"])
        self.stats["review_pair_details"] = [
            {
                "a": {
                    "victim_name": rec_lookup.get(a_id, {}).get("victim_name"),
                    "city": rec_lookup.get(a_id, {}).get("city"),
                    "url": rec_lookup.get(a_id, {}).get("url"),
                },
                "b": {
                    "victim_name": rec_lookup.get(b_id, {}).get("victim_name"),
                    "city": rec_lookup.get(b_id, {}).get("city"),
                    "url": rec_lookup.get(b_id, {}).get("url"),
                },
                "jaro_score": round(jaro, 3),
                "cosine_score": round(cosine, 3),
            }
            for a_id, b_id, jaro, cosine in result["review_pairs"]
        ]

        # Merge clusters and singletons.
        merger = CaseMerger()
        cases: list = []

        all_groups: list[list[str]] = list(result["clusters"]) + [
            [s] for s in result["singletons"]
        ]

        for group in all_groups:
            # For multi-record clusters, identify the canonical record (highest
            # source-priority + confidence). Used downstream as a tiebreak hint
            # and for logging clarity. Singletons trivially canonical.
            canonical_id: str | None = None
            if len(group) > 1:
                canonical_id = deduplicator.select_canonical(
                    group,
                    [
                        {"id": r, "source": rec_lookup[r]["source"],
                         "confidence_score": rec_lookup[r]["confidence_score"]}
                        for r in group if r in rec_lookup
                    ],
                )
                log.debug("canonical_selected", cluster_size=len(group),
                          canonical_id=canonical_id[:8] if canonical_id else None)
            cluster_input: list[dict[str, Any]] = []
            for rec_id in group:
                ext = ext_lookup.get(rec_id)
                rec = rec_lookup.get(rec_id)
                if not ext or not rec:
                    continue
                article = article_lookup.get(ext.article_id)
                if not article:
                    continue
                try:
                    extraction_obj = ExtractedArticleData(**ext.extracted_json)
                except Exception as e:
                    log.warning(
                        "invalid_extraction_in_merge",
                        record_id=rec_id[:8],
                        error=str(e),
                    )
                    self.stats["extraction_drop_in_merge"] += 1
                    continue
                cluster_input.append(
                    {
                        "extraction": extraction_obj,
                        "url": article.url,
                        "source": article.source,
                        "language": article.language,
                        "published_at": article.published_at,
                        "raw_html": article.raw_html,
                    }
                )
            if not cluster_input:
                continue
            try:
                case = merger.merge_cluster(cluster_input, pipeline_run_id=self.run_id)
                # Media pass — populate case.media + case.media_evidence from
                # each article's raw_html. Failures here must not block the
                # case from being persisted.
                try:
                    await self._attach_media(case, cluster_input)
                except Exception as e:
                    log.warning("media_pipeline_error", error=str(e))
                cases.append(case)
                # Persist canonical case row.
                with db_module.SessionLocal() as session:  # type: ignore[misc]
                    save_canonical_case(
                        session,
                        {
                            "case_json": case.model_dump(mode="json"),
                            "sources_merged": [s.url for s in case.sources],
                            "confidence_score": case.confidence_score,
                            "flags": case.flags,
                            "review_status": case.review_status,
                            "pipeline_run_id": self.run_id,
                        },
                    )
                    session.commit()
            except Exception as e:
                log.error("merge_error", error=str(e))

        self.stats["cases_exported"] = len(cases)
        log.info(
            "merge_complete",
            cases=len(cases),
            clusters=self.stats["clusters"],
            singletons=self.stats["singletons"],
            review_pairs=self.stats["review_pairs"],
        )
        return cases

    # ------------------------------------------------------------------
    # Media pass (per merged case)
    # ------------------------------------------------------------------

    async def _attach_media(self, case: Any, cluster_input: list[dict[str, Any]]) -> None:
        """Run MediaPipeline on a merged case and assign media + media_evidence.

        Mutates ``case`` in place. Skips cleanly when MediaSettings.enabled is
        False or no article has raw_html.
        """
        if not self._media_settings.enabled:
            return
        articles_for_media = [
            {
                "raw_html": ci.get("raw_html") or "",
                "url": ci.get("url") or "",
                "article_text": None,
            }
            for ci in cluster_input
            if ci.get("raw_html")
        ]
        if not articles_for_media:
            return

        # Build case-level classifier context from the merged case fields.
        victim_names = [
            n for n in (
                case.victim_name, case.victim_name_ar,
                case.victim_name_he, case.victim_name_en,
            ) if n
        ]
        for alias in case.aliases or []:
            if alias and alias not in victim_names:
                victim_names.append(alias)
        suspect_names = [case.suspect_name] if case.suspect_name else []
        city_names: list[str] = []
        if case.city:
            city_names.append(case.city)
        for v in (case.city_normalized or {}).values():
            if v and v not in city_names:
                city_names.append(v)
        if case.neighborhood and case.neighborhood not in city_names:
            city_names.append(case.neighborhood)

        ctx = ArticleContext(
            article_url=articles_for_media[0]["url"],
            victim_names=victim_names,
            suspect_names=suspect_names,
            city_names=city_names,
        )

        media_canon, evidence_canon = await self._media_pipeline.run_for_case(
            articles_for_media, ctx
        )
        case.media = [cm.model_dump(mode="json") for cm in media_canon]
        case.media_evidence = [cm.model_dump(mode="json") for cm in evidence_canon]
        self.stats["media_canonical"] += len(media_canon)
        self.stats["media_evidence_canonical"] += len(evidence_canon)

    # ------------------------------------------------------------------
    # Stages 6-8: Sanity → Quality → Reconcile (deterministic cleanup)
    # ------------------------------------------------------------------

    def _run_cleanup(self, cases: list, stages: set[str]) -> list:
        """Run sanity → quality → reconcile against the in-memory case list.

        Cases enter as ``CanonicalCaseSchema`` instances. The three cleanup
        functions all operate on plain dicts, so we round-trip through
        ``model_dump()`` and re-validate before returning. The schema was
        widened in this refactor to cover ``tier_coverage`` / ``timeline`` /
        ``motive_translations`` / ``reconciliation_provenance`` so the
        round-trip no longer silently drops fields.

        Order matters: sanity must precede quality because
        ``quality_pass.drop_invalid_sources`` reads ``timeline`` (written by
        sanity) and would erase entries tied to demoted sources.
        """
        from crime_pipeline.enrichment.quality_pass import run_quality_pass
        from crime_pipeline.enrichment.reconciler import reconcile_cases
        from crime_pipeline.enrichment.sanity_pass import run_sanity_pass
        from crime_pipeline.models import CanonicalCaseSchema

        case_dicts: list[dict[str, Any]] = [
            c.model_dump(mode="json") for c in cases
        ]

        if "sanity" in stages:
            case_dicts = [run_sanity_pass(d) for d in case_dicts]
            self.stats["sanity_applied"] = len(case_dicts)
            log.info("sanity_complete", cases=len(case_dicts))

        if "quality" in stages:
            case_dicts = [run_quality_pass(d) for d in case_dicts]
            self.stats["quality_applied"] = len(case_dicts)
            log.info("quality_complete", cases=len(case_dicts))

        if "reconcile" in stages:
            result = reconcile_cases(case_dicts)
            case_dicts = result.cases
            self.stats["reconcile_merged"] = len(result.merged_pairs)
            if result.merged_pairs:
                audit_path = (
                    self.settings.output_dir
                    / f"{self.run_id}_reconcile_audit.jsonl"
                )
                audit_path.parent.mkdir(parents=True, exist_ok=True)
                with audit_path.open("w", encoding="utf-8") as f:
                    for pair in result.merged_pairs:
                        f.write(
                            json.dumps(pair, ensure_ascii=False, default=str) + "\n"
                        )
                self.stats["reconcile_audit_path"] = str(audit_path)
                log.info(
                    "reconcile_complete",
                    merged=len(result.merged_pairs),
                    cases_before=result.cases_before,
                    cases_after=result.cases_after,
                    audit_path=str(audit_path),
                )
            else:
                log.info("reconcile_nothing_to_merge")

        # Re-validate as models so the export stage's attribute access works.
        return [CanonicalCaseSchema(**d) for d in case_dicts]

    # ------------------------------------------------------------------
    # Stage 9: Export
    # ------------------------------------------------------------------

    async def _export(self, cases: list) -> None:
        """Write a single rich JSON containing run metadata + stats + cases.

        Schema 2.0 — one self-describing file per run at
        ``{output_dir}/{run_id}.json``. Each case in the file already carries
        its own ``media`` / ``media_evidence`` / ``sources`` / ``conflicts``
        / per-category ``confidence``, so this single file is the complete
        ground-truth artifact for the run.
        """
        # Filter non-fatal incidents (attempted killings where the victim survived).
        # These are persisted to the DB with a "non_fatal" flag for audit but must
        # not appear in a homicides dataset.
        fatal_cases = []
        non_fatal_cases = []
        for case in cases:
            if case.victim_outcome == "survived":
                non_fatal_cases.append(case)
                log.info(
                    "non_fatal_excluded",
                    victim_name=case.victim_name,
                    city=case.city,
                    sources=len(case.sources),
                )
            else:
                fatal_cases.append(case)
        self.stats["non_fatal_excluded"] = len(non_fatal_cases)
        cases = fatal_cases
        self.stats["cases_exported"] = len(cases)
        self.stats["media_canonical"] = sum(len(case.media or []) for case in cases)
        self.stats["media_evidence_canonical"] = sum(
            len(case.media_evidence or []) for case in cases
        )

        exporter = JSONExporter(self.settings.output_dir)

        # Build a simple human-readable summary embedded in the JSON.
        summary_lines: list[str] = [
            f"Pipeline Run: {self.run_id}",
            "=" * 60,
            "",
            f"Started:  {self.stats.get('started_at', '')}",
            f"Finished: {self.stats.get('finished_at', '')}",
            "",
            f"Discovered:        {self.stats.get('discovered', 0)}",
            f"Fetched:           {self.stats.get('fetched', 0)}"
            f" (failed: {self.stats.get('fetch_failed', 0)})",
            f"Extracted:         {self.stats.get('extracted', 0)}"
            f" (failed: {self.stats.get('extraction_failed', 0)})",
            f"Clusters:          {self.stats.get('clusters', 0)}",
            f"Singletons:        {self.stats.get('singletons', 0)}",
            f"Review pairs:      {self.stats.get('review_pairs', 0)}",
            f"Cases exported:    {self.stats.get('cases_exported', 0)}",
            f"Non-fatal excl.:   {self.stats.get('non_fatal_excluded', 0)}",
            f"Media (decorative):{self.stats.get('media_canonical', 0)}",
            f"Media (evidence):  {self.stats.get('media_evidence_canonical', 0)}",
            f"Input tokens:      {self.stats.get('total_input_tokens', 0)}",
            f"Output tokens:     {self.stats.get('total_output_tokens', 0)}",
            "",
        ]
        for i, case in enumerate(cases, 1):
            summary_lines.append(f"Case #{i}")
            summary_lines.append(f"  Victim:          {case.victim_name or 'Unknown'}")
            summary_lines.append(f"  Age:             {case.victim_age or 'Unknown'}")
            summary_lines.append(f"  Date:            {case.incident_date or 'Unknown'}")
            summary_lines.append(f"  City:            {case.city or 'Unknown'}")
            summary_lines.append(f"  Weapon:          {case.weapon_type or 'Unknown'}")
            summary_lines.append(
                f"  Suspect status:  {case.suspect_status or 'Unknown'}"
            )
            summary_lines.append(f"  Sources:         {len(case.sources)}")
            summary_lines.append(f"  Confidence:      {case.confidence_score}")
            summary_lines.append(
                f"  Flags:           {', '.join(case.flags) if case.flags else 'none'}"
            )
            summary_lines.append("")

        exporter.export_run(
            run_id=self.run_id,
            cases=cases,
            stats=self.stats,
            human_summary="\n".join(summary_lines),
        )
        log.info(
            "export_complete",
            cases=len(cases),
            output_dir=str(self.settings.output_dir),
        )
