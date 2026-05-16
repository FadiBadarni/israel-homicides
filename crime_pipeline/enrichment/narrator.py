"""Post-reconcile generation of memorial-register case narratives.

Produces a 2-3 sentence factual summary per case in Arabic, Hebrew and
English. Runs after reconcile + name_enrichment + the final declarative
filter, so we only spend API calls on cases that actually ship.

Voice: dignified, restrained, factual. Strict no-inference rules
(motive, relationships, criminal background) — see ``SYSTEM_PROMPT``.

Caching contract:
    Keyed by (canonical_case_id, sources_hash, model_version).
    - sources_hash invalidates when the URL set under the case changes
      (additive enrichment, new merge, source dropped by quality_pass).
    - model_version invalidates when we change the prompt or model.

Re-runs without changes are zero-cost cache reads.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog
from google import genai
from google.genai import types
from sqlalchemy import select

from crime_pipeline.models import CaseNarrative, RawArticle

log = structlog.get_logger()


# Bump when prompt or model changes — invalidates the cache.
NARRATOR_VERSION = "gemini-2.5-flash:v1"


SYSTEM_PROMPT = """You are summarizing a documented homicide case for a public Arabic/Hebrew memorial register that records victims of crime in Arab society in Israel.

Voice: dignified, restrained, factual. This is not journalism and not eulogy — it is a public record entry.

HARD RULES:
1. Use ONLY facts present in the provided article bodies or structured fields. If something is not stated, do not infer it.
2. Do NOT speculate about motive, relationships between victim and suspect, family conflicts, or criminal background unless an article explicitly states them as confirmed fact.
3. Do NOT use loaded framing ("brutal", "tragic", "senseless"). Let the facts speak.
4. Refer to the victim by their name. Do not call them "the victim" repeatedly.
5. 2-3 short sentences. Plain prose, no headlines.
6. If a name field (victim_name_ar / victim_name_he / victim_name_en) is null, do NOT transliterate from another script — refer to the victim by whichever attested name fits the output language, even if it means using a foreign-script name in a sentence of another language. Source-attested names are inviolable.

Output a single JSON object:
{
  "ar": "<Arabic summary, 2-3 sentences>",
  "he": "<Hebrew summary, 2-3 sentences>",
  "en": "<English summary, 2-3 sentences>"
}
"""


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def _sources_hash(case: dict[str, Any]) -> str:
    """SHA256 of the sorted source-URL set. Stable across re-runs as
    long as the set of contributing sources doesn't change."""
    urls = sorted(
        s.get("url", "") for s in (case.get("sources") or []) if s.get("url")
    )
    return hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


_STRUCTURED_FIELDS = (
    "victim_name_ar",
    "victim_name_he",
    "victim_name_en",
    "victim_age",
    "victim_gender",
    "incident_date",
    "death_date",
    "city",
    "neighborhood",
    "district",
    "weapon_type",
    "suspect_status",
    "legal_status",
    "incident_type",
)


