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
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from crime_pipeline.config import Settings
from crime_pipeline.dedup.deduplicator import Deduplicator
from crime_pipeline.export.json_exporter import JSONExporter
from crime_pipeline.extraction.extractor import ArticleExtractor
from crime_pipeline.extraction.relevance import is_homicide_extraction
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

# ---------------------------------------------------------------------------
# Discovery query expansion (E+D strategy)
# ---------------------------------------------------------------------------

_DISCOVER_SECOND_PASS_THRESHOLD = 3

_HE_DESCRIPTOR_TERMS = ["רצח", "ירי"]   # murder, shooting
_AR_DESCRIPTOR_TERMS = ["مقتل", "قتل"]  # killed, killing

_ALL_CRIME_TERMS = frozenset(
    _HE_DESCRIPTOR_TERMS + _AR_DESCRIPTOR_TERMS
    + ["דקירה", "נרצח", "הרג", "إطلاق", "جريمة", "طعن"]
)


def _generate_descriptor_variants(query: str, date_from: str) -> list[str]:
    """Return incident-descriptor query variants to run when first-pass is sparse.

    Appending a crime term to a city name catches articles whose titles say
    "doctor kills his brother in Arraba" when the original query was the city
    name or a victim name that never appears in Hebrew news headlines.

    Returns [] when the query already contains crime vocabulary.
    """
    if any(t in query for t in _ALL_CRIME_TERMS):
        return []

    year = date_from[:4] if date_from and len(date_from) >= 4 else ""
    if year and year in query:
        year = ""  # don't duplicate year already in query string

    is_arabic = bool(re.search(r"[؀-ۿ]", query))
    terms = _AR_DESCRIPTOR_TERMS if is_arabic else _HE_DESCRIPTOR_TERMS

    return [
        f"{query} {term}" + (f" {year}" if year else "")
        for term in terms
    ]


