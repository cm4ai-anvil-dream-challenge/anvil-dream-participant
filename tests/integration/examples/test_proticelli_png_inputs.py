from __future__ import annotations

import json
import hashlib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from tests.integration.examples.proticelli_support import load_participant_module


MODEL = load_participant_module("proticelli_model.py", "proticelli_model")
IMAGES = load_participant_module("proticelli_images.py", "proticelli_images")


class ProtiCelliPngInputsTests(unittest.TestCase):
    def test_normalization_retains_robust_reference_and_empty_channel_fallback(self):
        from unittest.mock import MagicMock

        raw = np.zeros((3, 512, 512, 3), dtype=np.uint8)
        raw[0, ..., 1] = 100
        raw[0, 0, 0, 1] = 255
        raw[1, ..., 0] = 80
        normalized = np.full((3, 512, 512, 4), -1, dtype=np.float32)
        normalized[..., 2] = 1
        normalizer = MagicMock(percentile=99.95)
        normalizer.transform.return_value = normalized
        factory = MagicMock(return_value=normalizer)
        with patch.dict(sys.modules, {
            "proticelli.data.preprocessing": SimpleNamespace(ImageNormalizer=factory),
        }):
            images, scales = IMAGES.normalize_inputs(raw)
        factory.assert_called_once_with(bit_depth=8)
        stack = normalizer.transform.call_args.args[0]
        np.testing.assert_array_equal(stack[..., [0, 2, 3]], raw)
        self.assertTrue(np.all(stack[..., 1] == 0))
        np.testing.assert_array_equal(scales, [100, 80, 0])
        np.testing.assert_array_equal(images[0, 0, 0], [-1, 1, -1])

    def test_uint8_export_restores_nucleus_scale_and_preserves_provenance(self):
        selection = SimpleNamespace(
            protein_name="TUBB", accession="P00001", hit_rank=0, bitscore=100,
            evalue=1e-20, percent_identity=99,
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pixels = np.zeros((512, 512), dtype=np.float32)
            pixels[0, :8] = [-0.1, 0, 0.5, 1.0, 1.3, 1.4, 0.506, 3.0]
            output = IMAGES._write_prediction(root, "136", pixels, selection, "MAAA", 100)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.mode, "L")
                actual = np.asarray(image)
            self.assertEqual(actual.dtype, np.dtype("uint8"))
            np.testing.assert_array_equal(actual[0, :8], [0, 0, 50, 100, 130, 140, 51, 255])
            with Image.open(output) as image:
                self.assertEqual(json.loads(image.info["provenance"])["prediction_id"], "136")
            pixels[0, 0] = np.nan
            with self.assertRaisesRegex(ValueError, "invalid prediction"):
                IMAGES._write_prediction(root, "bad", pixels, selection, "MAAA", 100)
            self.assertFalse((root / "bad.png").exists())

    def test_prediction_selects_the_strongest_supported_sequence_across_isoforms(self):
        runner = load_participant_module("predict.py", "proticelli_runner")
        selected_proteins = []

        class Model:
            protein_map = {"TUBB": 0, "CCDC50": 1}

            def __init__(self, **kwargs):
                pass

            def predict(self, images, **kwargs):
                selected_proteins.extend(kwargs["protein_names"])
                return np.ones((len(images), 512, 512), dtype=np.float32)

        hits = {
            "MAAA": (MODEL.BlastHit("P00000", 500, 1e-40, 100, 0),),
            "MCCC": (MODEL.BlastHit("P00001", 10, 1e-10, 90, 0),),
            "MDDD": (MODEL.BlastHit("P00002", 100, 1e-20, 99, 0),),
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            png = root / "input.png"
            Image.fromarray(np.full((512, 512), 255, dtype=np.uint8)).save(png)
            fasta = root / "136.faa"
            fasta.write_text(">unsupported\nMAAA\n>weak\nMCCC\n>strong\nMDDD\n")
            manifest = root / "inputs.json"
            manifest.write_text(json.dumps([{
                "prediction_id": "136", "microtubules_image": str(png),
                "nucleus_image": str(png), "er_image": str(png),
                "fasta_file": str(fasta), "mask_image": str(png),
            }]))
            with (
                patch.dict(sys.modules, {"proticelli": SimpleNamespace(Model=Model)}),
                patch.object(IMAGES, "normalize_inputs", side_effect=lambda images: (
                    images.astype(np.float32) / 127.5 - 1, np.full(len(images), 255))),
                patch.object(MODEL, "unpack_weights", return_value=SimpleNamespace(
                    model_root=root, search_roots=(root,))),
                patch.object(MODEL, "_model_directories", return_value=(root, root)),
                patch.object(MODEL, "_blast_database", return_value=root / "db"),
                patch.object(MODEL, "_gene_map_path", return_value=root / "map.tsv"),
                patch.object(MODEL, "load_gene_map", return_value={
                    "P00000": "UNKNOWN", "P00001": "CCDC50", "P00002": "TUBB"}),
                patch.object(MODEL, "_ranked_blast_hits", side_effect=lambda sequence, *a, **k: hits[sequence]),
                patch.object(MODEL, "_blast_database_target_count", return_value=3),
                redirect_stdout(StringIO()),
            ):
                outputs = runner.predict_shard(manifest, root / "weights.tar", output=root / "predictions")
            self.assertEqual(selected_proteins, ["TUBB"])
            self.assertEqual(outputs[0].name, "136.png")
            with Image.open(outputs[0]) as image:
                provenance = json.loads(image.info["provenance"])
            self.assertEqual(provenance["sequence_sha256"], hashlib.sha256(b"MDDD").hexdigest())
            self.assertEqual(provenance["protein_name"], "TUBB")

    def test_localized_png_and_mask_inputs_load_in_channel_order(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            record = {"prediction_id": "136"}
            for role, value in (("microtubules_image", 255), ("nucleus_image", 0),
                                ("er_image", 128), ("mask_image", 255)):
                path = root / f"136_{role}.png"
                Image.fromarray(np.full((512, 512), value, dtype=np.uint8)).save(path)
                record[role] = str(path)
            fasta = root / "136.faa"
            fasta.write_text(">protein\nMAAA\n", encoding="ascii")
            record["fasta_file"] = str(fasta)
            manifest = root / "inputs.json"
            manifest.write_text(json.dumps([record]))
            inputs = MODEL.read_inputs_manifest(manifest)
            MODEL.validate_inputs(inputs)
            self.assertEqual(inputs[0].mask_image, root / "136_mask_image.png")
            selections = {"MAAA": MODEL.ProteinSelection("TUBB", "P00001", 1, 100, 1e-20, 99)}
            batch = next(IMAGES.iter_image_batches(inputs, {fasta: "MAAA"}, selections))
            self.assertEqual(batch.images.shape, (1, 512, 512, 3))
            np.testing.assert_array_equal(batch.images[0, 0, 0],
                                          np.array([255, 0, 128], dtype=np.uint8))
            self.assertEqual(batch.inputs[0].mask_image, root / "136_mask_image.png")

    def test_invalid_mask_is_rejected_before_inference(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "mask.png"
            Image.fromarray(np.zeros((512, 512, 3), dtype=np.uint8)).save(path)
            with self.assertRaisesRegex(ValueError, "grayscale"):
                IMAGES._read_image(path)


if __name__ == "__main__":
    unittest.main()
