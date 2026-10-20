"""Incremental reduction of validated per-image results into one aggregate."""

from __future__ import annotations

import math

from .aggregate import EvaluationAggregate
from .contract import EvaluationResultContractError, _text
from .image_results import PIXELS_PER_IMAGE, ImageResult
from .metrics import ComparisonMetrics

_ADDITIVE_SUM_FIELDS = (
    "absolute_error_sum",
    "squared_error_sum",
    "dot_product_sum",
    "prediction_square_sum",
    "target_square_sum",
)


class _AccurateSum:
    """Maintain an fsum-style partial expansion using constant small memory."""

    def __init__(self) -> None:
        self._partials: list[float] = []

    def add(self, value: float) -> None:
        partials = self._partials
        index = 0
        for other in partials:
            if abs(value) < abs(other):
                value, other = other, value
            high = value + other
            low = other - (high - value)
            if low:
                partials[index] = low
                index += 1
            value = high
        partials[index:] = [value]

    def total(self) -> float:
        return math.fsum(self._partials)


class ImageResultsReducer:
    """Reduce ordered per-image statistics into one evaluation-wide aggregate."""

    def __init__(self, *, dataset_id: str) -> None:
        self._dataset_id = _text(dataset_id, "dataset_id")
        self._sums = {name: _AccurateSum() for name in _ADDITIVE_SUM_FIELDS}
        self._maximum_absolute_error = 0.0
        self._record_count = 0

    @property
    def record_count(self) -> int:
        return self._record_count

    def add(self, image: ImageResult) -> None:
        """Add one already-validated image result."""
        for name in _ADDITIVE_SUM_FIELDS:
            self._sums[name].add(getattr(image.metrics, name))
        self._maximum_absolute_error = max(
            self._maximum_absolute_error, image.metrics.maximum_absolute_error
        )
        self._record_count += 1

    def build(self) -> EvaluationAggregate:
        """Derive one aggregate without changing the accumulated state."""
        if self._record_count == 0:
            raise EvaluationResultContractError("image results must contain at least one record")
        element_count = self._record_count * PIXELS_PER_IMAGE
        metrics = ComparisonMetrics.from_sums(
            **{name: self._sums[name].total() for name in _ADDITIVE_SUM_FIELDS},
            maximum_absolute_error=self._maximum_absolute_error,
            element_count=element_count,
        )
        return EvaluationAggregate(
            dataset_id=self._dataset_id,
            record_count=self._record_count,
            element_count=element_count,
            metrics=metrics,
        )
