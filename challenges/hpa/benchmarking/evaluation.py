"""Pure HPA image-results benchmark operation."""

from __future__ import annotations

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
