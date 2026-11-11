from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import pickle
import sys
import tempfile
import types
import unittest
import zipfile
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch


BUILDER = Path(__file__).resolve().parents[3] / (
    "examples/proticelli/scripts/build_weights.py"
)
if importlib.util.find_spec("tqdm") is None:

    class _Progress:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> _Progress:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def update(self, _: int) -> None:
            pass

    sys.modules["tqdm"] = types.SimpleNamespace(tqdm=_Progress)
spec = importlib.util.spec_from_file_location("weights_builder", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

RELEASE_URL = (
    "https://ftp.uniprot.org/pub/databases/uniprot/previous_releases/"
    "release-2026_02/knowledgebase/taxonomic_divisions/uniprot_sprot_human.dat.gz"
)


class Response(io.BytesIO):
    headers: dict[str, str] = {"Last-Modified": "test-date"}

    def read1(self, size: int) -> bytes:
        return self.read(size)


class InterruptedResponse(Response):
    def read1(self, size: int) -> bytes:
        if self.tell() == 0:
            return self.read(4)
        raise IncompleteRead(b"partial")


class WeightsDownloadTests(unittest.TestCase):
    def test_interrupted_http_transfer_retries_and_discards_partial_data(self) -> None:
        payload = b">sp|P55263|ADK_HUMAN\nAAAA\n"
        compressed = gzip.compress(payload, mtime=0)
        expected_sha256 = hashlib.sha256(compressed).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    builder,
                    "urlopen",
                    side_effect=[InterruptedResponse(b"partial transfer"), Response(compressed)],
                ) as request,
                patch.object(builder.time, "sleep"),
            ):
                path, provenance = builder.download_reference(
                    Path(directory), RELEASE_URL, expected_sha256
                )
            self.assertEqual(request.call_count, 2)
            self.assertEqual(gzip.decompress(path.read_bytes()), payload)
            self.assertEqual(
                provenance,
                {"kind": "https_url", "url": RELEASE_URL, "sha256": expected_sha256},
            )
            self.assertNotIn("last_modified", provenance)

    def test_source_hash_mismatch_is_rejected_without_retries(self) -> None:
        compressed = gzip.compress(b"reference", mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.dat.gz"
            source.write_bytes(compressed)
            staged = Path(directory) / builder.REFERENCE_FILENAME
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                builder.download_reference(Path(directory), str(source), expected_sha256="0" * 64)
            self.assertFalse(staged.exists())
            self.assertEqual(source.read_bytes(), compressed)

    def test_mutable_or_unversioned_reference_urls_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutable"):
            builder._source_url(
                "https://ftp.uniprot.org/pub/databases/uniprot/current_release/"
                "knowledgebase/taxonomic_divisions/uniprot_sprot_human.dat.gz"
            )
        with self.assertRaisesRegex(ValueError, "release identifier"):
            builder._source_url("https://example.org/uniprot_sprot_human.dat.gz")

    def test_invalid_pinned_gzip_is_rejected_and_removed(self) -> None:
        compressed = gzip.compress(b">sp|P55263|ADK_HUMAN\nAAAA\n", mtime=0)[:-8]
        expected_sha256 = hashlib.sha256(compressed).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(builder, "urlopen", return_value=Response(compressed)) as request:
                with self.assertRaisesRegex(ValueError, "not a valid gzip"):
                    builder.download_reference(Path(directory), RELEASE_URL, expected_sha256)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_retry_exhaustion_leaves_no_partial_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(
                    builder,
                    "urlopen",
                    side_effect=[InterruptedResponse(b"partial transfer") for _ in range(3)],
                ) as request,
                patch.object(builder.time, "sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts"):
                    builder.download_reference(root, RELEASE_URL)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(list(root.iterdir()), [])


class SwissProtExtractionTests(unittest.TestCase):
    def test_only_supported_reviewed_human_names_supply_sequences_and_gene_map(self) -> None:
        reviewed = """ID   ADK_HUMAN Reviewed; 4 AA.
AC   P55263; Q00001;
GN   Name=ADK {ECO:0001}; Synonyms=OTHER;
OX   NCBI_TaxID=9606;
DR   Ensembl; ENST0001; ENSP0001; ENSG00000156110.1;
SQ   SEQUENCE   4 AA; 0 MW; CHECKSUM CRC64;
     AAAA
//
"""
        comma_alias = (
            reviewed.replace("ADK_HUMAN", "ALIAS_HUMAN")
            .replace("P55263", "P55264")
            .replace("Name=ADK", "Name=ALIAS")
        )
        unsupported = (
            reviewed.replace("ADK_HUMAN", "OTHER_HUMAN")
            .replace("P55263", "P55265")
            .replace("Name=ADK", "Name=UNSUPPORTED")
        )
        mouse = reviewed.replace("P55263", "P00002").replace("9606", "10090")
        unreviewed = reviewed.replace("P55263", "P00003").replace("Reviewed;", "Unreviewed;")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gz"
            source.write_bytes(
                gzip.compress((reviewed + comma_alias + unsupported + mouse + unreviewed).encode())
            )
            fasta, mapping = root / "reference.fasta", root / "mapping.tsv"
            vocabulary = builder.supported_gene_names({"ADK": 0, "OTHER, ALIAS": 1})
            reviewed_count, selected_count = builder.extract_human_references(
                source, fasta, mapping, vocabulary
            )
            self.assertEqual((reviewed_count, selected_count), (3, 2))
            self.assertEqual(
                fasta.read_text(),
                ">sp|P55263|ADK_HUMAN\nAAAA\n>sp|P55264|ALIAS_HUMAN\nAAAA\n",
            )
            self.assertEqual(
                mapping.read_text().splitlines(),
                [
                    "accession\thgnc_symbol\tensembl_gene_ids",
                    "P55263\tADK\tENSG00000156110",
                    "P55264\tALIAS\tENSG00000156110",
                ],
            )

    def test_vocabulary_resource_identity_is_stable_and_shape_is_validated(self) -> None:
        payload = pickle.dumps({"ADK": 0, "OTHER, ALIAS": 1}, protocol=pickle.HIGHEST_PROTOCOL)
        vocabulary_file = Mock()
        vocabulary_file.read_bytes.return_value = payload
        vocabulary_package = Mock()
        vocabulary_package.joinpath.return_value = vocabulary_file
        with patch.object(builder.resources, "files", return_value=vocabulary_package) as files:
            index, identity = builder.load_supported_vocabulary()
        files.assert_called_once_with("proticelli.data")
        vocabulary_package.joinpath.assert_called_once_with("antibody_map.pkl")
        self.assertIn("alias", index)
        self.assertIn("other, alias", index)
        self.assertEqual(
            identity,
            {
                "resource": "proticelli.data/antibody_map.pkl",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "protein_count": 2,
            },
        )

        invalid_file = Mock()
        invalid_file.read_bytes.return_value = pickle.dumps(["ADK"])
        invalid_package = Mock()
        invalid_package.joinpath.return_value = invalid_file
        with patch.object(builder.resources, "files", return_value=invalid_package):
            with self.assertRaisesRegex(ValueError, "Invalid ProtiCelli protein vocabulary"):
                builder.load_supported_vocabulary()

    def test_incomplete_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gz"
            source.write_bytes(gzip.compress(b"ID   ADK_HUMAN Reviewed; 4 AA.\n"))
            with self.assertRaisesRegex(ValueError, "Truncated Swiss-Prot record"):
                builder.extract_human_references(source, root / "fasta", root / "map", {})


class DeterministicWeightsTests(unittest.TestCase):
    @staticmethod
    def _synthetic_uniprot(path: Path, count: int = 1000) -> bytes:
        records = []
        for index in range(count):
            records.append(
                f"ID   ENTRY{index:05d}_HUMAN Reviewed; 4 AA.\n"
                f"AC   P{index:05d};\n"
                f"GN   Name=GENE{index:05d};\n"
                "OX   NCBI_TaxID=9606;\n"
                "SQ   SEQUENCE   4 AA; 0 MW; CHECKSUM CRC64;\n"
                "     AAAA\n//\n"
            )
        compressed = gzip.compress("".join(records).encode(), mtime=0)
        path.write_bytes(compressed)
        return compressed

    @staticmethod
    def _write_model(model: Path) -> list[Path]:
        files = []
        for folder in builder.MODEL_FOLDERS:
            root = model / folder
            root.mkdir(parents=True)
            for name, contents in (
                ("config.json", b"{}\n"),
                ("diffusion_pytorch_model.safetensors", b"synthetic weights"),
            ):
                path = root / name
                path.write_bytes(contents)
                files.append(path)
        return files

    def test_full_build_bytes_ignore_source_mtime_model_mtime_and_blast_creation_time(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model"
            model.mkdir()
            model_files = self._write_model(model)
            (model / "checkpoint/unet/optimizer.bin").write_bytes(b"do not package training state")
            source = root / "reference.dat.gz"
            compressed = self._synthetic_uniprot(source)
            source_sha256 = hashlib.sha256(compressed).hexdigest()
            blast_bin = root / "blast-bin"
            blast_bin.mkdir()
            for name in ("makeblastdb", "blastdbcmd", "blastp"):
                (blast_bin / name).write_text("")

            creation_dates = iter(("2026-09-30T23:59:00", "2026-10-01T00:01:00"))
            vocabulary_payload = pickle.dumps(
                {"GENE00000, GENE00001": 0}, protocol=pickle.HIGHEST_PROTOCOL
            )
            vocabulary_file = types.SimpleNamespace(read_bytes=lambda: vocabulary_payload)
            vocabulary_package = types.SimpleNamespace(joinpath=lambda name: vocabulary_file)

            def fake_run(command: list[str], check: bool = False):
                if command[0].endswith("makeblastdb"):
                    fasta = Path(command[command.index("-in") + 1]).read_text()
                    self.assertEqual(fasta.count(">"), 2)
                    database = Path(command[command.index("-out") + 1])
                    database.with_suffix(".pdb").write_bytes(b"deterministic blast database")
                    metadata = {
                        "version": "1.2",
                        "dbname": database.name,
                        "dbtype": "Protein",
                        "db-version": 5,
                        "description": builder.BLAST_TITLE,
                        "number-of-letters": 8,
                        "number-of-sequences": 2,
                        "last-updated": next(creation_dates),
                        "number-of-volumes": 1,
                        "files": [f"{database.name}.pdb"],
                    }
                    database.with_suffix(".pjs").write_text(json.dumps(metadata))
                    self.assertIn("-blastdb_version", command)
                    self.assertEqual(command[command.index("-blastdb_version") + 1], "5")
                return None

            def fake_check_output(command: list[str], text: bool = False) -> str:
                self.assertTrue(command[0].endswith("blastp"))
                return "blastp: 2.17.0+\n"

            outputs = [root / "first.zip", root / "second.zip"]
            with (
                patch.object(builder, "BLAST_BIN", blast_bin),
                patch.object(builder, "REFERENCE_SHA256", source_sha256),
                patch.object(builder.resources, "files", return_value=vocabulary_package),
                patch.object(builder.subprocess, "run", side_effect=fake_run),
                patch.object(builder.subprocess, "check_output", side_effect=fake_check_output),
            ):
                for output, mtime in zip(outputs, (1_700_000_000, 1_800_000_000)):
                    for path in model_files:
                        os.utime(path, (mtime, mtime))
                    os.utime(source, (mtime, mtime))
                    with patch.object(
                        sys,
                        "argv",
                        [
                            str(BUILDER),
                            "--model-dir",
                            str(model),
                            "--output",
                            str(output),
                            "--reference-source",
                            str(source),
                        ],
                    ):
                        builder.main()

            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            with zipfile.ZipFile(outputs[0]) as archive:
                self.assertTrue(
                    all(info.date_time == builder.ZIP_TIMESTAMP for info in archive.infolist())
                )
                self.assertNotIn("checkpoint/unet/optimizer.bin", archive.namelist())
                blast_zip_bytes = archive.read("reference/human_blast_db.zip")
                map_zip_bytes = archive.read("reference/human_gene_map.zip")
            with zipfile.ZipFile(io.BytesIO(blast_zip_bytes)) as archive:
                metadata = json.loads(archive.read("human_reviewed_swissprot.pjs"))
                self.assertNotIn("last-updated", metadata)
                provenance = json.loads(archive.read("PROVENANCE.json"))
                self.assertEqual(provenance["total_reviewed_human_records"], 1000)
                self.assertEqual(provenance["selected_accession_count"], 2)
                self.assertEqual(
                    provenance["vocabulary"],
                    {
                        "resource": "proticelli.data/antibody_map.pkl",
                        "sha256": hashlib.sha256(vocabulary_payload).hexdigest(),
                        "protein_count": 1,
                    },
                )
            with zipfile.ZipFile(io.BytesIO(map_zip_bytes)) as archive:
                mapping = archive.read("human_gene_map.tsv").decode().splitlines()
                self.assertEqual(len(mapping), 3)
                self.assertTrue(all("UNSUPPORTED" not in line for line in mapping))

    def test_build_fails_when_no_reviewed_human_name_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reference.dat.gz"
            DeterministicWeightsTests._synthetic_uniprot(source)
            vocabulary_payload = pickle.dumps({"OTHER": 0}, protocol=pickle.HIGHEST_PROTOCOL)
            vocabulary_file = types.SimpleNamespace(read_bytes=lambda: vocabulary_payload)
            vocabulary_package = types.SimpleNamespace(joinpath=lambda name: vocabulary_file)
            with (
                patch.object(builder, "download_reference", return_value=(source, {})),
                patch.object(builder.resources, "files", return_value=vocabulary_package),
            ):
                with self.assertRaisesRegex(ValueError, "No reviewed human UniProt records match"):
                    builder.build_references(root, str(source))

    def test_command_line_requires_an_explicit_reference_source(self) -> None:
        with patch.object(
            sys, "argv", [str(BUILDER), "--model-dir", "/model", "--output", "/weights.zip"]
        ):
            with self.assertRaises(SystemExit) as error:
                builder.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
