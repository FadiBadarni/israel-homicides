"""CLI entry point for the homicide-news AI pipeline.

Usage:
    python -m crime_pipeline --query "Arraba 2026" --sources ynet,arab48 \\
        --date-from 2026-01-01 --date-to 2026-12-31

Run only specific stages (resume after a partial run):
    python -m crime_pipeline --query "Arraba 2026" --stage extract --stage dedup \\
        --stage merge --stage export
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import click
import structlog

from crime_pipeline.config import Settings
from crime_pipeline.pipeline import Pipeline


def configure_logging(level: str) -> None:
    """Configure structlog + stdlib logging for the CLI."""
    # Force UTF-8 on Windows before structlog/colorama attach to stdout,
    # so Hebrew/Arabic names in log fields don't crash with cp1252 errors.
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--query",
    required=False,
    default=None,
    help="Search query (e.g. 'Arraba 2026' or a victim name). Required unless --enrich-case is used.",
)
@click.option(
    "--enrich-case",
    "enrich_case",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to an existing canonical case JSON. Runs the second-pass enricher on it instead of a normal pipeline run.",
)
@click.option(
    "--enrich-weak",
    "enrich_weak",
    is_flag=True,
    default=False,
    help="With --enrich-case: only enrich cases that are missing victim_name or victim_outcome.",
)
@click.option(
    "--reconcile",
    "reconcile",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to an output JSON. Merges fragmented clusters with similar names and no city/date conflict. No API key required.",
)
@click.option(
    "--enrichment-queries",
    default=6, type=int, show_default=True,
    help="Maximum enrichment queries to generate per case.",
)
@click.option(
    "--enrichment-articles-per-query",
    default=4, type=int, show_default=True,
    help="Maximum articles to fetch per enrichment query.",
)
@click.option(
    "--arabic-only",
    "arabic_only",
    is_flag=True,
    default=False,
    help="Run enrichment with Arabic-only queries and Arabic Google News locale.",
)
@click.option(
    "--tier",
    "tier",
    type=click.Choice(["1", "2", "3"]),
    default=None,
    help="Target a specific source tier for enrichment. "
         "1=mainstream Israeli news (Ynet, Mako, Haaretz, ...). "
         "2=Arabic/local press (Arab48, Kul al-Arab). "
         "3=official sources (police.gov.il, courts).",
)
@click.option(
    "--sources",
    default="ynet,arab48",
    show_default=True,
    help="Comma-separated source names. Available: ynet, arab48.",
)
@click.option(
    "--date-from",
    default=None,
    help="Start date YYYY-MM-DD. Overrides --date-window-days when set.",
)
@click.option(
    "--date-to",
    default=None,
    help="End date YYYY-MM-DD. Defaults to today.",
)
@click.option(
    "--date-window-days",
    default=30,
    show_default=True,
    type=int,
    help=(
        "Look back N days from --date-to (default: today). Default 30 "
        "balances news rhythm vs historical-retrospective pollution. "
        "Ignored when --date-from is set."
    ),
)
@click.option(
    "--max-per-source",
    default=50,
    show_default=True,
    type=int,
    help="Max articles to discover per source.",
)
@click.option(
    "--max-pages",
    default=5,
    show_default=True,
    type=int,
    help=(
        "Max search-result pages to crawl per source per query. Arab48 "
        "returns 20 articles per page; --max-pages=5 caps at ~100 articles. "
        "The scraper stops early if a page adds zero new URLs."
    ),
)
@click.option(
    "--strict-city",
    is_flag=True,
    default=False,
    help=(
        "Drop merged cases whose extracted city doesn't normalize to the "
        "queried city via the gazetteer. Designed for --cities mode; the "
        "freeform --query string is treated as a city name when this flag "
        "is set. Cases the gazetteer can't validate are KEPT and tagged "
        "with a 'city_filter_unverified' flag (never silently dropped)."
    ),
)
@click.option(
    "--strict-date",
    is_flag=True,
    default=False,
    help=(
        "Drop merged cases whose extracted incident_date falls outside the "
        "queried [--date-from, --date-to] window. Catches sentencing / "
        "retrospective articles where a 2026 article correctly extracts a "
        "2020 incident date but the case doesn't belong in a 2026 dataset. "
        "Cases without any extracted date are KEPT and tagged "
        "'date_filter_unverified'."
    ),
)
@click.option(
    "--cities",
    "cities",
    default=None,
    help=(
        "Comma-separated list of city names (English transliteration; "
        "gazetteer maps to native scripts per source). When set, the "
        "pipeline runs once per (city, source) with run_id "
        "<city>_<year>_<source>. Mutually exclusive with --query. "
        "Example: --cities arraba,sakhnin,umm-al-fahm"
    ),
)
@click.option(
    "--cities-year",
    default=None,
    type=int,
    help=(
        "Year tag for --cities run_ids. Defaults to current year. "
        "Used purely for run_id naming; the actual date range is "
        "controlled by --date-from/--date-to/--date-window-days."
    ),
)
@click.option(
    "--keyword-mode",
    type=click.Choice(["hebrew", "arabic", "both"], case_sensitive=False),
    default=None,
    help=(
        "Crime-keyword sweep mode (Strategy C stage 2). Loops the "
        "pipeline once per (keyword, compatible source) using a curated "
        "Hebrew/Arabic homicide-keyword union. Hebrew kw → Ynet only; "
        "Arabic kw → Arab48 only. Each (keyword, source) run gets a "
        "separate run_id. Mutually exclusive with --query / --cities."
    ),
)
@click.option(
    "--verify-truth",
    "verify_truth",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "Path to a JSONL file with ground-truth homicide records "
        "({city, victim_name_he, victim_name_ar, incident_date}). "
        "When combined with --verify-run, the pipeline computes "
        "precision/recall/F1 against the truth set and exits."
    ),
)
@click.option(
    "--verify-run",
    "verify_run",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "Path to a pipeline output JSON to validate against --verify-truth."
    ),
)
@click.option(
    "--show-pipeline-funnel",
    "show_pipeline_funnel",
    default=None,
    help=(
        "Print stage-by-stage drop-off counts for a given pipeline_run_id and "
        "exit. Accepts a run_id substring (matches all runs starting with it) "
        "or 'all' for every run in the DB. Use --funnel-format=jsonl for "
        "machine-readable output."
    ),
)
@click.option(
    "--funnel-format",
    "funnel_format",
    default="table",
    show_default=True,
    type=click.Choice(["table", "jsonl"], case_sensitive=False),
    help="Output format for --show-pipeline-funnel.",
)
@click.option(
    "--stage",
    "stages",
    multiple=True,
    type=click.Choice(
        [
            "discover", "fetch", "triage", "extract", "dedup", "merge",
            "sanity", "quality", "reconcile", "export",
        ],
        case_sensitive=False,
    ),
    help=(
        "Run only specific stages (repeatable). Default: all ten stages "
        "(discover, fetch, triage, extract, dedup, merge, sanity, quality, "
        "reconcile, export)."
    ),
)
@click.option(
    "--jaro-threshold",
    default=None,
    type=float,
    help="Override the Jaro-Winkler name-similarity threshold.",
)
@click.option(
    "--cosine-threshold",
    default=None,
    type=float,
    help="Override the cosine embedding-similarity threshold.",
)
@click.option(
    "--log-level",
    default="INFO",
    show_default=True,
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False
    ),
    help="Logging level.",
)
@click.option(
    "--run-id",
    default=None,
    help="Custom run ID; auto-generated when omitted.",
)
@click.option(
    "--build-canonical",
    "build_canonical_mode",
    is_flag=True,
    default=False,
    help=(
        "Production mode: build THE canonical homicide dataset over "
        "every fetched article in the DB, using the latest extraction "
        "per article. Outputs one Schema-2.0 envelope at "
        "output/canonical_<date_from>_<date_to>.json. Requires "
        "--date-from and --date-to. No discover/fetch — operates on "
        "existing raw_articles."
    ),
)
@click.option(
    "--reextract-all",
    "reextract_all",
    is_flag=True,
    default=False,
    help=(
        "Re-run extraction on every triage-passed article in the DB "
        "with the current prompt. Use after prompt changes to refresh "
        "extracted_records before --build-canonical."
    ),
)
def cli(
    query: str | None,
    enrich_case: str | None,
    enrich_weak: bool,
    reconcile: str | None,
    enrichment_queries: int,
    enrichment_articles_per_query: int,
    arabic_only: bool,
    tier: str | None,
    sources: str,
    date_from: str | None,
    date_to: str | None,
    date_window_days: int,
    max_per_source: int,
    max_pages: int,
    strict_city: bool,
    strict_date: bool,
    cities: str | None,
    cities_year: int | None,
    keyword_mode: str | None,
    verify_truth: str | None,
    verify_run: str | None,
    show_pipeline_funnel: str | None,
    funnel_format: str,
    stages: tuple[str, ...],
    jaro_threshold: float | None,
    cosine_threshold: float | None,
    log_level: str,
    run_id: str | None,
    build_canonical_mode: bool,
    reextract_all: bool,
) -> None:
    """Run the homicide-news scraping/AI pipeline end-to-end."""
    configure_logging(log_level)

    # ── Pipeline funnel diagnostic (no API key needed) ───────────────────
    # Reads counts straight from the SQLite checkpoints. Useful for
    # spotting where articles drop in a sweep without re-running anything.
    if show_pipeline_funnel:
        from crime_pipeline.diagnostics import (
            format_funnel_as_jsonl, format_funnel_as_table, gather_funnel,
        )
        rows = gather_funnel(show_pipeline_funnel)
        if not rows:
            click.echo(
                f"No pipeline_run_id matched {show_pipeline_funnel!r}.",
                err=True,
            )
            sys.exit(1)
        if funnel_format.lower() == "jsonl":
            click.echo(format_funnel_as_jsonl(rows))
        else:
            click.echo(format_funnel_as_table(rows))
        sys.exit(0)

    # ── Re-extract all triage-passed articles (refresh DB) ───────────────
    # Production prep: run after a prompt change to repopulate
    # extracted_records with current-prompt outputs. No discover/fetch —
    # operates on raw_articles already in the DB.
    if reextract_all:
        from dotenv import load_dotenv
        load_dotenv()
        if not os.environ.get("GEMINI_API_KEY"):
            click.echo("ERROR: GEMINI_API_KEY required for --reextract-all.", err=True)
            sys.exit(2)
        try:
            settings = Settings()  # type: ignore[call-arg]
        except Exception as e:
            click.echo(f"ERROR: Settings load failed: {e}", err=True)
            sys.exit(2)

        import sqlite3
        from crime_pipeline.extraction.extractor import ArticleExtractor
        import crime_pipeline.storage.db as db_mod
        from crime_pipeline.storage.db import init_db
        from crime_pipeline.storage.repository import save_extraction

        init_db(str(settings.db_path))
        conn = sqlite3.connect(str(settings.db_path))
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, article_text, language, source, published_at
            FROM raw_articles
            WHERE fetch_status = 'success'
              AND triage_status IN ('yes', 'maybe')
              AND article_text IS NOT NULL
              AND length(article_text) > 200
            ORDER BY fetched_at DESC
            """
        )
        rows = cur.fetchall()
        conn.close()

        article_inputs = [
            {
                "article_id": r[0], "article_text": r[1],
                "language": r[2], "source": r[3], "published_at": r[4],
            }
            for r in rows
        ]
        click.echo(f"--reextract-all: {len(article_inputs)} triage-passed articles")

        extractor = ArticleExtractor(
            api_key=settings.gemini_api_key,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            concurrency=settings.llm_concurrency,
        )
        results = asyncio.run(extractor.extract_batch(article_inputs))

        from collections import Counter
        geo_counts: Counter = Counter()
        success = failed = 0
        with db_mod.SessionLocal() as session:  # type: ignore[misc]
            for inp, res in zip(article_inputs, results):
                if res.get("status") == "success" and res.get("extracted_data"):
                    data = res["extracted_data"]
                    geo_counts[data.get("incident_geography")] += 1
                    save_extraction(
                        session, inp["article_id"],
                        {
                            "article_id": inp["article_id"],
                            "extracted_json": data,
                            "validation_status": "valid",
                            "llm_model": settings.llm_model,
                            "input_tokens": res.get("input_tokens", 0),
                            "output_tokens": res.get("output_tokens", 0),
                            "cache_hit": res.get("cache_hit", False),
                            "latency_ms": res.get("latency_ms", 0),
                            "extraction_status": "success",
                        },
                    )
                    success += 1
                else:
                    failed += 1
            session.commit()

        click.echo(f"Re-extracted: success={success} failed={failed}")
        click.echo("Geography breakdown:")
        for geo, n in geo_counts.most_common():
            click.echo(f"  {geo!r}: {n}")
        sys.exit(0)

    # ── Canonical build mode ─────────────────────────────────────────────
    # Production operating mode: build THE canonical dataset for a date
    # window from the DB's current state. No discover/fetch/extract —
    # uses whatever's in extracted_records (run --reextract-all first
    # if you changed the prompt).
    if build_canonical_mode:
        if not date_from or not date_to:
            click.echo(
                "ERROR: --build-canonical requires --date-from and --date-to.",
                err=True,
            )
            sys.exit(2)
        try:
            settings = Settings()  # type: ignore[call-arg]
        except Exception as e:
            click.echo(f"ERROR: Settings load failed: {e}", err=True)
            sys.exit(2)

        canonical_run_id = run_id or f"canonical_{date_from}_{date_to}"
        pipeline = Pipeline(settings, run_id=canonical_run_id, strict_date=False)
        click.echo(f"--build-canonical: window {date_from} to {date_to}")
        try:
            stats = asyncio.run(pipeline.build_canonical(date_from, date_to))
            click.echo()
            click.echo("Canonical build complete:")
            for k, v in stats.items():
                click.echo(f"  {k}: {v}")
            sys.exit(0 if stats.get("canonical_cases", 0) > 0 else 2)
        except Exception as e:
            click.echo(f"Canonical build error: {e}", err=True)
            raise

    # ── Reconcile mode (no API key needed) ───────────────────────────────
    if reconcile:
        from crime_pipeline.enrichment.reconciler import reconcile_file
        click.echo(f"Reconciling: {reconcile}")
        summary = reconcile_file(reconcile)
        click.echo(f"  Cases before : {summary['cases_before']}")
        click.echo(f"  Cases after  : {summary['cases_after']}")
        click.echo(f"  Merges made  : {len(summary['merged_pairs'])}")
        for p in summary["merged_pairs"]:
            click.echo(f"    {p['name_a']}  +  {p['name_b']}  (jaro={p['jaro']})")
        sys.exit(0)

    # ── Enrichment-only mode ─────────────────────────────────────────────
    if enrich_case:
        from dotenv import load_dotenv
        load_dotenv()
        if not os.environ.get("GEMINI_API_KEY"):
            click.echo("ERROR: GEMINI_API_KEY required for enrichment.", err=True)
            sys.exit(2)
        try:
            settings = Settings()  # type: ignore[call-arg]
        except Exception as e:
            click.echo(f"ERROR: Settings load failed: {e}", err=True)
            sys.exit(2)

        from crime_pipeline.enrichment.enricher import CaseEnricher

        click.echo(f"Enrichment pass on: {enrich_case}")
        click.echo(f"  Max queries:           {enrichment_queries}")
        click.echo(f"  Articles per query:    {enrichment_articles_per_query}")
        target_tier_int: int | None = int(tier) if tier else None
        if target_tier_int:
            mode = f"tier_{target_tier_int}"
            # Tier 2 → Arabic locale; otherwise Hebrew/English locale.
            locale_choice = "ar" if target_tier_int == 2 else "he"
            strategy_choice = f"tier{target_tier_int}"
        elif arabic_only:
            mode = "arabic_only"
            locale_choice = "ar"
            strategy_choice = "arabic_only"
        else:
            mode = "default"
            locale_choice = "he"
            strategy_choice = "default"
        click.echo(f"  Mode:                  {mode}")
        enricher = CaseEnricher(
            gemini_api_key=settings.gemini_api_key,
            llm_model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            concurrency=2,  # cap for free-tier RPM
            request_delay=settings.request_delay_seconds,
            respect_robots=settings.robots_txt_respect,
            max_queries=enrichment_queries,
            max_articles_per_query=enrichment_articles_per_query,
            locale=locale_choice,
            query_strategy=strategy_choice,
            target_tier=target_tier_int,
        )
        try:
            envelope = asyncio.run(
                enricher.enrich(Path(enrich_case), weak_only=enrich_weak)
            )
            cases = envelope.get("cases") or []
            if cases:
                c = cases[0]
                click.echo("\n" + "=" * 60)
                click.echo("Enrichment complete:")
                click.echo(f"  canonical_case_id : {c.get('canonical_case_id')}")
                click.echo(f"  victim_name       : {c.get('victim_name')}")
                click.echo(f"  victim_name_ar    : {c.get('victim_name_ar')}")
                click.echo(f"  victim_name_he    : {c.get('victim_name_he')}")
                click.echo(f"  aliases           : {c.get('aliases')}")
                click.echo(f"  neighborhood      : {c.get('neighborhood')}")
                click.echo(f"  hospital          : {c.get('hospital')}")
                click.echo(f"  suspect_relation  : {c.get('suspect_relation')}")
                click.echo(f"  suspect_profession: {c.get('suspect_profession')}")
                click.echo(f"  evidence count    : {len(c.get('evidence') or [])}")
                click.echo(f"  media count       : {len(c.get('media') or [])}")
                click.echo(f"  sources           : {len(c.get('sources') or [])}")
                click.echo(f"  confidence        : {c.get('confidence_score')}")
                click.echo(f"  flags             : {c.get('flags')}")
                click.echo(f"  enrichment_passes : {c.get('enrichment_passes')}")
                click.echo(f"\nWrote: {enrich_case}")
            sys.exit(0)
        except Exception as e:
            click.echo(f"Enrichment error: {e}", err=True)
            raise

    # ── --verify mode ──────────────────────────────────────────────────
    # Strategy C stage 3. Truth-vs-pipeline comparison; computes
    # precision/recall/F1 and exits. Doesn't need GEMINI_API_KEY or any
    # scraper config — it's a pure file-vs-file evaluation.
    if verify_truth and verify_run:
        from crime_pipeline.verification import (
            load_pipeline_cases,
            load_truth_jsonl,
            verify_run_against_truth,
        )
        try:
            truth = load_truth_jsonl(verify_truth)
            cases = load_pipeline_cases(verify_run)
        except (ValueError, OSError) as e:
            click.echo(f"ERROR loading verify inputs: {e}", err=True)
            sys.exit(2)

        result = verify_run_against_truth(truth, cases)
        click.echo("\n" + "=" * 60)
        click.echo("Verification result:")
        click.echo(f"  Truth records:    {result.truth_count}")
        click.echo(f"  Pipeline cases:   {result.pipeline_count}")
        click.echo(f"  True positives:   {result.true_positive}")
        click.echo(f"  False negatives:  {result.false_negative}  ← missed real cases")
        click.echo(f"  False positives:  {result.false_positive}  ← extra/junk")
        click.echo(f"  Precision:        {result.precision:.1%}")
        click.echo(f"  Recall:           {result.recall:.1%}")
        click.echo(f"  F1:               {result.f1:.3f}")

        if result.missing_truth:
            click.echo("\nMissed truth records (false negatives):")
            for t in result.missing_truth:
                victim = (
                    t.get("victim_name") or t.get("victim_name_he")
                    or t.get("victim_name_ar") or "(unnamed)"
                )
                city = t.get("city") or "?"
                date_ = t.get("incident_date") or "?"
                click.echo(f"  - {victim} | {city} | {date_}")
        if result.extra_pipeline:
            click.echo("\nExtra pipeline cases (false positives):")
            for c in result.extra_pipeline:
                victim = c.get("victim_name") or c.get("victim_name_ar") or "(unnamed)"
                city = c.get("city") or "?"
                date_ = c.get("incident_date") or "?"
                conf = c.get("confidence_score") or 0
                click.echo(f"  - {victim} | {city} | {date_} | conf={conf}")

        # Persist machine-readable summary alongside the run JSON
        out_path = Path(verify_run).with_suffix(".verify.json")
        out_path.write_text(
            json.dumps(result.summary_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(f"\nSummary written to: {out_path}")
        sys.exit(0)

    # Four-way input check: --enrich-case (handled above), --query, --cities,
    # --keyword-mode. The three operator-driven modes are mutually exclusive.
    operator_modes = sum(bool(x) for x in (query, cities, keyword_mode))
    if operator_modes > 1:
        click.echo(
            "ERROR: --query / --cities / --keyword-mode are mutually exclusive. Pick one.",
            err=True,
        )
        sys.exit(2)
    if operator_modes == 0:
        click.echo(
            "ERROR: one of --query / --cities / --keyword-mode / --enrich-case is required.",
            err=True,
        )
        sys.exit(2)

    # Resolve date window. --date-from explicit always wins; otherwise look back
    # --date-window-days from --date-to (default: today). Default 30-day window
    # balances news rhythm against historical-retrospective pollution
    # (see search-noise-strategy debate synthesis).
    from datetime import date, timedelta
    resolved_to = date_to or date.today().isoformat()
    if date_from is None:
        try:
            anchor = date.fromisoformat(resolved_to)
        except ValueError:
            click.echo(f"ERROR: invalid --date-to: {resolved_to!r}", err=True)
            sys.exit(2)
        resolved_from = (anchor - timedelta(days=date_window_days)).isoformat()
    else:
        resolved_from = date_from

    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    stage_set: set[str] = (
        {s.lower() for s in stages}
        if stages
        else {
            "discover", "fetch", "triage", "extract", "dedup", "merge",
            "sanity", "quality", "reconcile", "export",
        }
    )

    # ── --cities backfill mode ──────────────────────────────────────────
    # Loops the pipeline once per (city, source). Each call gets a fresh
    # Pipeline (its stats and run_id are set in __init__), shared SQLite
    # DB, and a per-pair run_id so resume + cross-run-scoping works.
    if cities:
        if "extract" in stage_set and not os.environ.get("GEMINI_API_KEY"):
            from dotenv import load_dotenv
            load_dotenv()
            if not os.environ.get("GEMINI_API_KEY"):
                click.echo(
                    "ERROR: GEMINI_API_KEY env var is required for --cities mode.",
                    err=True,
                )
                sys.exit(2)
        try:
            settings = Settings()  # type: ignore[call-arg]
        except Exception as e:
            click.echo(f"ERROR: Settings load failed: {e}", err=True)
            sys.exit(2)

        from crime_pipeline.utils.gazetteer import normalize_city

        from datetime import date as _date
        year = cities_year or _date.today().year
        city_inputs = [c.strip() for c in cities.split(",") if c.strip()]
        click.echo(f"--cities mode: {len(city_inputs)} cities × {len(source_list)} sources")
        click.echo(f"  Year tag:    {year}")
        click.echo(f"  Date range:  {resolved_from} to {resolved_to}")
        click.echo(f"  Strict city: {strict_city}")
        click.echo("")

        summary_rows: list[dict] = []
        for city_in in city_inputs:
            record = normalize_city(city_in)
            if record is None:
                click.echo(f"  ⚠ Unknown city in gazetteer: {city_in!r} — skipping")
                continue

            # Native-script query per source. Arab48 = Arabic, Ynet = Hebrew.
            for source in source_list:
                if source == "arab48":
                    native_query = record.get("name_ar") or record.get("name_en") or city_in
                elif source == "ynet":
                    native_query = record.get("name_he") or record.get("name_en") or city_in
                else:
                    native_query = record.get("name_en") or city_in

                slug = (record.get("name_en") or city_in).lower().replace(" ", "_")
                pair_run_id = f"{slug}_{year}_{source}"
                click.echo(
                    f"▶ {city_in} → {source} (query={native_query!r}, run_id={pair_run_id})"
                )
                pipeline = Pipeline(
                    settings, run_id=pair_run_id,
                    strict_city=strict_city, strict_date=strict_date,
                )
                try:
                    pair_stats = asyncio.run(
                        pipeline.run(
                            query=native_query,
                            sources=[source],
                            date_from=resolved_from,
                            date_to=resolved_to,
                            max_per_source=max_per_source,
                            max_pages=max_pages,
                            stages=stage_set,
                        )
                    )
                    summary_rows.append({
                        "city": city_in,
                        "source": source,
                        "run_id": pair_run_id,
                        "discovered": pair_stats.get("discovered", 0),
                        "extracted": pair_stats.get("extracted", 0),
                        "cases_exported": pair_stats.get("cases_exported", 0),
                        "strict_city_dropped": pair_stats.get("strict_city_dropped", 0),
                    })
                except Exception as e:  # pragma: no cover — defensive
                    click.echo(f"  ✗ {pair_run_id} failed: {e}", err=True)
                    summary_rows.append({
                        "city": city_in, "source": source, "run_id": pair_run_id,
                        "discovered": 0, "extracted": 0, "cases_exported": 0,
                        "strict_city_dropped": 0, "error": str(e)[:80],
                    })

        click.echo("\n" + "=" * 72)
        click.echo("--cities backfill complete:")
        click.echo(
            f"  {'CITY':<20} {'SOURCE':<10} {'DISC':>6} {'EXT':>6} {'CASES':>6} {'CITY_DROP':>10}"
        )
        for r in summary_rows:
            click.echo(
                f"  {r['city']:<20} {r['source']:<10} "
                f"{r['discovered']:>6} {r['extracted']:>6} "
                f"{r['cases_exported']:>6} {r['strict_city_dropped']:>10}"
            )
        total_cases = sum(r["cases_exported"] for r in summary_rows)
        click.echo(f"\nTotal cases across all (city, source) pairs: {total_cases}")
        sys.exit(0)

    # ── --keyword-mode crime sweep ─────────────────────────────────────
    # Strategy C stage 2. Loops once per (keyword, compatible source).
    # Hebrew keywords go to Ynet only; Arabic to Arab48 only. Each
    # (keyword, source) pair gets its own run_id so results are traceable.
    if keyword_mode:
        if "extract" in stage_set and not os.environ.get("GEMINI_API_KEY"):
            from dotenv import load_dotenv
            load_dotenv()
            if not os.environ.get("GEMINI_API_KEY"):
                click.echo(
                    "ERROR: GEMINI_API_KEY env var is required for --keyword-mode.",
                    err=True,
                )
                sys.exit(2)
        try:
            settings = Settings()  # type: ignore[call-arg]
        except Exception as e:
            click.echo(f"ERROR: Settings load failed: {e}", err=True)
            sys.exit(2)

        # Curated keyword presets (Gemini's discover-phase recommendations).
        # Kept tight: 2 strong keywords per language. Adding more multiplies
        # API cost without much marginal recall — triage already filters noise.
        # Curated keyword presets (Gemini's discover-phase + live-recall
        # data). Each list is a union — every keyword runs as its own
        # pipeline call and results are de-duped at verify time.
        # Initial 2 keywords per language gave 69% recall on the Jan 2026
        # truth. Adding the *weapon-action* keywords (ירי / דקירה /
        # إطلاق نار / طعن) catches articles whose titles describe the
        # method rather than naming the crime — pushes recall higher
        # at the cost of more noise the triage filter then drops.
        # Expanded after the Jan 2026 truth investigation showed that
        # several murder cases were covered on Arab48 under verbs we
        # weren't searching for: قتل (bare killing verb), تصفية
        # ("liquidation" — gangland framing), أردى ("shot dead"), and
        # جثة ("body" — used in body-found articles). The triage filter
        # rejects non-homicide noise these broader terms surface, so
        # adding them lifts recall without polluting the dataset.
        _HE_KEYWORDS = ["רצח", "נרצח", "ירי", "דקירה"]
        _AR_KEYWORDS = [
            "جريمة قتل", "مقتل", "إطلاق نار", "طعن",
            "قتل", "تصفية", "أردى", "جثة",
        ]
        # Source compatibility — Hebrew kw on Hebrew sites, Arabic on
        # Arabic-language sites. Makan added 2026-05 after the Jan 2026
        # truth investigation showed several victims (تيمور عطالله) had
        # Makan-only coverage. Walla added shortly after to close the
        # Bedouin/Negev femicide gap (بسمة أبو فريحة — covered by Walla
        # by name, by Ynet/Arab48/Makan not at all).
        _SOURCES_FOR_LANG = {
            "he": ["ynet", "walla"],
            "ar": ["arab48", "makan"],
        }

        from datetime import date as _date
        year = cities_year or _date.today().year
        mode_lower = keyword_mode.lower()
        plan: list[tuple[str, str, str]] = []  # (keyword, source, lang)
        if mode_lower in ("hebrew", "both"):
            for kw in _HE_KEYWORDS:
                for src in _SOURCES_FOR_LANG["he"]:
                    plan.append((kw, src, "he"))
        if mode_lower in ("arabic", "both"):
            for kw in _AR_KEYWORDS:
                for src in _SOURCES_FOR_LANG["ar"]:
                    plan.append((kw, src, "ar"))

        click.echo(f"--keyword-mode={mode_lower}: {len(plan)} (keyword, source) pairs")
        click.echo(f"  Year tag:    {year}")
        click.echo(f"  Date range:  {resolved_from} to {resolved_to}")
        click.echo("")

        summary_rows: list[dict] = []
        for kw, source, lang in plan:
            # Slug the keyword for run_id (transliterate non-ASCII to short hash).
            # Include the source in the run_id so makan and arab48 sweeps
            # of the same keyword don't collide on a shared run_id (which
            # would mix their articles in the SQLite checkpoints).
            import hashlib
            slug = hashlib.md5(kw.encode()).hexdigest()[:8]
            pair_run_id = f"kw_{lang}_{source}_{slug}_{year}"
            click.echo(f"▶ keyword={kw!r} → {source} (run_id={pair_run_id})")

            pipeline = Pipeline(
                settings, run_id=pair_run_id, strict_date=strict_date,
            )
            try:
                pair_stats = asyncio.run(
                    pipeline.run(
                        query=kw,
                        sources=[source],
                        date_from=resolved_from,
                        date_to=resolved_to,
                        max_per_source=max_per_source,
                        max_pages=max_pages,
                        stages=stage_set,
                    )
                )
                summary_rows.append({
                    "keyword": kw, "source": source, "run_id": pair_run_id,
                    "discovered": pair_stats.get("discovered", 0),
                    "extracted": pair_stats.get("extracted", 0),
                    "cases_exported": pair_stats.get("cases_exported", 0),
                })
            except Exception as e:  # pragma: no cover — defensive
                click.echo(f"  ✗ {pair_run_id} failed: {e}", err=True)
                summary_rows.append({
                    "keyword": kw, "source": source, "run_id": pair_run_id,
                    "discovered": 0, "extracted": 0, "cases_exported": 0,
                    "error": str(e)[:80],
                })

        click.echo("\n" + "=" * 72)
        click.echo("--keyword-mode sweep complete:")
        click.echo(
            f"  {'KEYWORD':<20} {'SOURCE':<10} {'DISC':>6} {'EXT':>6} {'CASES':>6}"
        )
        for r in summary_rows:
            click.echo(
                f"  {r['keyword']:<20} {r['source']:<10} "
                f"{r['discovered']:>6} {r['extracted']:>6} "
                f"{r['cases_exported']:>6}"
            )
        total_cases = sum(r["cases_exported"] for r in summary_rows)
        click.echo(f"\nTotal cases across all (keyword, source) pairs: {total_cases}")
        click.echo("Note: cases may overlap across keywords. Run --reconcile or "
                   "verify CLI to dedup across runs.")
        sys.exit(0)

    # Fail fast: extract stage needs GEMINI_API_KEY before any work begins,
    # not after scraping completes. Check env (and the .env file Settings
    # would load) before instantiating Settings, since Settings requires it.
    if "extract" in stage_set and not os.environ.get("GEMINI_API_KEY"):
        # Try loading .env in case key lives there.
        from dotenv import load_dotenv
        load_dotenv()
        if not os.environ.get("GEMINI_API_KEY"):
            click.echo(
                "ERROR: GEMINI_API_KEY env var is required for the extract stage.\n"
                "       Set it in .env or your shell, or skip extract via --stage flags.",
                err=True,
            )
            sys.exit(2)

    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        click.echo(f"ERROR: Settings load failed: {e}", err=True)
        sys.exit(2)
    if jaro_threshold is not None:
        settings.jaro_threshold = jaro_threshold
    if cosine_threshold is not None:
        settings.cosine_threshold = cosine_threshold

    pipeline = Pipeline(
        settings, run_id=run_id,
        strict_city=strict_city, strict_date=strict_date,
    )

    click.echo(f"Pipeline starting | run_id={pipeline.run_id}")
    click.echo(f"  Query:       {query}")
    click.echo(f"  Sources:     {source_list}")
    click.echo(f"  Date range:  {resolved_from} to {resolved_to}")
    click.echo(f"  Stages:      {sorted(stage_set)}")
    click.echo(f"  DB path:     {settings.db_path}")
    click.echo(f"  Output dir:  {settings.output_dir}")
    click.echo("")

    try:
        stats = asyncio.run(
            pipeline.run(
                query=query,
                sources=source_list,
                date_from=resolved_from,
                date_to=resolved_to,
                max_per_source=max_per_source,
                max_pages=max_pages,
                stages=stage_set,
            )
        )
        click.echo("\n" + "=" * 60)
        click.echo("Pipeline complete:")
        for k, v in stats.items():
            click.echo(f"  {k}: {v}")
        click.echo(f"\nOutput written to: {settings.output_dir}")

        # Exit codes:
        #   0 = produced cases or extracted at least one article
        #   2 = ran without producing any usable output
        produced_output = (
            stats.get("cases_exported", 0) > 0
            or stats.get("extracted", 0) > 0
        )
        sys.exit(0 if produced_output else 2)
    except KeyboardInterrupt:
        click.echo("\nInterrupted by user", err=True)
        sys.exit(130)
    except Exception as e:
        click.echo(f"Pipeline error: {e}", err=True)
        raise


if __name__ == "__main__":
    cli()
