"""Write the final evaluation-wide diagnostic report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contract import (
    EvaluationResultContractError,
    _canonical_bytes,
    _nonnegative_number,
    _number,
    _positive_int,
    _text,
)
from .metrics import ComparisonMetrics


COMPARISON_REPORT_SCHEMA = "cm4ai-comparison-report"


@dataclass(frozen=True)
class ComparisonReport:
    """The diagnostic report written after one evaluation completes."""

    dataset_id: str
    mean_absolute_error: float
    root_mean_squared_error: float
    cosine_similarity: float | None
    maximum_absolute_error: float
    compared_images: int
    compared_pixels: int
    status: str = "complete"
    schema: str = COMPARISON_REPORT_SCHEMA

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "mean_absolute_error": self.mean_absolute_error,
            "root_mean_squared_error": self.root_mean_squared_error,
            "cosine_similarity": self.cosine_similarity,
            "maximum_absolute_error": self.maximum_absolute_error,
            "compared_images": self.compared_images,
            "compared_pixels": self.compared_pixels,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComparisonReport:
        required = {
            "schema",
            "dataset_id",
            "mean_absolute_error",
            "root_mean_squared_error",
            "cosine_similarity",
            "maximum_absolute_error",
            "compared_images",
            "compared_pixels",
            "status",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise EvaluationResultContractError("comparison report has an unexpected schema")
        if value["schema"] != COMPARISON_REPORT_SCHEMA:
            raise EvaluationResultContractError(
                f"comparison report schema must be {COMPARISON_REPORT_SCHEMA}"
            )
        if value["status"] != "complete":
            raise EvaluationResultContractError(
                "comparison report status must be complete"
            )
        cosine = value["cosine_similarity"]
        if cosine is not None:
            cosine = _number(cosine, "cosine_similarity")
            if not -1.000000000001 <= cosine <= 1.000000000001:
                raise EvaluationResultContractError(
                    "comparison report cosine_similarity must be between -1 and 1"
                )
        return cls(
            dataset_id=_text(value["dataset_id"], "dataset_id"),
            mean_absolute_error=_nonnegative_number(
                value["mean_absolute_error"], "mean_absolute_error"
            ),
            root_mean_squared_error=_nonnegative_number(
                value["root_mean_squared_error"], "root_mean_squared_error"
            ),
            cosine_similarity=cosine,
            maximum_absolute_error=_nonnegative_number(
                value["maximum_absolute_error"], "maximum_absolute_error"
            ),
            compared_images=_positive_int(value["compared_images"], "compared_images"),
            compared_pixels=_positive_int(value["compared_pixels"], "compared_pixels"),
        )


def write_comparison_report(
    *,
    dataset_id: str,
    metrics: ComparisonMetrics,
    compared_images: int,
    compared_pixels: int,
    output_path: str | Path,
) -> ComparisonReport:
    """Write global metrics already computed by BenchmarkEvaluation."""
    report = ComparisonReport(
        dataset_id=dataset_id,
        mean_absolute_error=metrics.mean_absolute_error,
        root_mean_squared_error=metrics.root_mean_squared_error,
        cosine_similarity=metrics.cosine_similarity,
        maximum_absolute_error=metrics.maximum_absolute_error,
        compared_images=compared_images,
        compared_pixels=compared_pixels,
    )
    parsed = ComparisonReport.from_dict(report.as_dict())
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(parsed.as_dict()))
    temporary.replace(destination)
    return parsed
