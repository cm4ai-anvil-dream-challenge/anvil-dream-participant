"""Benchmark an already merged image-results JSONL file.

Input: one per-image JSON object per line. The exact input fields
and example are in ``participant/benchmarking/README.md``.
Outputs: comparison report, evaluation aggregate, and numeric score-result JSON files.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from .aggregate import (
    EvaluationAggregate,
    write_evaluation_aggregate,
)
from .contract import EvaluationResultContractError, _text
from .image_results import PIXELS_PER_IMAGE, iter_image_results
from .metrics import ComparisonMetrics
from .score import score_evaluation, write_score_result
from .report import write_comparison_report


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


def benchmark_image_results(
    image_results_path: str | Path,
    *,
    dataset_id: str = "local",
    comparison_report_path: str | Path,
    aggregate_path: str | Path,
    score_result_path: str | Path,
) -> EvaluationAggregate:
    """Stream per-image primitive statistics into evaluation-wide benchmark outputs."""
    sums = {
        name: _AccurateSum()
        for name in (
            "absolute_error_sum",
            "squared_error_sum",
            "dot_product_sum",
            "prediction_square_sum",
            "target_square_sum",
        )
    }
    maximum_absolute_error = 0.0
    record_count = 0
    dataset_id = _text(dataset_id, "dataset_id")

    for image in iter_image_results(image_results_path):
        sums["absolute_error_sum"].add(image.metrics.absolute_error_sum)
        sums["squared_error_sum"].add(image.metrics.squared_error_sum)
        sums["dot_product_sum"].add(image.metrics.dot_product_sum)
        sums["prediction_square_sum"].add(image.metrics.prediction_square_sum)
        sums["target_square_sum"].add(image.metrics.target_square_sum)
        maximum_absolute_error = max(
            maximum_absolute_error, image.metrics.maximum_absolute_error
        )
        record_count += 1

    if record_count == 0:
        raise EvaluationResultContractError("image results must contain at least one record")
    element_count = record_count * PIXELS_PER_IMAGE

    metrics = ComparisonMetrics.from_sums(
        absolute_error_sum=sums["absolute_error_sum"].total(),
        squared_error_sum=sums["squared_error_sum"].total(),
        dot_product_sum=sums["dot_product_sum"].total(),
        prediction_square_sum=sums["prediction_square_sum"].total(),
        target_square_sum=sums["target_square_sum"].total(),
        maximum_absolute_error=maximum_absolute_error,
        element_count=element_count,
    )
    write_comparison_report(
        dataset_id=dataset_id,
        metrics=metrics,
        compared_images=record_count,
        compared_pixels=element_count,
        output_path=comparison_report_path,
    )
    aggregate = EvaluationAggregate(
        dataset_id=dataset_id,
        record_count=record_count,
        element_count=element_count,
        metrics=metrics,
    )
    write_evaluation_aggregate(aggregate, aggregate_path)
    result = score_evaluation(aggregate)
    write_score_result(result, score_result_path)
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-results", type=Path, required=True)
    parser.add_argument("--dataset-id", default="local", help="output dataset label (default: local)")
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="score-result.json output path")
    args = parser.parse_args()
    try:
        benchmark_image_results(
            args.image_results,
            dataset_id=args.dataset_id,
            comparison_report_path=args.comparison_report,
            aggregate_path=args.aggregate,
            score_result_path=args.output,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
