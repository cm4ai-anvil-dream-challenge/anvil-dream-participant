import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from dream_participant.submissions.bundle import (
    ARCHIVE_MANIFEST,
    ARCHIVE_WEIGHTS,
    ARCHIVE_WDL,
    BundleValidationError,
    SubmissionBundleManifest,
    SubmissionBundle,
)


WDL = """version 1.0
struct PredictionInput {
  String prediction_id
  File microtubules_image
  File nucleus_image
  File er_image
  File fasta_file
}
task Predict {
  input {
    Array[PredictionInput] inputs
    File weights
  }
  command { echo ~{weights} }
  output { Array[File] predictions = [] }
  runtime { docker: \"ghcr.io/example/predict@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\" }
}
"""


class SubmissionBundleTests(unittest.TestCase):
    def _files(self, root: Path) -> tuple[Path, Path, Path]:
        wdl = root / "workflow.wdl"
        weights = root / "weights.pt"
        other = root / "other"
        wdl.write_text(WDL, encoding="utf-8")
        weights.write_bytes(b"weights")
        (other / "nested").mkdir(parents=True)
        (other / "nested" / "config.json").write_text("{}", encoding="utf-8")
        return wdl, weights, other

    def test_archive_is_deterministic_and_contains_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl, weights, other = self._files(root)
            first = SubmissionBundle.create(
                wdl=wdl,
                weights=weights,
                other_files=other,
                synapse_user_id="123",
                anvil_email="User@Example.org",
                anvil_subject_id="subject-1",
            )
            second = SubmissionBundle.create(
                wdl=wdl,
                weights=weights,
                other_files=other,
                synapse_user_id="123",
                anvil_email="user@example.org",
                anvil_subject_id="subject-1",
            )
            first_path = first.write_archive(root / "first.tar")
            second_path = second.write_archive(root / "second.tar")
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            with tarfile.open(first_path, mode="r:") as archive:
                self.assertEqual(
                    [item.name for item in archive.getmembers()],
                    [ARCHIVE_MANIFEST, ARCHIVE_WDL, ARCHIVE_WEIGHTS, "other/nested/config.json"],
                )
                manifest = json.loads(archive.extractfile(ARCHIVE_MANIFEST).read())
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["anvil_identity"]["email"], "user@example.org")
            self.assertEqual(manifest["wdl"]["path"], ARCHIVE_WDL)
            self.assertEqual(manifest["weights"]["path"], ARCHIVE_WEIGHTS)
            self.assertEqual(len(manifest["container_images"]), 1)
            for field in ("submission_name", "submission_team"):
                with self.subTest(field=field):
                    incomplete = dict(manifest)
                    del incomplete[field]
                    with self.assertRaisesRegex(BundleValidationError, rf"missing.*{field}"):
                        SubmissionBundleManifest.from_dict(incomplete)
            self.assertEqual(
                manifest["full_fingerprint"],
                hashlib.sha256(
                    json.dumps(
                        {
                            "core_fingerprint": manifest["core_fingerprint"],
                            "other_files": manifest["other_files"],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            )


    def test_optional_directory_can_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl, weights, _ = self._files(root)
            bundle = SubmissionBundle.create(
                wdl=wdl,
                weights=weights,
                other_files=None,
                synapse_user_id="123",
                anvil_email="user@example.org",
                anvil_subject_id="subject-1",
            )
            self.assertEqual(bundle.manifest.other_files, ())


    def test_rejects_other_files_that_conflict_with_runtime_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl, weights, other = self._files(root)
            (other / "predictions").mkdir()
            (other / "predictions" / "fake.png").write_bytes(b"fake")

            with self.assertRaisesRegex(BundleValidationError, "DREAM runtime path"):
                SubmissionBundle.create(
                    wdl=wdl,
                    weights=weights,
                    other_files=other,
                    synapse_user_id="123",
                    anvil_email="user@example.org",
                    anvil_subject_id="subject-1",
                )

    def test_rejects_symlink_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl, weights, other = self._files(root)
            (other / "link").symlink_to(weights)
            with self.assertRaisesRegex(BundleValidationError, "symbolic links"):
                SubmissionBundle.create(
                    wdl=wdl,
                    weights=weights,
                    other_files=other,
                    synapse_user_id="123",
                    anvil_email="user@example.org",
                    anvil_subject_id="subject-1",
                )

    def test_container_image_extraction_ignores_wdl_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl, weights, _ = self._files(root)
            wdl.write_text(
                wdl.read_text(encoding="utf-8")
                + "\n# docker: \"ghcr.io/example/ignored@sha256:"
                + "b" * 64
                + "\"\n",
                encoding="utf-8",
            )
            bundle = SubmissionBundle.create(
                wdl=wdl,
                weights=weights,
                other_files=None,
                synapse_user_id="123",
                anvil_email="user@example.org",
                anvil_subject_id="subject-1",
            )

            self.assertEqual(len(bundle.manifest.container_images), 1)


    def test_bundle_does_not_have_dockerfile_or_workspace_fields(self) -> None:
        fields = set(SubmissionBundleManifest.__dataclass_fields__)
        self.assertNotIn("dockerfile", fields)
        self.assertNotIn("workspace_namespace", fields)
        self.assertNotIn("workspace_bucket", fields)
