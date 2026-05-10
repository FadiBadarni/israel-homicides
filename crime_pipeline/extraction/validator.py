"""
Pydantic v2 validator for raw LLM extraction responses.

Handles JSON fence stripping, parsing, and schema validation against
`ExtractedArticleData`.  On failure, builds a corrective retry prompt.
"""

import json
import re

from crime_pipeline.models import ExtractedArticleData


def extract_json_from_response(raw: str) -> dict:
    """
    Extract a JSON object from an LLM response.

    Strips Markdown code fences, then tries strict json.loads. If that fails
    (Gemini occasionally emits unescaped quotes inside string values, trailing
    commas, etc.) falls back to json-repair which is permissive about real-world
    LLM JSON quirks.

    Args:
        raw: The raw text returned by the LLM.

    Returns:
        A parsed Python dict.
    """
    cleaned = re.sub(r"```(?:json)?\n?", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to json-repair — handles unescaped quotes, trailing commas,
        # missing closing braces, etc.
        try:
            from json_repair import repair_json
            repaired = repair_json(cleaned, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
            # repair_json returns "" or None for unrecoverable input
        except Exception:
            pass
        # Re-raise original error if repair couldn't recover
        raise


def validate_extraction(
    raw_response: str,
) -> tuple[ExtractedArticleData | None, str | None]:
    """
    Validate an LLM extraction response against the canonical schema.

    Args:
        raw_response: The raw text content from the LLM's first response block.

    Returns:
        A 2-tuple:
        - ``(ExtractedArticleData, None)`` on success.
        - ``(None, error_message)`` when JSON parsing or Pydantic validation fails.
    """
    try:
        data = extract_json_from_response(raw_response)
        validated = ExtractedArticleData(**data)
        return validated, None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    except Exception as exc:  # covers pydantic.ValidationError and any other issue
        return None, f"Schema validation error: {exc}"


# Hebrew/Arabic phrases that unambiguously identify an attempted murder where the
# victim survived. LLMs reliably miss these when the article is not written from
# the victim's lethality perspective (e.g. arrest-report articles).
_SURVIVED_PATTERNS = [
    "ניסיון רצח",        # attempted murder (Hebrew)
    "ניסיון ההתנקשות",   # assassination attempt
    "ששרד",              # who survived
    "שרד את",            # survived the
    "נפצע ושרד",         # wounded and survived
    "محاولة اغتيال",     # assassination attempt (Arabic)
    "محاولة الاغتيال",
    "نجا من",            # survived (Arabic)
    "لم يُقتل",          # was not killed
]


def apply_lethality_fixups(extracted: dict, article_text: str) -> dict:
    """Override victim_outcome when article text contains unambiguous survival markers.

    Called after the LLM extraction is validated. Takes precedence over the model's
    (unreliable) victim_outcome value for the specific "attempted murder" pattern.
    Only overrides when the existing outcome is None or "critical" — never downgrades
    a confirmed "died".
    """
    current = extracted.get("victim_outcome")
    if current == "died":
        return extracted  # never override a confirmed death

    text = article_text or ""
    for pattern in _SURVIVED_PATTERNS:
        if pattern in text:
            extracted = dict(extracted)
            extracted["victim_outcome"] = "survived"
            break
    return extracted


def build_retry_prompt(original_response: str, error: str) -> str:
    """
    Build a corrective follow-up prompt after a validation failure.

    The prompt is short so it does not consume unnecessary tokens; it
    quotes the first 500 characters of the bad response for context and
    names the specific error.

    Args:
        original_response: The text the model returned that failed validation.
        error:             Human-readable error description from `validate_extraction`.

    Returns:
        A user-role message string to append to the conversation.
    """
    return (
        f"Your previous response had a validation error: {error}\n\n"
        f"Your response was:\n{original_response[:500]}\n\n"
        "Please respond ONLY with valid JSON matching the schema. No other text."
    )
