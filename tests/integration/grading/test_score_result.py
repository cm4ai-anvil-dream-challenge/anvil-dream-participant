from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dream_participant.benchmarking.aggregate import EvaluationAggregate
from dream_participant.benchmarking.contract import EvaluationResultContractError
from dream_participant.benchmarking.metrics import ComparisonMetrics
from dream_participant.benchmarking.score import ScoreResult, score_evaluation, write_score_result


def aggregate_for(
    *,
    absolute_error_sum: float,
    squared_error_sum: float,
    dot_product_sum: float,
    prediction_square_sum: float,
    target_square_sum: float,
    maximum_absolute_error: float,
    element_count: int = 2,
) -> EvaluationAggregate:
    metrics = ComparisonMetrics.from_sums(
        absolute_error_sum=absolute_error_sum,
        squared_error_sum=squared_error_sum,
        dot_product_sum=dot_product_sum,
        prediction_square_sum=prediction_square_sum,
        target_square_sum=target_square_sum,
        maximum_absolute_error=maximum_absolute_error,
        element_count=element_count,
    )
    return EvaluationAggregate(
        dataset_id="tiny",
        record_count=1,
        element_count=element_count,
        metrics=metrics,
    )


class ScoreResultTests(unittest.TestCase):
    def test_equal_weighted_global_metric_score_and_json_contract(self) -> None:
        aggregate = aggregate_for(
            absolute_error_sum=6.0,
            squared_error_sum=20.0,
            dot_product_sum=3.0,
            prediction_square_sum=1.0,
            target_square_sum=25.0,
            maximum_absolute_error=4.0,
        )
        target_rms = math.sqrt(25.0 / 2)
        result = score_evaluation(aggregate)
        expected_components = {
            "mae_score": 1 / (1 + aggregate.metrics.mean_absolute_error / target_rms),
            "rmse_score": 1 / (1 + aggregate.metrics.root_mean_squared_error / target_rms),
            "maximum_error_score": 1 / (1 + 4.0 / target_rms),
            "cosine_similarity_score": (1 + aggregate.metrics.cosine_similarity) / 2,
        }
        for component, expected in expected_components.items():
            self.assertAlmostEqual(result.components[component], expected)
        self.assertAlmostEqual(result.score, math.fsum(expected_components.values()) / 4)
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 1.0)

        with TemporaryDirectory() as directory:
            path = write_score_result(result, Path(directory) / "score.json")
            restored = ScoreResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(restored, result)
        self.assertEqual(restored.aggregate_sha256, aggregate.canonical_sha256())

    def test_zero_target_and_prediction_scores_one(self) -> None:
        result = score_evaluation(
            aggregate_for(
                absolute_error_sum=0.0,
                squared_error_sum=0.0,
                dot_product_sum=0.0,
                prediction_square_sum=0.0,
                target_square_sum=0.0,
                maximum_absolute_error=0.0,
            )
        )
        self.assertEqual(set(result.components.values()), {1.0})
        self.assertEqual(result.score, 1.0)

    def test_zero_target_with_nonzero_prediction_scores_zero(self) -> None:
        result = score_evaluation(
            aggregate_for(
                absolute_error_sum=2.0,
                squared_error_sum=4.0,
                dot_product_sum=0.0,
                prediction_square_sum=4.0,
                target_square_sum=0.0,
                maximum_absolute_error=2.0,
            )
        )
        self.assertEqual(set(result.components.values()), {0.0})
        self.assertEqual(result.score, 0.0)

    def test_parser_rejects_nonfinite_or_inconsistent_component_score(self) -> None:
        result = score_evaluation(
            aggregate_for(
                absolute_error_sum=2.0,
                squared_error_sum=4.0,
                dot_product_sum=0.0,
                prediction_square_sum=4.0,
                target_square_sum=0.0,
                maximum_absolute_error=2.0,
            )
        ).as_dict()
        result["score"] = float("inf")
        with self.assertRaisesRegex(EvaluationResultContractError, "finite"):
            ScoreResult.from_dict(result)
        result = score_evaluation(
            aggregate_for(
                absolute_error_sum=2.0,
                squared_error_sum=4.0,
                dot_product_sum=0.0,
                prediction_square_sum=4.0,
                target_square_sum=0.0,
                maximum_absolute_error=2.0,
            )
        ).as_dict()
        result["score"] = 0.25
        with self.assertRaisesRegex(EvaluationResultContractError, "component mean"):
            ScoreResult.from_dict(result)


if __name__ == "__main__":
    unittest.main()
