"""Regression tests for num_victims coercion.

The LLM occasionally emits ``null`` or ``0`` for unspecified victim counts
(common when an article describes a homicide without saying "one person").
Pre-fix, this caused Pydantic to reject the whole extraction with:

    Schema validation error: ... num_victims ... Input should be a valid integer

The validator coerces None / 0 / "" / "null" → 1 so the rest of the
extraction survives.
"""
from __future__ import annotations

import pytest

from crime_pipeline.models import CanonicalCaseSchema, ExtractedArticleData


@pytest.mark.parametrize("bad_value", [None, 0, "", "null"])
def test_extracted_article_coerces_invalid_num_victims_to_one(bad_value) -> None:
    """ExtractedArticleData must accept the LLM's bad inputs and coerce them."""
    record = ExtractedArticleData(num_victims=bad_value)
    assert record.num_victims == 1


@pytest.mark.parametrize("bad_value", [None, 0])
def test_canonical_case_coerces_invalid_num_victims_to_one(bad_value) -> None:
    """CanonicalCaseSchema must do the same — model_dump round-trips
    through dict and a stale 0 / None must not reject re-validation.
    """
    case = CanonicalCaseSchema(num_victims=bad_value)
    assert case.num_victims == 1


def test_real_value_passes_through() -> None:
    record = ExtractedArticleData(num_victims=3)
    assert record.num_victims == 3


def test_default_when_field_omitted() -> None:
    record = ExtractedArticleData()
    assert record.num_victims == 1


def test_negative_still_rejected() -> None:
    """ge=1 still applies for legitimately bad inputs (e.g., -2)."""
    with pytest.raises(Exception):  # pydantic.ValidationError
        ExtractedArticleData(num_victims=-2)
