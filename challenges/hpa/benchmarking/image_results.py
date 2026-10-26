"""Per-image comparison statistics consumed by the public benchmark."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError, model_validator

from anvil_dream_participant._validation import StrictModel
from .contract import BenchmarkContractError
from .metrics import ComparisonMetrics, _is_within_squared_error_bound

PIXELS_PER_IMAGE = 512 * 512
_IMAGE_RESULT_FIELDS = frozenset({"sample_id", "metrics"})
_METRIC_FIELDS = frozenset(
    {
        "absolute_error_sum",
        "squared_error_sum",
        "dot_product_sum",
        "prediction_square_sum",
        "target_square_sum",
        "maximum_absolute_error",
        "mean_absolute_error",
        "root_mean_squared_error",
        "cosine_similarity",
    }
)


class _MetricsWire(StrictModel):
    absolute_error_sum: float
    squared_error_sum: float
    dot_product_sum: float
    prediction_square_sum: float
    target_square_sum: float
    maximum_absolute_error: float
    mean_absolute_error: float
    root_mean_squared_error: float
    cosine_similarity: float | None

    @model_validator(mode="before")
    @classmethod
    def _fields(cls, value: object) -> object:
        if not isinstance(value, dict) or set(value) != _METRIC_FIELDS:
            raise ValueError("metrics has unexpected fields")
        return value


class _ImageResultWire(StrictModel):
    sample_id: str
    metrics: _MetricsWire

    @model_validator(mode="before")
    @classmethod
    def _fields(cls, value: object) -> object:
        if not isinstance(value, dict) or set(value) != _IMAGE_RESULT_FIELDS:
            raise ValueError("image result has unexpected fields")
        return value


ImageResultContractError = BenchmarkContractError


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\x00" in value:
        raise ImageResultContractError(f"{field} must be a non-empty trimmed string")
    return value


def _number(value: object, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImageResultContractError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ImageResultContractError(f"{field} must be finite")
    if nonnegative and result < 0:
        raise ImageResultContractError(f"{field} must be non-negative")
    return result


@dataclass(frozen=True)
class ImageResult:
    """One image's comparison measurements and identity."""

    sample_id: str
    metrics: ComparisonMetrics

    def as_dict(self) -> dict[str, object]:
        return {
            "sample_id": self.sample_id,
            "metrics": self.metrics.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ImageResult":
        try:
            wire = _ImageResultWire.model_validate(value)
        except ValidationError as error:
            raise ImageResultContractError(str(error)) from error
        raw_metrics = wire.metrics
        sums = dict(
            absolute_error_sum=_number(
                raw_metrics.absolute_error_sum, "absolute_error_sum", nonnegative=True
            ),
            squared_error_sum=_number(
                raw_metrics.squared_error_sum, "squared_error_sum", nonnegative=True
            ),
            dot_product_sum=_number(raw_metrics.dot_product_sum, "dot_product_sum"),
            prediction_square_sum=_number(
                raw_metrics.prediction_square_sum,
                "prediction_square_sum",
                nonnegative=True,
            ),
            target_square_sum=_number(
                raw_metrics.target_square_sum, "target_square_sum", nonnegative=True
            ),
            maximum_absolute_error=_number(
                raw_metrics.maximum_absolute_error,
                "maximum_absolute_error",
                nonnegative=True,
            ),
        )
        if sums["maximum_absolute_error"] > sums["absolute_error_sum"]:
            raise ImageResultContractError("maximum_absolute_error exceeds absolute_error_sum")
        if not _is_within_squared_error_bound(
            sums["squared_error_sum"],
            sums["maximum_absolute_error"],
            PIXELS_PER_IMAGE,
        ):
            raise ImageResultContractError("squared_error_sum exceeds pixel_count times max squared")
        cosine = raw_metrics.cosine_similarity
        if cosine is not None:
            cosine = _number(cosine, "cosine_similarity")
            if not -1.000000000001 <= cosine <= 1.000000000001:
                raise ImageResultContractError("cosine_similarity must be between -1 and 1")
        metrics = ComparisonMetrics(
            **sums,
            mean_absolute_error=_number(
                raw_metrics.mean_absolute_error, "mean_absolute_error", nonnegative=True
            ),
            root_mean_squared_error=_number(
                raw_metrics.root_mean_squared_error,
                "root_mean_squared_error",
                nonnegative=True,
            ),
            cosine_similarity=cosine,
        )
        if metrics != ComparisonMetrics.from_sums(**sums, element_count=PIXELS_PER_IMAGE):
            raise ImageResultContractError("derived metrics do not match primitive statistics")
        return cls(
            sample_id=_text(wire.sample_id, "sample_id"),
            metrics=metrics,
        )


def iter_image_results(path: str | Path) -> Iterator[ImageResult]:
    """Read and validate one JSON object per line without loading the file."""
    source = Path(path).expanduser()
    try:
        with source.open("r", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ImageResultContractError(f"JSONL line {line_number} is blank")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ImageResultContractError(
                        f"JSONL line {line_number} is not valid JSON"
                    ) from error
                try:
                    yield ImageResult.from_dict(value)
                except ImageResultContractError as error:
                    raise ImageResultContractError(f"JSONL line {line_number}: {error}") from error
    except OSError as error:
        raise ImageResultContractError(f"could not read image results: {error}") from error
