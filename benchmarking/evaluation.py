"""Benchmark an already merged image-results JSONL file.

Input: one per-image JSON object per line. The exact input fields
and example are in ``participant/benchmarking/README.md``.
Outputs: comparison report, evaluation aggregate, and numeric score-result JSON files.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .aggregate import (
    EvaluationAggregate,
    write_evaluation_aggregate,
)
from .image_results import iter_image_results
from .reducer import ImageResultsReducer
from .score import score_evaluation, write_score_result
from .report import write_comparison_report


def benchmark_image_results(
    image_results_path: str | Path,
    *,
    dataset_id: str = "local",
    comparison_report_path: str | Path,
    aggregate_path: str | Path,
    score_result_path: str | Path,
) -> EvaluationAggregate:
    """Stream per-image primitive statistics into evaluation-wide benchmark outputs."""
    reducer = ImageResultsReducer(dataset_id=dataset_id)
    for image in iter_image_results(image_results_path):
        reducer.add(image)
    aggregate = reducer.build()

    write_comparison_report(
        dataset_id=aggregate.dataset_id,
        metrics=aggregate.metrics,
        compared_images=aggregate.record_count,
        compared_pixels=aggregate.element_count,
        output_path=comparison_report_path,
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
