"""Write the final evaluation-wide diagnostic report."""

from __future__ import annotations

import json
from pathlib import Path

from .metrics import ComparisonMetrics


COMPARISON_REPORT_SCHEMA = "cm4ai-comparison-report"


def write_comparison_report(
    *,
    dataset_id: str,
    metrics: ComparisonMetrics,
    compared_images: int,
    compared_pixels: int,
    output_path: str | Path,
) -> dict[str, object]:
    """Write global metrics already computed by BenchmarkEvaluation."""
    report = {
        "schema": COMPARISON_REPORT_SCHEMA,
        "dataset_id": dataset_id,
        "mean_absolute_error": metrics.mean_absolute_error,
        "root_mean_squared_error": metrics.root_mean_squared_error,
        "cosine_similarity": metrics.cosine_similarity,
        "maximum_absolute_error": metrics.maximum_absolute_error,
        "compared_images": compared_images,
        "compared_pixels": compared_pixels,
        "status": "complete",
    }
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return report
