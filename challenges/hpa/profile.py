"""Public challenge profile for the HPA participant workflow."""

from __future__ import annotations

from pathlib import Path

from ...challenge import ChallengeProfile


def _validate_workflow(path: Path) -> None:
    from .workflows import parse_participant_workflow

    parse_participant_workflow(path, require_exact_participant_shape=True)


def _benchmark(
    results: Path,
    *,
    dataset_id: str,
    comparison_report_path: Path,
    aggregate_path: Path,
    score_result_path: Path,
) -> object:
    from .benchmarking.evaluation import benchmark_image_results

    return benchmark_image_results(
        results,
        dataset_id=dataset_id,
        comparison_report_path=comparison_report_path,
        aggregate_path=aggregate_path,
        score_result_path=score_result_path,
    )


PROFILE = ChallengeProfile(
    name="HPA",
    evaluation_id="9620030",
    queue_name="CM4AI AnVIL DREAM Challenge Evaluation",
    validate_workflow=_validate_workflow,
    benchmark=_benchmark,
)