class Pipeline:
    """Top-level orchestrator wiring the six pipeline stages together."""

    def __init__(
        self,
        settings: Settings,
        run_id: str | None = None,
        strict_city: bool = False,
        strict_date: bool = False,
    ) -> None:
        self.settings = settings
        self.run_id = run_id or str(uuid.uuid4())[:12]
        # When True, post-merge cases whose normalized city doesn't match
        # the queried city are dropped (or kept with a `city_filter_unverified`
        # flag if the gazetteer can't validate). Only meaningful in city-mode
        # runs (--cities); the CLI guards against using it with a freeform
        # --query string.
        self.strict_city = strict_city
        self._strict_city_target: dict | None = None
        # When True, post-merge cases whose extracted incident_date falls
        # outside the queried [date_from, date_to] window are dropped. The
        # 2020-killed-Wafa-Abahara-in-a-2026-sentencing-article case is the
        # canonical reason this filter exists. Without it, sentencing /
        # retrospective articles about old murders contaminate year-by-year
        # backfills with cases from the wrong year.
        self.strict_date = strict_date
        self._strict_date_window: tuple[Any, Any] | None = None
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
            "triage_kept": 0,
            "triage_dropped": 0,
            "triage_reasons": {},
            "relevance_kept": 0,
            "relevance_dropped": 0,
            "relevance_drop_reasons": {},
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
        max_pages: int = 5,
        stages: set[str] | None = None,
    ) -> dict[str, Any]:
        """Execute the full pipeline (or a subset of stages) and return stats."""
        stages = stages or {
            "discover", "fetch", "triage", "extract", "dedup", "merge",
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

        # Resolve the strict-city target via the gazetteer if the operator
        # enabled --strict-city. We accept the queried city in any script —
        # the gazetteer normalizes Hebrew / Arabic / English to the same
        # CityRecord. Resolution failure is logged but not fatal: the post-
        # merge filter falls back to flagging instead of dropping.
        if self.strict_city:
            from crime_pipeline.utils.gazetteer import normalize_city
            self._strict_city_target = normalize_city(query)
            if self._strict_city_target is None:
                log.warning(
                    "strict_city_target_unresolved",
                    query=query,
                    note="cases will be flagged 'city_filter_unverified', not dropped",
                )
            else:
                log.info(
                    "strict_city_target",
                    name_en=self._strict_city_target.get("name_en"),
                )

        # Resolve the strict-date window (canonicalised once per run).
        if self.strict_date:
            from datetime import date as _date
            try:
                self._strict_date_window = (
                    _date.fromisoformat(date_from),
                    _date.fromisoformat(date_to),
                )
                log.info(
                    "strict_date_window",
                    date_from=date_from, date_to=date_to,
                )
            except ValueError as exc:
                log.warning(
                    "strict_date_window_unresolved",
                    date_from=date_from, date_to=date_to, error=str(exc),
                )
                self._strict_date_window = None

        discovered: list = []
        articles: list = []
        extractions: list = []
        cases: list = []

        # ── Stage 1: Discover ─────────────────────────────────────────
        if "discover" in stages:
            discovered = await self._discover(
                query, sources, date_from, date_to, max_per_source, max_pages
            )

        # ── Stage 2: Fetch ────────────────────────────────────────────
        if "fetch" in stages:
            articles = await self._fetch(discovered, sources)
        else:
            # Resume mode: scope to this run only — without the run_id filter
            # a multi-city/multi-keyword backfill cross-contaminates dedup.
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                articles = list(
                    get_articles_by_status(session, "success", pipeline_run_id=self.run_id)
                )

        # ── Stage 2.5: Triage (cheap classifier, drops most articles) ──
        # Sends title + first 600 chars to Gemini-flash with thinking off.
        # Cuts ~80% of full-extraction tokens by dropping non-homicide
        # articles before the expensive extract stage. Persists every
        # decision to raw_articles for audit + replay.
        if articles and "triage" in stages:
            articles = await self._triage(articles)

        # ── Stage 3: Extract ──────────────────────────────────────────
        if "extract" in stages:
            extractions = await self._extract(articles)
        else:
            # Resume mode: scope to this run's extractions only.
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                extractions = list(
                    get_all_extractions(session, pipeline_run_id=self.run_id)
                )

        # ── Relevance gate (between extract and dedup) ───────────────
        # Drops mostly-null extractions that broad search queries produce
        # when the search engine returns tangentially-related articles.
        # Conservative: keeps anything with any victim / city / date /
        # death-marker signal. Skipped only if both extract and dedup are
        # excluded (nothing to filter anyway).
        if extractions and ("extract" in stages or "dedup" in stages):
            extractions = self._filter_relevance(extractions)

        # ── Stages 4 + 5: Dedup + Merge ───────────────────────────────
        if "dedup" in stages or "merge" in stages:
            cases = await self._dedup_and_merge(extractions, articles)

        # ── Stages 6–8: Sanity → Quality → Reconcile ─────────────────
        # All three are deterministic, zero-API-cost transforms over the
        # in-memory case list. They were silently skipped before this work;
        # any one can still be opted out of via --stage exclusion.
        if any(s in stages for s in ("sanity", "quality", "reconcile")):
            cases = await asyncio.to_thread(self._run_cleanup, cases, stages)

        # Persist the final canonical representation after deterministic cleanup
        # so SQLite and exported JSON describe the same cases.
        if cases and any(s in stages for s in ("dedup", "merge", "sanity", "quality", "reconcile")):
            await asyncio.to_thread(self._persist_canonical_cases, cases)

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
        max_pages: int = 5,
    ) -> list:
        """Find candidate URLs across each source, with E+D query expansion.

        First pass uses the caller-supplied query. If it returns fewer than
        _DISCOVER_SECOND_PASS_THRESHOLD unique URLs, a second pass runs
        crime-type descriptor variants (e.g. "{city} רצח {year}") to catch
        articles whose titles use incident descriptors rather than victim names.
        """
        # Build scraper map once — avoid recreating per query in second pass.
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

        seen: set[str] = set()
        unique: list = []
        total_raw = 0

        async def _run_query(q: str) -> None:
            nonlocal total_raw
            for source, scraper in scrapers.items():
                try:
                    try:
                        urls = await scraper.discover(
                            q, date_from, date_to,
                            max_results=max_per_source, max_pages=max_pages,
                        )
                    except TypeError:
                        # Scraper hasn't adopted max_pages yet — call without it.
                        urls = await scraper.discover(
                            q, date_from, date_to, max_results=max_per_source,
                        )
                    total_raw += len(urls)
                    log.info("discovered", source=source, query=q, count=len(urls))
                    for u in urls:
                        if u.url not in seen:
                            seen.add(u.url)
                            unique.append(u)
                except Exception as e:  # pragma: no cover - defensive
                    log.error("discover_error", source=source, query=q, error=str(e))

        # First pass: original query.
        await _run_query(query)

        # Second pass (E+D): when the first pass is sparse, run crime-type
        # descriptor variants that catch headlines like "doctor kills his
        # brother in Arraba" when the original query was a victim name or a
        # bare city name that doesn't appear in titles.
        if len(unique) < _DISCOVER_SECOND_PASS_THRESHOLD:
            for alt_query in _generate_descriptor_variants(query, date_from):
                log.info(
                    "discover_second_pass",
                    alt_query=alt_query,
                    first_pass_count=len(unique),
                )
                await _run_query(alt_query)

        self.stats["discovered"] = len(unique)
        log.info("discover_complete", unique_urls=len(unique), total_raw=total_raw)
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
                        "pipeline_run_id": self.run_id,
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
    # Stage 2.5: Triage (cheap classifier between fetch and extract)
    # ------------------------------------------------------------------

    async def _triage(self, articles: list) -> list:
        """Run the C-stage triage classifier; persist verdicts; return only
        articles that should proceed to full extraction.

        Decision distribution is recorded in ``self.stats``:
          triage_kept       — articles that proceed to extract
          triage_dropped    — articles dropped pre-extract
          triage_reasons    — count of each incident_type drop reason
        """
        from crime_pipeline.extraction.triage import Triager

        triager = Triager(
            api_key=self.settings.gemini_api_key,
            model=self.settings.llm_model,
            concurrency=self.settings.llm_concurrency,
        )

        triage_inputs = [
            {
                "article_id": a.id,
                "title": a.title,
                "lede": (a.article_text or "")[:600],
            }
            for a in articles
            if a.fetch_status == "success" and a.article_text
        ]
        if not triage_inputs:
            log.warning("no_articles_to_triage")
            return []

        results = await triager.triage_batch(triage_inputs)
        result_by_id = {r.article_id: r for r in results}

        # Persist every triage decision to raw_articles for audit + replay.
        with db_module.SessionLocal() as session:  # type: ignore[misc]
            from crime_pipeline.models import RawArticle
            for r in results:
                row = session.get(RawArticle, r.article_id)
                if row is None:
                    continue
                row.triage_status = r.status
                row.triage_incident_type = r.incident_type
                row.triage_reason = r.reason
                row.triage_model_version = r.model_version
                row.triage_input_tokens = r.input_tokens
                row.triage_output_tokens = r.output_tokens
            session.commit()

        # Stats + filtering
        kept = []
        reasons: dict[str, int] = {}
        in_tok = 0
        out_tok = 0
        for a in articles:
            r = result_by_id.get(a.id)
            if r is None:
                # No triage decision (e.g. article had no text) — pass through
                kept.append(a)
                continue
            in_tok += r.input_tokens
            out_tok += r.output_tokens
            if r.status in ("yes", "maybe"):
                kept.append(a)
            else:
                reasons[r.reason] = reasons.get(r.reason, 0) + 1

        self.stats["triage_kept"] = len(kept)
        self.stats["triage_dropped"] = len(articles) - len(kept)
        self.stats["triage_reasons"] = reasons
        self.stats["total_input_tokens"] += in_tok
        self.stats["total_output_tokens"] += out_tok
        log.info(
            "triage_complete",
            kept=len(kept),
            dropped=len(articles) - len(kept),
            reasons=reasons,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
        return kept

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
    # Relevance gate (between extract and dedup)
    # ------------------------------------------------------------------

    def _filter_relevance(self, extractions: list) -> list:
        """Drop extractions that show no signal of being a real homicide.

        Inputs are ``ExtractedRecord`` rows whose ``.extracted_json`` holds the
        validated dict from the LLM. The gate is conservative: anything with
        any victim / city / date / death-marker signal is kept. Only obvious
        zero-signal extractions and explicit ``victim_outcome="survived"``
        cases are dropped.

        Side effects: updates ``self.stats`` with kept/dropped counts and a
        ``relevance_drop_reasons`` reason→count map.
        """
        kept: list = []
        drop_reasons: dict[str, int] = {}
        for ext in extractions:
            data = ext.extracted_json or {}
            keep, reason = is_homicide_extraction(data)
            if keep:
                kept.append(ext)
            else:
                drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
                log.info(
                    "relevance_dropped",
                    article_id=ext.article_id[:8],
                    reason=reason,
                )
        self.stats["relevance_kept"] = len(kept)
        self.stats["relevance_dropped"] = len(extractions) - len(kept)
        self.stats["relevance_drop_reasons"] = drop_reasons
        log.info(
            "relevance_filter_complete",
            kept=len(kept),
            dropped=len(extractions) - len(kept),
            reasons=drop_reasons,
        )
        return kept

    # ------------------------------------------------------------------
    # Strict city filter helper (S5)
    # ------------------------------------------------------------------

    def _matches_strict_city(self, case) -> tuple[bool, str]:
        """Decide whether a merged case matches ``self._strict_city_target``.

        Returns ``(keep, reason)``. When the gazetteer can't validate the
        case's extracted city, we KEEP and tag with a flag rather than
        silently drop — false-negatives (lost real homicide) are worse
        than false-positives (one extra row to triage).
        """
        target = self._strict_city_target or {}
        target_en = (target.get("name_en") or "").strip().lower()

        from crime_pipeline.utils.gazetteer import normalize_city

        case_city = getattr(case, "city", None)
        if not case_city:
            # No city was extracted — can't validate. Keep + flag.
            flags = list(getattr(case, "flags", None) or [])
            if "city_filter_unverified" not in flags:
                flags.append("city_filter_unverified")
            try:
                case.flags = flags
            except Exception:  # pragma: no cover — schema mutation guard
                pass
            return True, "no_city_extracted"

        case_record = normalize_city(case_city)
        if case_record is None:
            # Gazetteer doesn't know this city. Sonnet's rule: keep + flag.
            flags = list(getattr(case, "flags", None) or [])
            if "city_filter_unverified" not in flags:
                flags.append("city_filter_unverified")
            try:
                case.flags = flags
            except Exception:  # pragma: no cover
                pass
            return True, "gazetteer_miss"

        case_en = (case_record.get("name_en") or "").strip().lower()
        if case_en and target_en and case_en == target_en:
            return True, "city_match"
        return False, f"city_mismatch:{case_en or case_city}"

    def _matches_strict_date(self, case) -> tuple[bool, str]:
        """Decide whether a merged case's incident_date is in window.

        Returns ``(keep, reason)``. When the case has no extracted
        incident_date, we KEEP and flag with `date_filter_unverified`
        — same logic as the city filter: don't silently drop on missing
        data, surface it for operator review.
        """
        window = self._strict_date_window
        if window is None:
            return True, "no_window"
        date_from, date_to = window

        # Try incident_date first, fall back to death_date (a 2020-killed
        # victim with no incident_date but death_date=2020 still gets dropped).
        from datetime import date as _date
        candidate = None
        for field in ("incident_date", "death_date"):
            val = getattr(case, field, None)
            if val is None:
                continue
            if isinstance(val, _date):
                candidate = val
                break
            try:
                candidate = _date.fromisoformat(str(val))
                break
            except (ValueError, TypeError):
                continue

        if candidate is None:
            flags = list(getattr(case, "flags", None) or [])
            if "date_filter_unverified" not in flags:
                flags.append("date_filter_unverified")
            try:
                case.flags = flags
            except Exception:  # pragma: no cover
                pass
            return True, "no_date_extracted"

        if date_from <= candidate <= date_to:
            return True, "date_in_window"
        return False, f"date_out_of_window:{candidate.isoformat()}"

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
        # Scope to this run only — multi-city/keyword backfills share the DB.
        article_lookup: dict[str, Any] = {a.id: a for a in articles} if articles else {}
        if not article_lookup:
            with db_module.SessionLocal() as session:  # type: ignore[misc]
                article_lookup = {
                    a.id: a
                    for a in get_articles_by_status(
                        session, "success", pipeline_run_id=self.run_id
                    )
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

                # Strict-city filter (S5): in --cities mode the operator wants
                # cases whose normalized city matches the queried city only.
                # On gazetteer match-failure, FLAG don't drop (Sonnet's rule).
                if self.strict_city and self._strict_city_target is not None:
                    keep, drop_reason = self._matches_strict_city(case)
                    if not keep:
                        log.info(
                            "strict_city_filter_drop",
                            case_id=getattr(case, "canonical_case_id", None),
                            extracted_city=getattr(case, "city", None),
                            target=self._strict_city_target.get("name_en"),
                            reason=drop_reason,
                        )
                        self.stats["strict_city_dropped"] = (
                            self.stats.get("strict_city_dropped", 0) + 1
                        )
                        continue

                # Strict-date filter: drop cases whose incident_date is outside
                # the queried window. Catches the Wafa-Abahara 2020 sentencing
                # scenario where a 2026 article correctly extracts the 2020
                # incident date but the case shouldn't ship in a 2026 dataset.
                if self.strict_date and self._strict_date_window is not None:
                    keep, drop_reason = self._matches_strict_date(case)
                    if not keep:
                        log.info(
                            "strict_date_filter_drop",
                            case_id=getattr(case, "canonical_case_id", None),
                            extracted_date=getattr(case, "incident_date", None),
                            window=self._strict_date_window,
                            reason=drop_reason,
                        )
                        self.stats["strict_date_dropped"] = (
                            self.stats.get("strict_date_dropped", 0) + 1
                        )
                        continue

                # Media pass — populate case.media + case.media_evidence from
                # each article's raw_html. Failures here must not block the
                # case from being persisted.
                try:
                    await self._attach_media(case, cluster_input)
                except Exception as e:
                    log.warning("media_pipeline_error", error=str(e))
                cases.append(case)
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

    def _persist_canonical_cases(self, cases: list) -> None:
        """Persist final canonical cases after merge/cleanup transforms."""
        if not cases:
            return
        with db_module.SessionLocal() as session:  # type: ignore[misc]
            for case in cases:
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
        log.info("canonical_cases_persisted", cases=len(cases))

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
