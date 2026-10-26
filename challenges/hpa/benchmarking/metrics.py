"""Shared per-image and evaluation-wide comparison statistics."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ComparisonMetrics:
    """Primitive comparison sums and their derived metrics."""

    absolute_error_sum: float
    squared_error_sum: float
    dot_product_sum: float
    prediction_square_sum: float
    target_square_sum: float
    maximum_absolute_error: float
    mean_absolute_error: float
    root_mean_squared_error: float
    cosine_similarity: float | None

    @classmethod
    def from_sums(
        cls,
        *,
        absolute_error_sum: float,
        squared_error_sum: float,
        dot_product_sum: float,
        prediction_square_sum: float,
        target_square_sum: float,
        maximum_absolute_error: float,
        element_count: int,
    ) -> ComparisonMetrics:
        if isinstance(element_count, bool) or not isinstance(element_count, int) or element_count < 1:
            raise ValueError("element_count must be a positive integer")
        denominator = math.sqrt(prediction_square_sum) * math.sqrt(target_square_sum)
        cosine = None if denominator == 0.0 else dot_product_sum / denominator
        if cosine is not None:
            if not math.isfinite(cosine):
                raise ValueError("cosine_similarity must be finite")
            cosine = max(-1.0, min(1.0, cosine))
        return cls(
            absolute_error_sum=absolute_error_sum,
            squared_error_sum=squared_error_sum,
            dot_product_sum=dot_product_sum,
            prediction_square_sum=prediction_square_sum,
            target_square_sum=target_square_sum,
            maximum_absolute_error=maximum_absolute_error,
            mean_absolute_error=absolute_error_sum / element_count,
            root_mean_squared_error=math.sqrt(squared_error_sum / element_count),
            cosine_similarity=cosine,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "absolute_error_sum": self.absolute_error_sum,
            "squared_error_sum": self.squared_error_sum,
            "dot_product_sum": self.dot_product_sum,
            "prediction_square_sum": self.prediction_square_sum,
            "target_square_sum": self.target_square_sum,
            "maximum_absolute_error": self.maximum_absolute_error,
            "mean_absolute_error": self.mean_absolute_error,
            "root_mean_squared_error": self.root_mean_squared_error,
            "cosine_similarity": self.cosine_similarity,
        }


def _is_within_squared_error_bound(
    squared_error_sum: float,
    maximum_absolute_error: float,
    element_count: int,
) -> bool:
    """Allow a few rounding ULPs when checking the maximum-derived bound."""
    maximum_square = maximum_absolute_error * maximum_absolute_error
    if not math.isfinite(maximum_square):
        return True
    try:
        squared_bound = maximum_square * element_count
    except OverflowError:
        return True
    if not math.isfinite(squared_bound):
        return True
    return squared_error_sum <= squared_bound or math.isclose(
        squared_error_sum,
        squared_bound,
        rel_tol=4 * math.ulp(1.0),
    )
