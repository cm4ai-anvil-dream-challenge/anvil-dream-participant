from __future__ import annotations

import json
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.integration.examples.proticelli_support import (
    load_participant_module,
    write_weights_zip,
)

MODEL = load_participant_module("proticelli_model.py", "proticelli_model")


class ProtiCelliModelResolutionTests(unittest.TestCase):
    def test_sequence_resolver_walks_past_unmapped_and_unsupported_hits(self) -> None:
        module = MODEL
        hits = (
            module.BlastHit("P00001", 100.0, 1e-20, 99.0, 0),
            module.BlastHit("P00002", 90.0, 1e-15, 75.0, 1),
            module.BlastHit("P00003", 80.0, 1e-10, 50.0, 2),
        )
        with (
            patch.object(module, "_ranked_blast_hits", return_value=hits) as ranked,
            patch.object(module, "_blast_database_target_count", return_value=3),
        ):
            selection = module.select_supported_protein(
                "MAAA",
                database="test-db",
                gene_map={"P00001": "", "P00002": "UNKNOWN", "P00003": "TUBB"},
                protein_vocabulary={"TUBB": 0},
                vocabulary_index={"tubb": "TUBB"},
            )

        self.assertEqual(selection.protein_name, "TUBB")
        self.assertEqual(selection.hit_rank, 3)
        self.assertEqual(selection.accession, "P00003")
        self.assertEqual(ranked.call_args.kwargs["target_count"], 3)

    def test_all_unrecognized_blast_hits_fail_explicitly(self) -> None:
        module = MODEL
        with (
            patch.object(
                module,
                "_ranked_blast_hits",
                return_value=(module.BlastHit("P99999", 10.0, 0.1, 30.0, 0),),
            ),
            patch.object(module, "_blast_database_target_count", return_value=1),
        ):
            with self.assertRaisesRegex(ValueError, "no hit supported"):
                module.select_supported_protein(
                    "MAAA",
                    database="test-db",
                    gene_map={"P99999": "UNKNOWN"},
                    protein_vocabulary={"TUBB": 0},
                )

    def test_nested_reference_zip_layout_and_path_traversal(self) -> None:
        module = MODEL
        with TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "weights.zip"
            write_weights_zip(weights)
            location = module.unpack_weights(weights, root / "task")
            self.assertTrue(module._model_directories(location.model_root))
            self.assertTrue(
                module._blast_database(location.search_roots).name.endswith(
                    "human_reviewed_swissprot"
                )
            )
            self.assertEqual(
                module._gene_map_path(location.search_roots).name, "human_gene_map.tsv"
            )

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../escape", "no")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                module.unpack_weights(unsafe, root / "unsafe-task")

    def test_database_sequence_count_parses_and_caches_blastdbcmd_info(self) -> None:
        module = MODEL
        output = (
            "Database: reviewed\n"
            "\t12,345 sequences; 9,000 total residues\n"
            "Database: alternate\n"
            "\t2 sequences; 28 total residues\n"
        )
        completed = SimpleNamespace(stdout=output, stderr="")
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            count = module._blast_database_target_count("/tmp/cache-test-db")
            second = module._blast_database_target_count("/tmp/cache-test-db")

        self.assertEqual(count, 12_347)
        self.assertEqual(second, 12_347)
        run.assert_called_once()

    def test_manifest_rejects_missing_fasta_or_extra_target_fields(self) -> None:
        module = MODEL
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "inputs.json"
            manifest.write_text(json.dumps([{"prediction_id": "one", "image": "x"}]))
            with self.assertRaisesRegex(ValueError, "must contain exactly"):
                module.read_inputs_manifest(manifest)
