"""CLI entry point for the homicide-news AI pipeline.

Usage:
    python -m crime_pipeline --query "Arraba 2026" --sources ynet,panet \\
        --date-from 2026-01-01 --date-to 2026-12-31

Run only specific stages (resume after a partial run):
    python -m crime_pipeline --query "Arraba 2026" --stage extract --stage dedup \\
        --stage merge --stage export
"""
from __future__ import annotations

import asyncio
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
         "2=Arabic/local press (Arab48, Panet, Kul al-Arab). "
         "3=official sources (police.gov.il, courts).",
)
@click.option(
    "--sources",
    default="ynet,panet",
    show_default=True,
    help="Comma-separated source names for normal runs. Default available requested sources: ynet, panet.",
)
@click.option(
    "--date-from",
    default="2026-01-01",
    show_default=True,
    help="Start date YYYY-MM-DD.",
)
@click.option(
    "--date-to",
    default="2026-12-31",
    show_default=True,
    help="End date YYYY-MM-DD.",
)
@click.option(
    "--max-per-source",
    default=50,
    show_default=True,
    type=int,
    help="Max articles to discover per source.",
)
@click.option(
    "--stage",
    "stages",
    multiple=True,
    type=click.Choice(
        ["discover", "fetch", "extract", "dedup", "merge", "export"],
        case_sensitive=False,
    ),
    help="Run only specific stages (repeatable). Default: all six stages.",
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
    date_from: str,
    date_to: str,
    max_per_source: int,
    stages: tuple[str, ...],
    jaro_threshold: float | None,
    cosine_threshold: float | None,
    log_level: str,
    run_id: str | None,
) -> None:
    """Run the homicide-news scraping/AI pipeline end-to-end."""
    configure_logging(log_level)

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

    if not query:
        click.echo("ERROR: --query is required (unless --enrich-case is set).", err=True)
        sys.exit(2)

    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    stage_set: set[str] = (
        {s.lower() for s in stages}
        if stages
        else {"discover", "fetch", "extract", "dedup", "merge", "export"}
    )

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

    pipeline = Pipeline(settings, run_id=run_id)

    click.echo(f"Pipeline starting | run_id={pipeline.run_id}")
    click.echo(f"  Query:       {query}")
    click.echo(f"  Sources:     {source_list}")
    click.echo(f"  Date range:  {date_from} to {date_to}")
    click.echo(f"  Stages:      {sorted(stage_set)}")
    click.echo(f"  DB path:     {settings.db_path}")
    click.echo(f"  Output dir:  {settings.output_dir}")
    click.echo("")

    try:
        stats = asyncio.run(
            pipeline.run(
                query=query,
                sources=source_list,
                date_from=date_from,
                date_to=date_to,
                max_per_source=max_per_source,
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
