from __future__ import annotations

import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dream_participant.cli.participant import _submit_anvil_bundle, build_parser
from dream_participant.config import participant_config_for_evaluation
from dream_participant.integrations.terra import AnvilIdentity
from dream_participant.submissions.bundle import BundleLimits, SubmissionBundle
from dream_participant.submissions.gcs_staging import (
    GcsStagingError,
    GcsSubmissionSources,
    stage_gcs_submission_files,
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
  runtime { docker: "ghcr.io/example/predict@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
}
""".encode()


class FakeBlob:
    def __init__(self, name: str, data: bytes, generation: int = 17) -> None:
        self.name = name
        self.data = data
        self.generation = generation
        self.size = len(data)
        self.downloads: list[tuple[int | None, int | None]] = []

    def reload(self) -> None:
        return None

    def download_to_file(
        self,
        output,
        *,
        if_generation_match: int | None = None,
    ) -> None:
        self.downloads.append((self.generation, if_generation_match))
        output.write(self.data)


class FakeBucket:
    def __init__(self, client: FakeStorageClient, name: str) -> None:
        self.client = client
        self.name = name

    def blob(self, name: str, generation: int | None = None) -> FakeBlob:
        return self.client.objects[self.name, name]


class FakeStorageClient:
    def __init__(
        self,
        objects: dict[tuple[str, str], FakeBlob],
        prefix_objects: list[FakeBlob] | None = None,
    ) -> None:
        self.objects = objects
        self.prefix_objects = prefix_objects or []
        for blob in self.prefix_objects:
            self.objects.setdefault(("source-bucket", blob.name), blob)

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self, name)

    def list_blobs(self, bucket: str, *, prefix: str) -> list[FakeBlob]:
        return self.prefix_objects


class GcsSubmissionTests(unittest.TestCase):
    def test_explicit_files_use_filenames_in_the_bundle_with_pinned_downloads(self) -> None:
        client = self._client()
        config = FakeBlob("context/nested/config.yaml", b"seed: 3\n", generation=29)
        tokens = FakeBlob("vocab/tokens.json", b'["a"]', generation=31)
        client.objects["source-bucket", config.name] = config
        client.objects["another-bucket", tokens.name] = tokens
        args = build_parser().parse_args([
            "submit-anvil", "--wdl", "gs://source-bucket/model/workflow.wdl",
            "--weights", "gs://source-bucket/model/weights.pt", "--files",
            "gs://source-bucket/context/nested/config.yaml", "gs://another-bucket/vocab/tokens.json",
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = self._stage(client, GcsSubmissionSources.parse(
                args.wdl, args.weights, args.files, recursive=args.recursive,
            ), root / "staged")
            bundle = SubmissionBundle.create(
                wdl=staged.wdl, weights=staged.weights, other_files=staged.other_files,
                synapse_user_id="123", anvil_email="person@example.org",
                anvil_subject_id="sam-subject",
            )
            with tarfile.open(bundle.write_archive(root / "submission.tar")) as archive:
                self.assertEqual(archive.extractfile("other/config.yaml").read(), b"seed: 3\n")
                self.assertEqual(archive.extractfile("other/tokens.json").read(), b'["a"]')
        self.assertEqual(config.downloads, [(29, 29)])
        self.assertEqual(tokens.downloads, [(31, 31)])

    def test_duplicate_file_names_fail_before_downloading(self) -> None:
        client = self._client()
        for name in ("first/config.yaml", "second/config.yaml"):
            client.objects["source-bucket", name] = FakeBlob(name, b"config")
        sources = GcsSubmissionSources.parse(
            "gs://source-bucket/model/workflow.wdl", "gs://source-bucket/model/weights.pt",
            ["gs://source-bucket/first/config.yaml", "gs://source-bucket/second/config.yaml"],
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "duplicate relative file paths"):
                self._stage(client, sources, Path(directory))
        self.assertTrue(all(not blob.downloads for blob in client.objects.values()))

    def test_multiple_recursive_directories_preserve_paths_and_reject_conflicts(self) -> None:
        client = self._client()
        files = [FakeBlob("first/nested/config.yaml", b"config"),
                 FakeBlob("second/vocab/tokens.json", b"tokens")]
        for blob in files:
            client.objects["source-bucket", blob.name] = blob
        client.list_blobs = lambda bucket, prefix: [
            blob for blob in files if blob.name.startswith(prefix)
        ]
        sources = GcsSubmissionSources.parse(
            "gs://source-bucket/model/workflow.wdl", "gs://source-bucket/model/weights.pt",
            ["gs://source-bucket/first", "gs://source-bucket/second/"], recursive=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            staged = self._stage(client, sources, Path(directory))
            self.assertEqual((staged.other_files / "nested/config.yaml").read_bytes(), b"config")
            self.assertEqual((staged.other_files / "vocab/tokens.json").read_bytes(), b"tokens")
        conflict = FakeBlob("second/nested/config.yaml", b"other")
        files.append(conflict)
        client.objects["source-bucket", conflict.name] = conflict
        for blob in client.objects.values():
            blob.downloads.clear()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "duplicate relative file paths"):
                self._stage(client, sources, Path(directory))
        self.assertTrue(all(not blob.downloads for blob in client.objects.values()))

    def test_recursive_requires_files_and_reserved_filenames_fail_before_download(self) -> None:
        with self.assertRaisesRegex(GcsStagingError, "--recursive requires --files"):
            GcsSubmissionSources.parse(
                "gs://source-bucket/model/workflow.wdl", "gs://source-bucket/model/weights.pt",
                recursive=True,
            )
        client = self._client()
        client.objects["source-bucket", "context/predictions"] = FakeBlob("context/predictions", b"bad")
        sources = GcsSubmissionSources.parse(
            "gs://source-bucket/model/workflow.wdl", "gs://source-bucket/model/weights.pt",
            ["gs://source-bucket/context/predictions"],
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "runtime path"):
                self._stage(client, sources, Path(directory))
        self.assertTrue(all(not blob.downloads for blob in client.objects.values()))

    def _sources(self, other: str | None = None) -> GcsSubmissionSources:
        return GcsSubmissionSources.parse(
            "gs://source-bucket/model/workflow.wdl",
            "gs://source-bucket/model/weights.pt",
            [other] if other else None,
            recursive=other is not None,
        )

    def _client(self, prefix_objects: list[FakeBlob] | None = None) -> FakeStorageClient:
        return FakeStorageClient(
            {
                ("source-bucket", "model/workflow.wdl"): FakeBlob("model/workflow.wdl", WDL),
                ("source-bucket", "model/weights.pt"): FakeBlob("model/weights.pt", b"weights"),
            },
            prefix_objects,
        )

    def _stage(
        self,
        client: FakeStorageClient,
        sources: GcsSubmissionSources,
        destination: Path,
        *,
        limits: BundleLimits = BundleLimits(),
    ):
        with patch(
            "dream_participant.submissions.gcs_staging.storage.Client", return_value=client
        ) as factory:
            staged = stage_gcs_submission_files(
                sources,
                destination,
                credentials="vm-credentials",
                project="vm-project",
                limits=limits,
            )
        factory.assert_called_once_with(credentials="vm-credentials", project="vm-project")
        return staged

    def test_gcs_object_and_prefix_paths_reject_traversal_and_ambiguous_paths(self) -> None:
        invalid_paths = (
            "../escape",
            "data/../escape",
            "data/%2e%2e/escape",
            "data//escape",
            "data/./escape",
            "/escape",
            r"data\escape",
        )
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(GcsStagingError):
                GcsSubmissionSources.parse(
                    f"gs://source-bucket/{path}",
                    "gs://source-bucket/model/weights.pt",
                    None,
                )

        with self.assertRaisesRegex(GcsStagingError, "not a prefix"):
            GcsSubmissionSources.parse(
                "gs://source-bucket/model/workflow.wdl",
                "gs://source-bucket/model/weights.pt",
                ["gs://source-bucket/context/"],
            )

    def test_empty_other_files_prefix_fails_before_download(self) -> None:
        client = self._client()
        sources = self._sources("gs://source-bucket/context/")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "contains no files"):
                self._stage(client, sources, Path(directory))
        self.assertEqual(client.objects["source-bucket", "model/workflow.wdl"].downloads, [])
        self.assertEqual(client.objects["source-bucket", "model/weights.pt"].downloads, [])

    def test_remote_prefix_path_attack_fails_before_any_download(self) -> None:
        attacked = FakeBlob("context/../escape", b"private")
        client = self._client([attacked])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "unsafe GCS object path"):
                self._stage(
                    client,
                    self._sources("gs://source-bucket/context/"),
                    Path(directory),
                )
        self.assertEqual(client.objects["source-bucket", "model/workflow.wdl"].downloads, [])

    def test_bundle_limits_fail_before_downloading_objects(self) -> None:
        client = self._client()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GcsStagingError, "member size limit"):
                self._stage(
                    client,
                    self._sources(),
                    Path(directory),
                    limits=BundleLimits(
                        max_members=10,
                        max_member_bytes=8,
                        max_total_bytes=100,
                        max_manifest_bytes=100,
                    ),
                )
        self.assertEqual(client.objects["source-bucket", "model/workflow.wdl"].downloads, [])
        self.assertEqual(client.objects["source-bucket", "model/weights.pt"].downloads, [])

    def test_staging_pins_each_download_to_the_listed_generation(self) -> None:
        config_blob = FakeBlob("context/nested/config.yaml", b"seed: 3\n", generation=29)
        client = self._client([config_blob])
        with tempfile.TemporaryDirectory() as directory:
            staged = self._stage(
                client,
                self._sources("gs://source-bucket/context/"),
                Path(directory),
            )
            self.assertEqual(
                (staged.other_files / "nested/config.yaml").read_bytes(),
                b"seed: 3\n",
            )
        self.assertEqual(
            client.objects["source-bucket", "model/workflow.wdl"].downloads,
            [(17, 17)],
        )
        self.assertEqual(
            client.objects["source-bucket", "model/weights.pt"].downloads,
            [(17, 17)],
        )
        self.assertEqual(config_blob.downloads, [(29, 29)])

    def test_staged_files_produce_the_same_deterministic_tar_as_local_inputs(self) -> None:
        config_blob = FakeBlob("context/nested/config.yaml", b"seed: 3\n")
        token_blob = FakeBlob("context/vocab/tokens.json", b'["a"]')
        client = self._client([config_blob, token_blob])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = self._stage(
                client,
                self._sources("gs://source-bucket/context/"),
                root / "staged",
            )
            local_wdl = root / "workflow.wdl"
            local_weights = root / "weights.pt"
            local_other = root / "other"
            local_wdl.write_bytes(WDL)
            local_weights.write_bytes(b"weights")
            (local_other / "nested").mkdir(parents=True)
            (local_other / "vocab").mkdir()
            (local_other / "nested" / "config.yaml").write_bytes(b"seed: 3\n")
            (local_other / "vocab" / "tokens.json").write_bytes(b'["a"]')

            local_bundle = SubmissionBundle.create(
                wdl=local_wdl,
                weights=local_weights,
                other_files=local_other,
                synapse_user_id="123",
                anvil_email="person@example.org",
                anvil_subject_id="sam-subject",
            )
            gcs_bundle = SubmissionBundle.create(
                wdl=staged.wdl,
                weights=staged.weights,
                other_files=staged.other_files,
                synapse_user_id="123",
                anvil_email="person@example.org",
                anvil_subject_id="sam-subject",
            )
            local_archive = local_bundle.write_archive(root / "local.tar")
            gcs_archive = gcs_bundle.write_archive(root / "gcs.tar")
            local_archive_bytes = local_archive.read_bytes()
            gcs_archive_bytes = gcs_archive.read_bytes()
            local_manifest = local_bundle.manifest.as_dict()
            gcs_manifest = gcs_bundle.manifest.as_dict()

        self.assertEqual(local_archive_bytes, gcs_archive_bytes)
        self.assertEqual(local_manifest, gcs_manifest)

    def test_submit_anvil_verifies_identity_before_gcs_and_reuses_bundle_flow(self) -> None:
        events: list[str] = []
        terra = Mock(credentials="vm-credentials", google_project="vm-project")
        terra.authenticated_anvil_identity_from_sam.side_effect = lambda: (
            events.append("identity"),
            AnvilIdentity("person@example.org", "sam-subject"),
        )[1]
        staged = SimpleNamespace(
            wdl=Path("/tmp/workflow.wdl"),
            weights=Path("/tmp/weights"),
            other_files=Path("/tmp/other"),
        )
        args = build_parser().parse_args(
            [
                "submit-anvil",
                "--wdl",
                "gs://source-bucket/model/workflow.wdl",
                "--weights",
                "gs://source-bucket/model/weights.pt",
                "--files",
                "gs://source-bucket/context/",
                "--recursive",
            ]
        )
        config = participant_config_for_evaluation(args.evaluation_id)
        bundle_flow = Mock(side_effect=lambda *args, **kwargs: (events.append("bundle"), 0)[1])
        stage = Mock(side_effect=lambda *args, **kwargs: (events.append("stage"), staged)[1])
        with (
            patch("dream_participant.cli.participant.ParticipantTerraClient", return_value=terra),
            patch("dream_participant.cli.participant.stage_gcs_submission_files", stage),
            patch("dream_participant.cli.participant._submit_bundle", bundle_flow),
            patch("dream_participant.cli.participant.progress"),
        ):
            self.assertEqual(_submit_anvil_bundle(config, args), 0)

        self.assertEqual(events, ["identity", "stage", "bundle"])
        terra.authenticated_anvil_identity_from_sam.assert_called_once_with()
        stage.assert_called_once_with(
            GcsSubmissionSources.parse(args.wdl, args.weights, args.files, recursive=args.recursive),
            unittest.mock.ANY,
            credentials="vm-credentials",
            project="vm-project",
        )
        self.assertEqual(
            bundle_flow.call_args.kwargs["identity"],
            AnvilIdentity("person@example.org", "sam-subject"),
        )
        self.assertEqual(bundle_flow.call_args.args[1].wdl, staged.wdl)

    def test_gcs_failure_stops_before_the_synapse_submission_flow(self) -> None:
        terra = Mock(credentials="vm-credentials", google_project="vm-project")
        terra.authenticated_anvil_identity_from_sam.return_value = AnvilIdentity(
            "person@example.org", "sam-subject"
        )
        args = build_parser().parse_args(
            [
                "submit-anvil",
                "--wdl",
                "gs://source-bucket/model/workflow.wdl",
                "--weights",
                "gs://source-bucket/model/weights.pt",
            ]
        )
        with (
            patch("dream_participant.cli.participant.ParticipantTerraClient", return_value=terra),
            patch(
                "dream_participant.cli.participant.stage_gcs_submission_files",
                side_effect=GcsStagingError("object not found"),
            ),
            patch("dream_participant.cli.participant._submit_bundle") as bundle_flow,
            patch("dream_participant.cli.participant.ParticipantSynapseGateway") as synapse,
            patch("dream_participant.cli.participant.progress"),
            self.assertRaisesRegex(SystemExit, "submission staging failed: object not found"),
        ):
            _submit_anvil_bundle(participant_config_for_evaluation("9620030"), args)

        bundle_flow.assert_not_called()
        synapse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
