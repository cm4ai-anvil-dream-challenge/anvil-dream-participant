from __future__ import annotations

import unittest

from anvil_dream_participant.benchmarking.contract import EvaluationResultContractError
from anvil_dream_participant.benchmarking.image_results import (
    PIXELS_PER_IMAGE,
    ImageResult,
)
from anvil_dream_participant.benchmarking.metrics import ComparisonMetrics
from anvil_dream_participant.benchmarking.reducer import ImageResultsReducer


def _image(
    sample_id: str,
    *,
    absolute: float,
    squared: float,
    dot: float,
    prediction_squared: float,
    target_squared: float,
    maximum: float,
) -> ImageResult:
    return ImageResult(
        sample_id=sample_id,
        metrics=ComparisonMetrics.from_sums(
            absolute_error_sum=absolute,
            squared_error_sum=squared,
            dot_product_sum=dot,
            prediction_square_sum=prediction_squared,
            target_square_sum=target_squared,
            maximum_absolute_error=maximum,
            element_count=PIXELS_PER_IMAGE,
        ),
    )


class ImageResultsReducerTests(unittest.TestCase):
    def test_reduces_primitive_sums_and_derives_metrics(self) -> None:
        reducer = ImageResultsReducer(dataset_id="tiny")
        reducer.add(
            _image(
                "cell-0",
                absolute=1,
                squared=1,
                dot=1,
                prediction_squared=1,
                target_squared=1,
                maximum=1,
            )
        )
        reducer.add(
            _image(
                "cell-1",
                absolute=2,
                squared=4,
                dot=2,
                prediction_squared=4,
                target_squared=4,
                maximum=2,
            )
        )
        reducer.add(
            _image(
                "cell-2",
                absolute=3,
                squared=9,
                dot=3,
                prediction_squared=9,
                target_squared=9,
                maximum=3,
            )
        )

        aggregate = reducer.build()

        self.assertEqual(aggregate.dataset_id, "tiny")
        self.assertEqual(aggregate.record_count, 3)
        self.assertEqual(aggregate.element_count, 3 * PIXELS_PER_IMAGE)
        self.assertEqual(
            aggregate.metrics,
            ComparisonMetrics.from_sums(
                absolute_error_sum=6,
                squared_error_sum=14,
                dot_product_sum=6,
                prediction_square_sum=14,
                target_square_sum=14,
                maximum_absolute_error=3,
                element_count=3 * PIXELS_PER_IMAGE,
            ),
        )

    def test_build_is_idempotent_and_does_not_change_accumulated_state(self) -> None:
        reducer = ImageResultsReducer(dataset_id="tiny")
        reducer.add(
            _image(
                "cell-0",
                absolute=1,
                squared=1,
                dot=1,
                prediction_squared=1,
                target_squared=1,
                maximum=1,
            )
        )

        first = reducer.build()
        second = reducer.build()
        self.assertEqual(first, second)

        reducer.add(
            _image(
                "cell-1",
                absolute=2,
                squared=4,
                dot=2,
                prediction_squared=4,
                target_squared=4,
                maximum=2,
            )
        )
        third = reducer.build()
        self.assertEqual(third.record_count, 2)
        self.assertEqual(third.metrics.absolute_error_sum, 3)

    def test_accurate_sums_retain_small_values_between_large_ones(self) -> None:
        reducer = ImageResultsReducer(dataset_id="tiny")
        for value in (1e16, 1.0, -1e16):
            reducer.add(
                _image(
                    "cell",
                    absolute=0,
                    squared=0,
                    dot=value,
                    prediction_squared=0,
                    target_squared=0,
                    maximum=0,
                )
            )

        self.assertEqual(reducer.build().metrics.dot_product_sum, 1.0)

    def test_empty_reducer_rejects(self) -> None:
        with self.assertRaisesRegex(EvaluationResultContractError, "at least one record"):
            ImageResultsReducer(dataset_id="tiny").build()

    def test_dataset_id_must_be_a_non_empty_string(self) -> None:
        with self.assertRaisesRegex(EvaluationResultContractError, "dataset_id"):
            ImageResultsReducer(dataset_id="")


if __name__ == "__main__":
    unittest.main()
