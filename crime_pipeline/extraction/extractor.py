"""
Async LLM extractor for crime-news articles.

`ArticleExtractor` wraps the Google Gemini async client with:
- temperature=0, configurable max_tokens
- asyncio.Semaphore-based concurrency cap
- Native JSON output mode (response_mime_type="application/json")
- One automatic retry on JSON / schema validation failure
- Structured result dict compatible with the pipeline's storage layer
"""

from __future__ import annotations

import asyncio
import time

import structlog
from google import genai
from google.genai import types

log = structlog.get_logger()


class ArticleExtractor:
    """
    Async extractor that converts raw article text into validated JSON
    using Google Gemini.

    Args:
        api_key:     Google Gemini API key.
        model:       Gemini model ID. Defaults to ``"gemini-2.5-flash"``.
        max_tokens:  Maximum output tokens per call. Defaults to ``1024``.
        concurrency: Maximum simultaneous in-flight API requests.
                     Controlled via ``asyncio.Semaphore``. Defaults to ``8``.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        max_tokens: int = 1024,
        concurrency: int = 8,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        article_text: str,
        language: str,
        published_date: str | None,
        source: str,
    ) -> dict:
        """
        Extract structured data from a single article.

        Uses Gemini's native JSON output mode for reliable structured output.
        On a validation failure the extractor makes exactly one additional
        API call that includes the original bad response and a corrective
        instruction.

        Args:
            article_text:   Full article body.
            language:       ``"ar"`` or ``"he"``.
            published_date: ISO 8601 date string or ``None``.
            source:         Publication / feed name (for logging & prompts).

        Returns:
            A dict with keys: extracted_data, raw_response, input_tokens,
            output_tokens, cache_hit, latency_ms, status, error.
        """
        from crime_pipeline.extraction.prompts import SYSTEM_PROMPT, build_user_prompt
        from crime_pipeline.extraction.validator import (
            apply_lethality_fixups,
            build_retry_prompt,
            validate_extraction,
        )

        user_content = build_user_prompt(article_text, language, published_date, source)

        base_config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json",
            # Disable Gemini 2.5's "thinking" mode for extraction. With it on
            # the model burns ~95% of max_output_tokens on hidden reasoning
            # and emits a truncated JSON that json-repair then salvages into
            # a mostly-empty dict that validate_extraction accepts as valid
            # — every extracted field except victim_name silently becomes
            # null. Extraction is a structured task; thinking adds cost and
            # subtracts correctness.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        async with self._semaphore:
            start_time = time.time()
            try:
                # ── First attempt ──────────────────────────────────────────
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=user_content,
                    config=base_config,
                )

                latency_ms = int((time.time() - start_time) * 1000)
                raw_response = response.text or ""
                usage = response.usage_metadata
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
                cache_hit = cached_tokens > 0

                # Defensive: detect truncation. A truncated JSON often gets
                # silently "repaired" into a tiny valid dict by json-repair,
                # which validate_extraction would then accept as success even
                # though the data is mostly null. Log a clear warning so
                # operators see this in stats.
                finish_reason = None
                if response.candidates:
                    finish_reason = getattr(
                        response.candidates[0], "finish_reason", None
                    )
                if finish_reason is not None and str(finish_reason).endswith(
                    "MAX_TOKENS"
                ):
                    log.warning(
                        "extraction_truncated_at_max_tokens",
                        source=source,
                        model=self.model,
                        max_tokens=self.max_tokens,
                        output_tokens=output_tokens,
                        thoughts_tokens=getattr(
                            usage, "thoughts_token_count", None
                        ),
                    )

                validated, error = validate_extraction(raw_response)

                # ── Retry on validation failure (once) ─────────────────────
                if validated is None:
                    log.warning(
                        "extraction_validation_failed",
                        error=error,
                        source=source,
                        model=self.model,
                    )
                    retry_user_content = (
                        f"{user_content}\n\n"
                        f"PREVIOUS RESPONSE WAS INVALID: {build_retry_prompt(raw_response, error)}"
                    )
                    retry_response = await self.client.aio.models.generate_content(
                        model=self.model,
                        contents=retry_user_content,
                        config=base_config,
                    )
                    raw_response = retry_response.text or ""
                    retry_usage = retry_response.usage_metadata
                    input_tokens += getattr(retry_usage, "prompt_token_count", 0) or 0
                    output_tokens += getattr(retry_usage, "candidates_token_count", 0) or 0
                    validated, error = validate_extraction(raw_response)

                status = "success" if validated else "parse_failed"

                extracted_data = validated.model_dump(mode="json") if validated else None
                if extracted_data:
                    extracted_data = apply_lethality_fixups(extracted_data, article_text)

                return {
                    # mode="json" serializes date/datetime to ISO strings so
                    # the dict is safely round-trippable through SQLAlchemy's
                    # JSON column type.
                    "extracted_data": extracted_data,
                    "raw_response": raw_response,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_hit": cache_hit,
                    "latency_ms": latency_ms,
                    "status": status,
                    "error": error,
                }

            except Exception as exc:
                latency_ms = int((time.time() - start_time) * 1000)
                log.error(
                    "extraction_api_error",
                    error=str(exc),
                    source=source,
                    model=self.model,
                )
                return {
                    "extracted_data": None,
                    "raw_response": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_hit": False,
                    "latency_ms": latency_ms,
                    "status": "api_error",
                    "error": str(exc),
                }

    async def extract_batch(self, articles: list[dict]) -> list[dict]:
        """
        Extract structured data from multiple articles concurrently.

        Concurrency is bounded by the ``asyncio.Semaphore`` set at
        construction time.
        """
        tasks = [
            self.extract(
                article_text=a["article_text"],
                language=a["language"],
                published_date=a.get("published_at"),
                source=a["source"],
            )
            for a in articles
        ]
        return await asyncio.gather(*tasks)
