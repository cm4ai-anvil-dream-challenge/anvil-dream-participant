from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from anvil_dream_participant.benchmarking.contract import EvaluationResultContractError
from anvil_dream_participant.benchmarking.image_results import PIXELS_PER_IMAGE
from anvil_dream_participant.benchmarking.metrics import ComparisonMetrics
from anvil_dream_participant.benchmarking.report import (
    COMPARISON_REPORT_SCHEMA,
    ComparisonReport,
    write_comparison_report,
)


def _metrics() -> ComparisonMetrics:
    return ComparisonMetrics.from_sums(
        absolute_error_sum=10.0,
        squared_error_sum=10.0,
        dot_product_sum=0.0,
        prediction_square_sum=10.0,
        target_square_sum=10.0,
        maximum_absolute_error=1.0,
        element_count=PIXELS_PER_IMAGE,
    )


class ComparisonReportTests(unittest.TestCase):
    def test_write_returns_a_typed_report_with_canonical_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "comparison-report.json"
            report = write_comparison_report(
                dataset_id="tiny",
                metrics=_metrics(),
                compared_images=1,
                compared_pixels=PIXELS_PER_IMAGE,
                output_path=path,
            )
            written = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsInstance(report, ComparisonReport)
        self.assertEqual(written, report.as_dict())
        self.assertEqual(report.schema, COMPARISON_REPORT_SCHEMA)
        self.assertEqual(report.status, "complete")
        self.assertEqual(ComparisonReport.from_dict(written), report)

    def test_rejects_unknown_fields_and_wrong_schema_or_status(self) -> None:
        report = ComparisonReport(
            dataset_id="tiny",
            mean_absolute_error=1.0,
            root_mean_squared_error=1.0,
            cosine_similarity=0.0,
            maximum_absolute_error=1.0,
            compared_images=1,
            compared_pixels=PIXELS_PER_IMAGE,
        )
        value = report.as_dict()
        value["unexpected"] = True
        with self.assertRaisesRegex(EvaluationResultContractError, "unexpected schema"):
            ComparisonReport.from_dict(value)

        value = report.as_dict()
        value["schema"] = "other"
        with self.assertRaisesRegex(EvaluationResultContractError, "schema"):
            ComparisonReport.from_dict(value)

        value = report.as_dict()
        value["status"] = "failed"
        with self.assertRaisesRegex(EvaluationResultContractError, "status"):
            ComparisonReport.from_dict(value)

    def test_rejects_boolean_counts_and_invalid_metrics(self) -> None:
        report = ComparisonReport(
            dataset_id="tiny",
            mean_absolute_error=1.0,
            root_mean_squared_error=1.0,
            cosine_similarity=0.0,
            maximum_absolute_error=1.0,
            compared_images=1,
            compared_pixels=PIXELS_PER_IMAGE,
        )
        value = report.as_dict()
        value["compared_images"] = True
        with self.assertRaisesRegex(EvaluationResultContractError, "compared_images"):
            ComparisonReport.from_dict(value)

        value = report.as_dict()
        value["mean_absolute_error"] = float("nan")
        with self.assertRaisesRegex(EvaluationResultContractError, "finite"):
            ComparisonReport.from_dict(value)

        value = report.as_dict()
        value["cosine_similarity"] = 2.0
        with self.assertRaisesRegex(EvaluationResultContractError, "between -1 and 1"):
            ComparisonReport.from_dict(value)

    def test_writer_rejects_non_positive_counts(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvaluationResultContractError, "compared_images"):
                write_comparison_report(
                    dataset_id="tiny",
                    metrics=_metrics(),
                    compared_images=0,
                    compared_pixels=PIXELS_PER_IMAGE,
                    output_path=Path(directory) / "report.json",
                )


if __name__ == "__main__":
    unittest.main()
