from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anvil_dream_participant.challenge import ChallengeProfile
from anvil_dream_participant.config import participant_config_for_evaluation
from anvil_dream_participant.integrations.synapse_readiness import (
    ChallengeAccessState,
    ChallengeReadiness,
)
from anvil_dream_participant.integrations.terra import AnvilIdentity
from anvil_dream_participant.submissions.operations import SubmissionRequest, submit_bundle
from anvil_dream_participant.workflows.wdl import WorkflowContractError


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
"""

IMAGE = "ghcr.io/example/participant-model@sha256:" + "b" * 64


def _profile(validate_workflow=lambda _path: None) -> ChallengeProfile:
    return ChallengeProfile(
        name="Fixture",
        evaluation_id="1",
        queue_name="Fixture queue",
        validate_workflow=validate_workflow,
    )


def _readiness() -> ChallengeReadiness:
    route = {
        "evaluation_id": "1",
        "content_source_id": "syn123",
        "challenge_id": "challenge",
        "participant_team_id": "7",
        "participant_team_name": "team",
        "participant_team_accepts_requests": True,
    }
    return ChallengeReadiness(
        route,
        "123",
        True,
        {
            "is_member": True,
            "is_registered": True,
            "has_open_invitation": False,
            "has_open_request": False,
            "has_unmet_access_requirement": False,
            "team_accepts_requests": True,
        },
        ChallengeAccessState["READY"],
        "ready",
    )


class DirectSubmissionTests(unittest.TestCase):
    def test_participant_config_contains_only_participant_fields(self) -> None:
        config = participant_config_for_evaluation("1", queue_name="queue")
        self.assertEqual(set(config), {"synapse", "terra"})
        self.assertEqual(set(config["terra"]), {"sam_url"})
        self.assertNotIn("membership_request_id", config["synapse"])
        self.assertNotIn("organizer_synapse_user_id", config["synapse"])

    def test_public_image_is_validated_before_synapse_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wdl = root / "workflow.wdl"
            weights = root / "weights.pt"
            wdl.write_text(WDL, encoding="utf-8")
            weights.write_bytes(b"weights")
            events: list[str] = []
            validated_images: list[str] = []
            terra = Mock()
            terra.authenticated_anvil_identity.return_value = AnvilIdentity(
                "user@example.org", "subject"
            )
            gateway = Mock()
            gateway.setup_private_submission_project.return_value = ({"id": "syn456"}, Mock())
            gateway.preflight_submission.return_value = _readiness()
            gateway.upload_and_submit.side_effect = lambda *_args: (
                events.append("upload"),
                {
                    "submission": SimpleNamespace(id="55", entityId="syn789"),
                    "reused": True,
                },
            )[1]
            bundle = SimpleNamespace(
                manifest=SimpleNamespace(
                    container_images=(IMAGE,),
                    canonical_json=Mock(return_value=b"{}"),
                    core_fingerprint="c" * 64,
                    full_fingerprint="f" * 64,
                ),
                write_archive=Mock(side_effect=lambda path: Path(path).write_bytes(b"bundle")),
            )

            def verify_image(image: str) -> None:
                events.append("image")
                validated_images.append(image)

            request = SubmissionRequest(
                profile=_profile(),
                wdl=wdl,
                weights=weights,
                anvil_email="user@example.org",
            )
            with (
                patch(
                    "anvil_dream_participant.submissions.operations.ParticipantTerraClient",
                    return_value=terra,
                ),
                patch(
                    "anvil_dream_participant.submissions.operations.ParticipantSynapseGateway",
                    return_value=gateway,
                ),
                patch(
                    "anvil_dream_participant.submissions.operations.SubmissionBundle.create",
                    return_value=bundle,
                ),
                patch(
                    "anvil_dream_participant.submissions.operations.verify_public_container_image",
                    side_effect=verify_image,
                ),
                patch("anvil_dream_participant.submissions.operations.progress"),
            ):
                receipt = submit_bundle(request)

        self.assertEqual(events, ["image", "upload"])
        self.assertEqual(validated_images, [IMAGE])
        self.assertEqual(receipt["submission_id"], "55")
        self.assertEqual(receipt["entity_id"], "syn789")
        self.assertEqual(receipt["core_fingerprint"], "c" * 64)
        self.assertEqual(receipt["full_fingerprint"], "f" * 64)
        self.assertTrue(receipt["tar_reused"])
        gateway.setup_private_submission_project.assert_called_once_with()
        gateway.preflight_submission.assert_called_once_with("syn456", None)

    def test_verified_vm_identity_skips_local_google_userinfo_and_keeps_team(self) -> None:
        identity = AnvilIdentity("user@example.org", "subject")
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
        gateway.setup_private_submission_project.return_value = ({"id": "synParent"}, Mock())
        gateway.preflight_submission.return_value = SimpleNamespace(submitter_id="123")
        gateway.upload_and_submit.return_value = {
            "submission": SimpleNamespace(id="55", entityId="syn789"),
            "reused": False,
        }
        request = SubmissionRequest(
            profile=_profile(),
            wdl=Path("workflow.wdl"),
            weights=Path("weights.pt"),
            anvil_email=identity.email,
            team="submission-team-1",
        )

        with (
            patch(
                "anvil_dream_participant.submissions.operations.ParticipantTerraClient"
            ) as terra,
            patch(
                "anvil_dream_participant.submissions.operations.ParticipantSynapseGateway",
                return_value=gateway,
            ),
            patch(
                "anvil_dream_participant.submissions.operations.SubmissionBundle.create",
                return_value=bundle,
            ) as create,
            patch("anvil_dream_participant.submissions.operations.progress"),
        ):
            submit_bundle(request, identity=identity)

        terra.assert_not_called()
        gateway.preflight_submission.assert_called_once_with("synParent", "submission-team-1")
        gateway.upload_and_submit.assert_called_once()
        self.assertEqual(
            gateway.upload_and_submit.call_args.args[1:],
            ("synParent", "participant-prediction", "submission-team-1"),
        )
        self.assertEqual(create.call_args.kwargs["submission_team"], "submission-team-1")
        self.assertEqual(create.call_args.kwargs["anvil_subject_id"], identity.subject_id)

    def test_evaluation_override_selects_the_generic_synapse_queue(self) -> None:
        identity = AnvilIdentity("person@example.org", "sam-subject")
        gateway = Mock()
        gateway.setup_private_submission_project.return_value = ({"id": "synParent"}, Mock())
        gateway.preflight_submission.return_value = SimpleNamespace(submitter_id="123")
        gateway.upload_and_submit.return_value = {
            "submission": SimpleNamespace(id="55", entityId="syn789"),
            "reused": False,
        }
        bundle = SimpleNamespace(
            manifest=SimpleNamespace(
                container_images=(),
                canonical_json=Mock(return_value=b"{}"),
                core_fingerprint="c" * 64,
                full_fingerprint="f" * 64,
            ),
            write_archive=Mock(side_effect=lambda path: Path(path).write_bytes(b"bundle")),
        )
        request = SubmissionRequest(
            profile=_profile(),
            wdl=Path("workflow.wdl"),
            weights=Path("weights.pt"),
            anvil_email=identity.email,
            evaluation_id_override="2",
        )

        with (
            patch(
                "anvil_dream_participant.submissions.operations.ParticipantSynapseGateway",
                return_value=gateway,
            ) as gateway_type,
            patch(
                "anvil_dream_participant.submissions.operations.SubmissionBundle.create",
                return_value=bundle,
            ),
            patch("anvil_dream_participant.submissions.operations.progress"),
        ):
            submit_bundle(request, identity=identity)

        config = gateway_type.call_args.args[0]
        self.assertEqual(config["evaluation_id"], "2")
        self.assertEqual(config["queue_name"], "Synapse Evaluation 2")

    def test_profile_validation_fails_before_cloud_access(self) -> None:
        from anvil_dream_participant.profile import PROFILE

        example = Path(__file__).resolve().parents[3] / (
            "examples/proticelli/participant.wdl"
        )
        source = example.read_text(encoding="utf-8").replace(
            "  runtime {",
            "  runtime {\n    cpu: 8",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            wdl = Path(directory) / "workflow.wdl"
            wdl.write_text(source, encoding="utf-8")
            request = SubmissionRequest(
                profile=PROFILE,
                wdl=wdl,
                weights=Path("weights.pt"),
                anvil_email="person@example.org",
            )
            with (
                patch(
                    "anvil_dream_participant.submissions.operations.ParticipantTerraClient"
                ) as terra,
                patch(
                    "anvil_dream_participant.submissions.operations.ParticipantSynapseGateway"
                ) as synapse,
                self.assertRaisesRegex(WorkflowContractError, "unsupported cpu"),
            ):
                submit_bundle(request)

        terra.assert_not_called()
        synapse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