def _format_structured(case: dict[str, Any]) -> str:
    lines = []
    for k in _STRUCTURED_FIELDS:
        v = case.get(k)
        if v not in (None, "", []):
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _build_user_prompt(case: dict[str, Any], articles: list[tuple[str, str, str]]) -> str:
    structured = _format_structured(case)
    blocks = []
    for i, (url, lang, text) in enumerate(articles, start=1):
        snippet = text.strip()
        if len(snippet) > 4000:
            snippet = snippet[:4000] + "…"
        blocks.append(f"--- Article {i} ({lang}, {url}) ---\n{snippet}")
    article_block = "\n\n".join(blocks) or "(no article bodies available)"
    return (
        "STRUCTURED FIELDS (merged from all sources):\n"
        f"{structured}\n\n"
        "SOURCE ARTICLES:\n\n"
        f"{article_block}\n\n"
        "Now produce the JSON summary as instructed."
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _gather_article_bodies(session: Any, urls: list[str]) -> list[tuple[str, str, str]]:
    """Return (url, language, article_text) for every URL we can resolve."""
    if not urls:
        return []
    rows = session.execute(
        select(RawArticle.url, RawArticle.language, RawArticle.article_text).where(
            RawArticle.url.in_(urls)
        )
    ).all()
    return [(u, l, t) for (u, l, t) in rows if t]


def _read_cache(session: Any, case_id: str, sources_hash: str) -> dict[str, str] | None:
    if not case_id:
        return None
    # Composite primary key lookup. Returns None when the case/hash pair
    # isn't cached or when the model_version has been bumped (prompt or
    # model change).
    row = session.get(CaseNarrative, (case_id, sources_hash))
    if row is None:
        return None
    if row.model_version != NARRATOR_VERSION:
        return None
    return {"ar": row.narrative_ar, "he": row.narrative_he, "en": row.narrative_en}


def _write_cache(
    session: Any,
    case_id: str,
    sources_hash: str,
    narratives: dict[str, str],
) -> None:
    if not case_id:
        return
    existing = session.get(CaseNarrative, (case_id, sources_hash))
    if existing is None:
        session.add(
            CaseNarrative(
                canonical_case_id=case_id,
                sources_hash=sources_hash,
                model_version=NARRATOR_VERSION,
                narrative_ar=narratives.get("ar"),
                narrative_he=narratives.get("he"),
                narrative_en=narratives.get("en"),
                generated_at=datetime.now(timezone.utc),
            )
        )
    else:
        existing.model_version = NARRATOR_VERSION
        existing.narrative_ar = narratives.get("ar")
        existing.narrative_he = narratives.get("he")
        existing.narrative_en = narratives.get("en")
        existing.generated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def _generate_one(
    client: genai.Client,
    case: dict[str, Any],
    articles: list[tuple[str, str, str]],
    model: str,
) -> dict[str, str]:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
        max_output_tokens=1024,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    user_content = _build_user_prompt(case, articles)
    start = time.time()
    response = await client.aio.models.generate_content(
        model=model,
        contents=user_content,
        config=config,
    )
    latency_ms = int((time.time() - start) * 1000)
    raw = response.text or "{}"
    parsed = json.loads(raw)
    log.info(
        "narrator_generated",
        canonical_case_id=case.get("canonical_case_id"),
        latency_ms=latency_ms,
        input_tokens=getattr(response.usage_metadata, "prompt_token_count", 0),
        output_tokens=getattr(response.usage_metadata, "candidates_token_count", 0),
    )
    return {
        "ar": parsed.get("ar"),
        "he": parsed.get("he"),
        "en": parsed.get("en"),
    }


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def attach_cached_narrations(
    cases: list[dict[str, Any]],
    session_factory: Any,
) -> int:
    """Populate ``case_narrative_{ar,he,en}`` from the cache only — no API.

    Counterpart to ``narrate_cases`` for cost-sensitive paths (e.g. a
    ``--build-canonical --no-narrate`` rebuild). Cached entries are zero-
    cost; without this hook, rebuilds silently drop previously generated
    narratives because the gated generator never reads the cache.

    Returns the number of cases that received cached narratives.
    """
    if not cases:
        return 0
    attached = 0
    with session_factory() as session:
        for case in cases:
            case_id = case.get("canonical_case_id") or ""
            if not case_id:
                continue
            h = _sources_hash(case)
            cached = _read_cache(session, case_id, h)
            if cached and any(cached.values()):
                case["case_narrative_ar"] = cached.get("ar")
                case["case_narrative_he"] = cached.get("he")
                case["case_narrative_en"] = cached.get("en")
                attached += 1
    return attached


async def narrate_cases(
    cases: list[dict[str, Any]],
    api_key: str,
    session_factory: Any,
    model: str = "gemini-2.5-flash",
    concurrency: int = 4,
) -> dict[str, int]:
    """Mutate ``cases`` in place by populating ``case_narrative_{ar,he,en}``.

    Args:
        cases: list of case dicts (from ``CanonicalCaseSchema.model_dump``).
        api_key: Gemini API key.
        session_factory: callable producing a new SQLAlchemy Session
            (typically ``db.SessionLocal``). One session is opened for
            the cache reads/writes and committed at the end.
        model: Gemini model id.
        concurrency: max concurrent in-flight API calls.

    Returns:
        Counter dict: ``{cached, generated, failed, skipped}``.
    """
    counter = {"cached": 0, "generated": 0, "failed": 0, "skipped": 0}
    if not cases:
        return counter

    client = genai.Client(api_key=api_key)
    semaphore = asyncio.Semaphore(concurrency)

    with session_factory() as session:
        # Pre-resolve article bodies once per unique URL set so the
        # generation loop doesn't re-query the DB per case.
        all_urls: set[str] = set()
        for c in cases:
            for s in c.get("sources") or []:
                if s.get("url"):
                    all_urls.add(s["url"])
        body_rows = _gather_article_bodies(session, list(all_urls))
        body_by_url = {url: (url, lang, text) for (url, lang, text) in body_rows}

        async def _run_one(case: dict[str, Any]) -> None:
            case_id = case.get("canonical_case_id") or ""
            h = _sources_hash(case)
            cached = _read_cache(session, case_id, h) if case_id else None
            if cached and any(cached.values()):
                case["case_narrative_ar"] = cached.get("ar")
                case["case_narrative_he"] = cached.get("he")
                case["case_narrative_en"] = cached.get("en")
                counter["cached"] += 1
                return

            urls = [s.get("url") for s in (case.get("sources") or []) if s.get("url")]
            articles = [body_by_url[u] for u in urls if u in body_by_url]
            if not articles:
                counter["skipped"] += 1
                log.info(
                    "narrator_skipped_no_bodies",
                    canonical_case_id=case_id,
                    urls=len(urls),
                )
                return

            try:
                async with semaphore:
                    narratives = await _generate_one(client, case, articles, model)
            except Exception as exc:  # noqa: BLE001 — narrator must not block export
                counter["failed"] += 1
                log.warning(
                    "narrator_failed",
                    canonical_case_id=case_id,
                    error=str(exc),
                )
                return

            case["case_narrative_ar"] = narratives.get("ar")
            case["case_narrative_he"] = narratives.get("he")
            case["case_narrative_en"] = narratives.get("en")
            counter["generated"] += 1
            if case_id:
                # Commit each cache row independently so one IntegrityError
                # (e.g. canonical_case_id slug collision the merger missed)
                # doesn't roll back the whole batch and force re-spend on
                # the next run.
                try:
                    _write_cache(session, case_id, h, narratives)
                    session.commit()
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    log.warning(
                        "narrator_cache_write_failed",
                        canonical_case_id=case_id,
                        error=str(exc),
                    )

        await asyncio.gather(*(_run_one(c) for c in cases))

    log.info("narrator_summary", **counter)
    return counter
