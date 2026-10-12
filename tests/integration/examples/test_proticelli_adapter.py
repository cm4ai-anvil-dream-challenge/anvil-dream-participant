from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from tests.integration.examples.proticelli_support import (
    load_participant_module,
    write_weights_zip,
)

MODEL = load_participant_module("proticelli_model.py", "proticelli_model")


class ProtiCelliAdapterTests(unittest.TestCase):
    def test_three_images_sequence_cache_ranked_mapping_and_id_outputs(self) -> None:
        module = load_participant_module("predict.py", "proticelli_runner")
        call_log: list[tuple[str, object]] = []
        values = {
            "microtubules_image": 0,
            "nucleus_image": 128,
            "er_image": 255,
        }

        class Model:
            protein_map = {"TUBB": 0, "CCDC50": 1}

            def __init__(self, **kwargs):
                call_log.append(("model", kwargs))

            def predict(self, images, **kwargs):
                call_log.append(("predict", (images.copy(), kwargs)))
                return {
                    "images": [
                        np.full((512, 512), index, dtype=np.float32) for index in range(len(images))
                    ]
                }

        with TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for prediction_id in ("z-view-2", "a-view-1"):
                paths = {}
                for field, value in values.items():
                    path = root / f"{prediction_id}-{field}.png"
                    Image.fromarray(np.full((512, 512), value, dtype=np.uint8)).save(path)
                    paths[field] = path
                fasta = root / f"{prediction_id}.fasta"
                fasta.write_text(">explicit record\nMAAA\n", encoding="ascii")
                records.append(
                    {
                        "prediction_id": prediction_id,
                        **{field: str(path) for field, path in paths.items()},
                        "fasta_file": str(fasta),
                    }
                )
            manifest = root / "inputs.json"
            manifest.write_text(json.dumps(records), encoding="utf-8")
            weights = root / "weights.zip"
            write_weights_zip(weights)
            output = root / "predictions"
            hits = (
                MODEL.BlastHit("P00001", 100.0, 1e-20, 99.0, 0),
                MODEL.BlastHit("P00002", 90.0, 1e-15, 75.0, 1),
                MODEL.BlastHit("P00003", 80.0, 1e-10, 50.0, 2),
            )
            with (
                patch.dict(sys.modules, {"proticelli": SimpleNamespace(Model=Model)}),
                patch.object(module.proticelli_images, "normalize_inputs", side_effect=lambda images: (
                    np.stack((np.full(images.shape[:-1], -1),
                              np.ones(images.shape[:-1]), np.ones(images.shape[:-1])), axis=-1),
                    np.full(len(images), 128))),
                patch.object(MODEL, "_ranked_blast_hits", return_value=hits) as rank_hits,
                patch.object(MODEL, "_blast_database_target_count", return_value=3),
            ):
                outputs = module.predict_shard(
                    manifest,
                    weights,
                    batch_size=4,
                    inference_steps=50,
                    seed=123,
                    output=output,
                )

            self.assertEqual(
                tuple(path.name for path in outputs), ("z-view-2.png", "a-view-1.png")
            )
            self.assertEqual([np.asarray(Image.open(path)).shape for path in outputs], [(512, 512)] * 2)
            self.assertEqual(float(np.asarray(Image.open(outputs[1])).max()), 128.0)
            rank_hits.assert_called_once()
            self.assertEqual(rank_hits.call_args.kwargs["target_count"], 3)

        predict_kwargs = call_log[1][1][1]
        images = call_log[1][1][0]
        self.assertEqual(images.shape, (2, 512, 512, 3))
        for actual, expected in zip(
            [float(images[0, 0, 0, channel]) for channel in range(3)],
            [-1, 1, 1],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(predict_kwargs["protein_names"], ("TUBB", "TUBB"))
        self.assertNotIn("cell_line_names", predict_kwargs)
    def test_help_describes_sequence_based_three_image_inputs(self) -> None:
        help_text = (
            load_participant_module("predict.py", "proticelli_runner").build_parser().format_help()
        )

        self.assertIn("fasta_file", help_text)
        self.assertIn("microtubules_image", help_text)
        self.assertNotIn("target_protein", help_text)
        self.assertNotIn("cell_line", help_text)
