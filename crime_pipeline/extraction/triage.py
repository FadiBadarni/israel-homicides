"""
C-stage triage classifier for the crime_pipeline.

Sits between fetch and extract. For each fetched article, send the title +
first ~300 chars of body to Gemini-2.5-flash and get back ONE incident_type
label. Articles labeled ``homicide`` / ``attempted_homicide`` / ``unknown``
proceed to full extraction; everything else is dropped.

Why this matters: at 60+ pages per broad search query, ~95% of fetched
articles are not homicides. Full extraction is ~5K input tokens per article
($0.0004); triage is ~600 input tokens ($0.00005). The 10x cost reduction
keeps the pipeline affordable at scale without sacrificing recall, because
the triage is recall-biased (uncertain → ``unknown`` → keep).

The triage's incident_type is a HINT, not authoritative. The post-extract
relevance gate is still the final word on what enters dedup.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog
from google import genai
from google.genai import types

from crime_pipeline.extraction.validator import extract_json_from_response

log = structlog.get_logger()

# Bumped whenever the prompt or category set changes meaningfully. Persisted
# alongside each triage decision so we can identify rows that need re-running
# after a prompt update without diffing source.
TRIAGE_PROMPT_VERSION = "v1"

# Categories MUST match crime_pipeline.models.ExtractedArticleData.incident_type
# and crime_pipeline.extraction.relevance._HOMICIDE_TYPES /
# _NON_HOMICIDE_TYPES — single source of truth for what's "kept" downstream.
_VALID_INCIDENT_TYPES = frozenset({
    "homicide",
    "attempted_homicide",
    "accident",
    "suicide",
    "historical",
    "other_crime",
    "non_crime",
    "unknown",
})

# What the triage status maps to from incident_type. Recall-biased: anything
# not explicitly non-homicide proceeds.
_KEEP_TYPES = frozenset({"homicide", "attempted_homicide"})
_MAYBE_TYPES = frozenset({"unknown"})
# Everything else is implicitly "no" via the elif default in `_status_for`.


def _status_for(incident_type: str) -> str:
    if incident_type in _KEEP_TYPES:
        return "yes"
    if incident_type in _MAYBE_TYPES:
        return "maybe"
    return "no"


TRIAGE_SYSTEM_PROMPT = """You are a fast triage classifier for Israeli crime news.

Given the TITLE and FIRST PARAGRAPH of an Arabic or Hebrew article, decide which incident type it describes. Pick exactly ONE:

- "homicide": confirmed deliberate killing — current case (e.g. נרצח / قُتل / مقتل with named victim, criminal investigation)
- "attempted_homicide": deliberate attempt that did NOT confirm death — wounded, critical, in hospital after a shooting/stabbing/assault. ניסיון רצח / محاولة قتل / إطلاق نار / طعن without explicit death wording
- "accident": non-criminal death — workplace (תאונת עבודה / حادث عمل), traffic (תאונת דרכים / حادث طرق), fall (سقوط), drowning, electrocution
- "suicide": self-inflicted death (התאבדות / انتحار)
- "historical": retrospective, anniversary, year-end statistics, "since the start of the year N people have been killed", commemorations of past killings (יום האדמה / يوم الأرض). Deaths described are NOT a current incident
- "other_crime": criminal but not homicide — fraud (احتيال), theft (سرقة), arrest for cheating (شبهة الغش), drug bust
- "non_crime": not about a crime — protests (احتجاج), opinion, sports, culture, politics, weather
- "unknown": cannot tell from title + lede alone (paywall snippet, ambiguous wording). PREFER "unknown" over "non_crime" when in doubt — this is a recall-biased triage; full extraction will follow.

Output ONLY a JSON object with this exact shape:
{"incident_type": "<one of the above>"}

No prose. No markdown. No additional fields."""


def build_triage_user_prompt(title: str | None, lede: str) -> str:
    """Format the per-article user message for the triage classifier."""
    safe_title = (title or "(no title)").strip()
    safe_lede = (lede or "").strip()[:600]
    return f"TITLE: {safe_title}\n\nFIRST PARAGRAPH:\n{safe_lede}"


@dataclass(slots=True)
class TriageResult:
    article_id: str
    status: str  # "yes" | "maybe" | "no"
    incident_type: str  # one of _VALID_INCIDENT_TYPES
    reason: str  # short human-readable; defaults to incident_type
    model_version: str  # e.g. "gemini-2.5-flash:v1"
    input_tokens: int
    output_tokens: int
    error: str | None = None


class Triager:
    """Async batch triage classifier using Gemini-2.5-flash with thinking off."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        max_tokens: int = 64,
        concurrency: int = 8,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self._semaphore = asyncio.Semaphore(concurrency)

    async def triage_one(
        self,
        article_id: str,
        title: str | None,
        lede: str,
    ) -> TriageResult:
        user_prompt = build_triage_user_prompt(title, lede)
        config = types.GenerateContentConfig(
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            temperature=0,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json",
            # Same fix as the extractor: thinking-mode off so output isn't
            # silently truncated. ~64 tokens is plenty for {"incident_type": "..."}.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        async with self._semaphore:
            try:
                resp = await self.client.aio.models.generate_content(
                    model=self.model, contents=user_prompt, config=config,
                )
                usage = resp.usage_metadata
                in_tok = getattr(usage, "prompt_token_count", 0) or 0
                out_tok = getattr(usage, "candidates_token_count", 0) or 0
                raw = resp.text or ""
                data = extract_json_from_response(raw)
                incident_type = (data.get("incident_type") or "").strip().lower()
                if incident_type not in _VALID_INCIDENT_TYPES:
                    # Recall-bias: when the LLM emits a category we don't know,
                    # fall back to "unknown" so the article isn't silently dropped.
                    log.warning(
                        "triage_unknown_category",
                        article_id=article_id[:8],
                        raw_value=incident_type,
                    )
                    incident_type = "unknown"
                return TriageResult(
                    article_id=article_id,
                    status=_status_for(incident_type),
                    incident_type=incident_type,
                    reason=incident_type,
                    model_version=f"{self.model}:{TRIAGE_PROMPT_VERSION}",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )
            except Exception as exc:  # pragma: no cover — network paths
                log.warning(
                    "triage_call_failed",
                    article_id=article_id[:8],
                    error=str(exc)[:200],
                )
                # On any error, fall back to MAYBE so the article still
                # proceeds to full extraction. Better to spend the extract
                # tokens than silently lose a real homicide.
                return TriageResult(
                    article_id=article_id,
                    status="maybe",
                    incident_type="unknown",
                    reason="triage_error_fallback",
                    model_version=f"{self.model}:{TRIAGE_PROMPT_VERSION}",
                    input_tokens=0,
                    output_tokens=0,
                    error=str(exc)[:200],
                )

    async def triage_batch(
        self,
        articles: list[dict[str, Any]],
    ) -> list[TriageResult]:
        """Triage a batch of {article_id, title, lede} dicts concurrently."""
        if not articles:
            return []
        start = time.time()
        results = await asyncio.gather(*[
            self.triage_one(a["article_id"], a.get("title"), a.get("lede") or "")
            for a in articles
        ])
        log.info(
            "triage_batch_complete",
            count=len(results),
            elapsed_seconds=round(time.time() - start, 2),
        )
        return results
