import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


from dream_participant.cli.participant import _submit_bundle, build_parser
from dream_participant.config import (
    ParticipantConfig,
    ParticipantSynapseConfig,
    ParticipantTerraConfig,
)
from dream_participant.integrations.terra import AnvilIdentity
from dream_participant.integrations.synapse_readiness import (
    ChallengeAccessState,
    ChallengeReadiness,
    ChallengeRoute,
    TeamMembershipSnapshot,
)
from dream_participant.integrations.synapse_upload import ParticipantSubmissionResult


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

IMAGE = "ghcr.io/example/participant-model@sha256:" + "b" * 64


def participant_wdl(image: str = IMAGE) -> str:
    return WDL.replace(
        "echo ~{weights}",
        'python3 /model/predict.py --inputs-manifest "~{write_json(inputs)}" '
        '--weights "~{weights}" --output predictions',
    ).replace(
        "ghcr.io/example/predict@sha256:" + "a" * 64,
        image,
    )


class DirectSubmissionTests(unittest.TestCase):
    def test_participant_config_contains_no_workspace_grant_fields(self) -> None:
        config_fields = set(ParticipantTerraConfig.__dataclass_fields__)
        self.assertEqual(config_fields, {"sam_url"})
        self.assertNotIn("membership_request_id", ParticipantSynapseConfig.__dataclass_fields__)
        self.assertNotIn("organizer_synapse_user_id", ParticipantSynapseConfig.__dataclass_fields__)

    def test_parser_removes_registration_and_workspace_arguments(self) -> None:
        parser = build_parser("anvil-dream-participant")
        with self.assertRaises(SystemExit):
            parser.parse_args(["register"])
        args = parser.parse_args(
            [
                "submit",
                "--wdl",
                "workflow.wdl",
                "--weights",
                "weights.pt",
                "--anvil-email",
                "user@example.org",
            ]
        )
        self.assertEqual(args.other_files, None)
        self.assertFalse(hasattr(args, "dockerfile"))
        self.assertFalse(hasattr(args, "workspace"))

    def test_public_image_is_validated_before_synapse_upload(self) -> None:
        route = ChallengeRoute("1", "syn123", "challenge", "7", "team", True)
        readiness = ChallengeReadiness(
            route,
            "123",
            "participant",
            True,
            True,
                    TeamMembershipSnapshot(True, True, False, False, False, True),
            ChallengeAccessState.READY,
            "ready",
        )
        config = ParticipantConfig(
            synapse=ParticipantSynapseConfig("queue", evaluation_id="1"),
            terra=ParticipantTerraConfig("https://sam.example.org"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl = root / "workflow.wdl"
            weights = root / "weights.pt"
            wdl.write_text(participant_wdl(), encoding="utf-8")
            weights.write_bytes(b"weights")
            args = argparse.Namespace(
                wdl=wdl,
                weights=weights,
                other_files=None,
                anvil_email="user@example.org",
                name=None,
                team=None,
            )
            events: list[str] = []
            validated_images: list[str] = []
            terra = Mock()
            terra.authenticated_anvil_identity.return_value = AnvilIdentity(
                "user@example.org", "subject"
            )
            gateway = Mock()
            gateway.setup_private_submission_project.return_value = (
                {"id": "syn456"},
                Mock(),
            )
            gateway.preflight_submission.return_value = readiness
            gateway.upload_and_submit.side_effect = lambda *args: (
                events.append("upload"),
                ParticipantSubmissionResult(
                    submission=SimpleNamespace(id="55", entityId="syn789"),
                    reused=True,
                ),
            )[1]
            output = io.StringIO()
            progress_mock = Mock()

            def verify_image(image: str) -> None:
                events.append("image")
                validated_images.append(image)

            with (
                patch(
                    "dream_participant.cli.participant.ParticipantTerraClient", return_value=terra
                ),
                patch(
                    "dream_participant.cli.participant.ParticipantSynapseGateway",
                    return_value=gateway,
                ),
                patch(
                    "dream_participant.cli.participant.verify_public_container_image",
                    side_effect=verify_image,
                ),
                patch("dream_participant.cli.participant.progress", progress_mock),
                redirect_stdout(output),
            ):
                self.assertEqual(_submit_bundle(config, args), 0)
            self.assertEqual(events, ["image", "upload"])
            self.assertEqual(validated_images, [IMAGE])
            submission_output = json.loads(output.getvalue())
            self.assertEqual(submission_output["submission_id"], "55")
            self.assertTrue(submission_output["tar_reused"])
            self.assertIn(
                "Reused the existing private submission TAR",
                [call.args[0] for call in progress_mock.call_args_list],
            )
            gateway.setup_private_submission_project.assert_called_once_with()
            gateway.preflight_submission.assert_called_once_with("syn456", None)

    def test_explicit_team_flows_through_preflight_and_queue_submission(self) -> None:
        identity = AnvilIdentity("user@example.org", "subject")
        config = ParticipantConfig(
            synapse=ParticipantSynapseConfig(
                "queue", submission_parent_id="synParent", evaluation_id="1"
            ),
            terra=ParticipantTerraConfig("https://sam.example.org"),
        )
        args = argparse.Namespace(
            wdl=Path("workflow.wdl"),
            weights=Path("weights.pt"),
            other_files=None,
            anvil_email=identity.email,
            name=None,
            team="submission-team-1",
        )
        bundle = SimpleNamespace(
            manifest=SimpleNamespace(
                container_images=(),
                canonical_json=Mock(return_value=b"{}"),
                core_fingerprint="c" * 64,
                full_fingerprint="f" * 64,
            ),
            write_archive=Mock(side_effect=lambda path: Path(path).write_bytes(b"bundle")),
        )
        gateway = Mock()
        gateway.preflight_submission.return_value = SimpleNamespace(submitter_id="123")
        gateway.upload_and_submit.return_value = ParticipantSubmissionResult(
            submission=SimpleNamespace(id="55", entityId="syn789"),
            reused=False,
        )

        with (
            patch(
                "dream_participant.cli.participant.ParticipantSynapseGateway", return_value=gateway
            ),
            patch(
                "dream_participant.cli.participant.SubmissionBundle.create", return_value=bundle
            ) as create,
            patch("dream_participant.cli.participant.parse_participant_workflow"),
            patch("dream_participant.cli.participant.verify_public_container_image"),
            patch("dream_participant.cli.participant.progress"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(_submit_bundle(config, args, identity=identity), 0)

        gateway.preflight_submission.assert_called_once_with("synParent", "submission-team-1")
        gateway.upload_and_submit.assert_called_once()
        self.assertEqual(
            gateway.upload_and_submit.call_args.args[1:],
            ("synParent", "participant-prediction", "submission-team-1"),
        )
        self.assertEqual(create.call_args.kwargs["submission_team"], "submission-team-1")

    def test_verified_vm_identity_skips_local_google_userinfo(self) -> None:
        route = ChallengeRoute("1", "syn123", "challenge", "7", "team", True)
        readiness = ChallengeReadiness(
            route,
            "123",
            "participant",
            True,
            True,
                    TeamMembershipSnapshot(True, True, False, False, False, True),
            ChallengeAccessState.READY,
            "ready",
        )
        config = ParticipantConfig(
            synapse=ParticipantSynapseConfig("queue", evaluation_id="1"),
            terra=ParticipantTerraConfig("https://sam.example.org"),
        )
        identity = AnvilIdentity("person@example.org", "sam-subject")
        bundle = SimpleNamespace(
            manifest=SimpleNamespace(
                container_images=(),
                core_fingerprint="c" * 64,
                full_fingerprint="f" * 64,
                canonical_json=Mock(return_value=b"{}"),
            ),
            write_archive=Mock(side_effect=lambda path: Path(path).write_bytes(b"bundle")),
        )
        gateway = Mock()
        gateway.setup_private_submission_project.return_value = ({"id": "syn456"}, Mock())
        gateway.preflight_submission.return_value = readiness
        gateway.upload_and_submit.return_value = ParticipantSubmissionResult(
            submission=SimpleNamespace(id="55", entityId="syn789"),
            reused=False,
        )
        args = argparse.Namespace(
            wdl=Path("workflow.wdl"),
            weights=Path("weights.pt"),
            other_files=None,
            anvil_email=identity.email,
            name=None,
            team=None,
        )
        with (
            patch("dream_participant.cli.participant.ParticipantTerraClient") as terra,
            patch(
                "dream_participant.cli.participant.ParticipantSynapseGateway", return_value=gateway
            ),
            patch(
                "dream_participant.cli.participant.SubmissionBundle.create", return_value=bundle
            ) as create,
            patch("dream_participant.cli.participant.parse_participant_workflow"),
            patch("dream_participant.cli.participant.progress"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(_submit_bundle(config, args, identity=identity), 0)

        terra.assert_not_called()
        self.assertEqual(create.call_args.kwargs["anvil_email"], identity.email)
        self.assertEqual(create.call_args.kwargs["anvil_subject_id"], identity.subject_id)
        gateway.upload_and_submit.assert_called_once()


    def test_hardware_runtime_is_rejected_before_cloud_access(self) -> None:
        config = ParticipantConfig(
            synapse=ParticipantSynapseConfig("queue", evaluation_id="1"),
            terra=ParticipantTerraConfig("https://sam.example.org"),
        )
        with tempfile.TemporaryDirectory() as directory:
            wdl = Path(directory) / "workflow.wdl"
            wdl.write_text(
                WDL.replace("runtime { docker:", "runtime { cpu: 8\n    docker:"),
                encoding="utf-8",
            )
            args = SimpleNamespace(wdl=wdl)
            with (
                patch("dream_participant.cli.participant.ParticipantTerraClient") as terra,
                patch("dream_participant.cli.participant.ParticipantSynapseGateway") as synapse,
            ):
                with self.assertRaisesRegex(SystemExit, "unsupported cpu"):
                    _submit_bundle(config, args)

            terra.assert_not_called()
            synapse.assert_not_called()
