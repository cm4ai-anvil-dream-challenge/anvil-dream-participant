import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dream_participant.cli import main
from dream_participant.cli.participant import build_parser, run as participant_run
from dream_participant.integrations.synapse import ParticipantSynapseGateway
from dream_participant.integrations.synapse_readiness import (
    ChallengeAccessState,
    ChallengeReadinessError,
    ChallengeRoute,
    SynapseChallengeReadiness,
)


def readiness_gateway(
    *,
    permissions: dict[str, bool],
    membership: dict[str, bool],
    acl: dict[str, object] | None = None,
    team_eligibility: dict[str, bool] | None = None,
) -> ParticipantSynapseGateway:
    gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
    gateway.client = Mock()
    config = SimpleNamespace(queue_name="test queue")
    gateway.readiness = SynapseChallengeReadiness(gateway.client, config)
    route = ChallengeRoute(
        "evaluation-1",
        "challenge-entity",
        "challenge-1",
        "participant-team-1",
        "Participants",
        True,
    )
    gateway.readiness.evaluation = Mock(return_value={"id": route.evaluation_id})
    gateway.readiness._challenge_route = Mock(return_value=route)
    gateway.assert_private_to_submitter = Mock()
    gateway.client.getTeam.return_value = {
        "id": "submission-team-1",
        "name": "Submission team",
    }

    def rest_get(path: str) -> dict[str, object]:
        responses: dict[str, dict[str, object]] = {
            "/userProfile": {"ownerId": "123", "userName": "participant"},
            "/user/123/certifiedUserPassingRecord": {"certified": True},
            "/evaluation/evaluation-1/permissions": permissions,
            "/team/participant-team-1/member/123/membershipStatus": membership,
            "/challenge?participantId=123&limit=100&offset=0": {
                "results": [{"id": "challenge-1"} if membership.get("isMember") else {}]
            },
        }
        if acl is not None:
            responses["/evaluation/evaluation-1/acl"] = acl
        if team_eligibility is not None:
            responses["/evaluation/evaluation-1/team/submission-team-1/submissionEligibility"] = {
                "teamEligibility": team_eligibility
            }
        try:
            return responses[path]
        except KeyError as error:
            raise AssertionError(f"Unexpected Synapse request {path}") from error

    gateway.client.restGET.side_effect = rest_get
    return gateway


class SynapseReadinessTests(unittest.TestCase):
    def test_public_cli_rejects_organizer_role(self) -> None:
        with self.assertRaisesRegex(SystemExit, "not included in the participant package"):
            main(["organizer", "--help"])

    def test_participant_help_does_not_mark_a_run_failed(self) -> None:
        with patch("dream_participant.cli.participant.close_progress") as close:
            with self.assertRaises(SystemExit) as raised:
                participant_run(["submit", "--help"])
        self.assertEqual(raised.exception.code, 0)
        close.assert_called_once_with(failed=False)

    def test_admin_effective_permission_does_not_mask_missing_participant_team_acl(self) -> None:
        gateway = readiness_gateway(
            permissions={"canSubmit": True, "canChangePermissions": True},
            membership={"isMember": True},
            acl={"resourceAccess": [{"principalId": "another-team", "accessType": ["SUBMIT"]}]},
        )

        with self.assertRaises(ChallengeReadinessError) as raised:
            gateway.preflight_submission("private-parent")

        readiness = raised.exception.readiness
        self.assertIsNotNone(readiness)
        self.assertEqual(readiness.state, ChallengeAccessState.PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED)
        self.assertTrue(readiness.queue_can_submit)
        self.assertFalse(readiness.participant_team_acl_can_submit)
        self.assertIn("grant that team Can Submit", str(raised.exception))
        gateway.assert_private_to_submitter.assert_not_called()
        gateway.client.restGET.assert_any_call("/evaluation/evaluation-1/acl")

    def test_registered_participant_preflights_as_individual_without_acl_read(self) -> None:
        gateway = readiness_gateway(
            permissions={"canSubmit": True, "canChangePermissions": False},
            membership={"isMember": True},
        )

        readiness = gateway.preflight_submission("private-parent")

        self.assertTrue(readiness.ready)
        self.assertIsNone(readiness.submission_team_id)
        self.assertIsNone(readiness.participant_team_acl_can_submit)
        gateway.assert_private_to_submitter.assert_called_once_with("private-parent")
        self.assertNotIn(
            "/evaluation/evaluation-1/acl",
            [call.args[0] for call in gateway.client.restGET.call_args_list],
        )
        self.assertFalse(
            any(
                "/submissionTeams?" in call.args[0]
                for call in gateway.client.restGET.call_args_list
            )
        )
        self.assertFalse(
            any("/user/123/team?" in call.args[0] for call in gateway.client.restGET.call_args_list)
        )

    def test_nonmember_and_user_without_queue_permission_are_blocked(self) -> None:
        cases = (
            (
                "nonmember",
                {"canSubmit": True, "canChangePermissions": False},
                {"isMember": False},
                ChallengeAccessState.MEMBERSHIP_REQUEST_REQUIRED,
            ),
            (
                "denied user",
                {"canSubmit": False, "canChangePermissions": False},
                {"isMember": True},
                ChallengeAccessState.QUEUE_SUBMIT_DENIED,
            ),
        )
        for name, permissions, membership, expected_state in cases:
            with self.subTest(name=name):
                gateway = readiness_gateway(permissions=permissions, membership=membership)
                with self.assertRaises(ChallengeReadinessError) as raised:
                    gateway.preflight_submission("private-parent")
                self.assertEqual(raised.exception.readiness.state, expected_state)
                gateway.assert_private_to_submitter.assert_not_called()

    def test_explicit_submission_team_must_be_eligible(self) -> None:
        gateway = readiness_gateway(
            permissions={"canSubmit": True, "canChangePermissions": False},
            membership={"isMember": True},
            team_eligibility={"isEligible": False, "isRegistered": False},
        )

        with self.assertRaises(ChallengeReadinessError) as raised:
            gateway.preflight_submission("private-parent", "submission-team-1")

        self.assertEqual(
            raised.exception.readiness.state,
            ChallengeAccessState.SUBMISSION_TEAM_INELIGIBLE,
        )
        gateway.client.restGET.assert_any_call(
            "/evaluation/evaluation-1/team/submission-team-1/submissionEligibility"
        )
        gateway.assert_private_to_submitter.assert_not_called()

    def test_eligible_explicit_team_is_retained_for_submission(self) -> None:
        gateway = readiness_gateway(
            permissions={"canSubmit": True, "canChangePermissions": False},
            membership={"isMember": True},
            team_eligibility={"isEligible": True, "isRegistered": True},
        )

        readiness = gateway.preflight_submission("private-parent", "submission-team-1")

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.submission_team_id, "submission-team-1")
        self.assertEqual(readiness.submission_team_name, "Submission team")
        gateway.client.getTeam.assert_called_once_with("submission-team-1")

    def test_submit_requires_wdl_weights_and_anvil_identity(self) -> None:
        args = build_parser().parse_args(
            [
                "submit",
                "--wdl",
                "workflow.wdl",
                "--weights",
                "weights.bin",
                "--anvil-email",
                "person@example.org",
            ]
        )
        self.assertEqual(args.anvil_email, "person@example.org")
        self.assertIsNone(args.other_files)
        self.assertEqual(args.evaluation_id, "9620030")

    def test_submit_anvil_parses_gcs_paths_and_is_listed_as_separate_command(self) -> None:
        args = build_parser().parse_args(
            [
                "submit-anvil",
                "--wdl",
                "gs://source-bucket/model/workflow.wdl",
                "--weights",
                "gs://source-bucket/model/weights.pt",
                "--other-files",
                "gs://source-bucket/context/",
                "--evaluation-id",
                "9620087",
                "--name",
                "anvil-run",
                "--team",
                "team-7",
            ]
        )

        self.assertEqual(args.command, "submit-anvil")
        self.assertEqual(args.wdl, "gs://source-bucket/model/workflow.wdl")
        self.assertEqual(args.weights, "gs://source-bucket/model/weights.pt")
        self.assertEqual(args.other_files, "gs://source-bucket/context/")
        self.assertFalse(hasattr(args, "anvil_email"))
        self.assertEqual(args.evaluation_id, "9620087")
        self.assertEqual(args.name, "anvil-run")
        self.assertEqual(args.team, "team-7")
        with patch(
            "dream_participant.cli.participant._submit_anvil_bundle", return_value=0
        ) as submit:
            self.assertEqual(
                main(
                    [
                        "submit-anvil",
                        "--wdl",
                        "gs://source-bucket/workflow.wdl",
                        "--weights",
                        "gs://source-bucket/weights.pt",
                    ]
                ),
                0,
            )
        self.assertEqual(submit.call_args.args[0].synapse.evaluation_id, "9620030")

    def test_submit_anvil_rejects_a_claimed_email(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "submit-anvil",
                    "--wdl",
                    "gs://source-bucket/workflow.wdl",
                    "--weights",
                    "gs://source-bucket/weights.pt",
                    "--anvil-email",
                    "someone-else@example.org",
                ]
            )

    def test_submit_uses_the_given_evaluation_id_without_config(self) -> None:
        routes = (
            (None, "9620030", "CM4AI AnVIL DREAM Challenge Evaluation"),
            ("9620087", "9620087", "Synapse Evaluation 9620087"),
            ("1234567", "1234567", "Synapse Evaluation 1234567"),
        )
        for selected_id, evaluation_id, queue_name in routes:
            with self.subTest(evaluation_id=evaluation_id):
                arguments = [
                    "submit",
                    "--wdl",
                    "workflow.wdl",
                    "--weights",
                    "weights.bin",
                    "--anvil-email",
                    "person@example.org",
                ]
                if selected_id:
                    arguments.extend(("--evaluation-id", selected_id))
                with patch(
                    "dream_participant.cli.participant._submit_bundle", return_value=0
                ) as submit:
                    self.assertEqual(main(arguments), 0)
                self.assertEqual(submit.call_args.args[0].synapse.evaluation_id, evaluation_id)
                self.assertEqual(submit.call_args.args[0].synapse.queue_name, queue_name)

    def test_submit_rejects_a_name_or_invalid_evaluation_id(self) -> None:
        required = [
            "submit",
            "--wdl",
            "workflow.wdl",
            "--weights",
            "weights.bin",
            "--anvil-email",
            "person@example.org",
        ]
        for option in (
            ("--evaluation-id", "test"),
            ("--evaluation-id", "0"),
            ("--challenge", "test"),
        ):
            with self.subTest(option=option), self.assertRaises(SystemExit):
                build_parser().parse_args([*required, *option])

    def test_participant_config_option_is_rejected(self) -> None:
        with (
            patch("dream_participant.cli.participant._submit_bundle", return_value=0),
            self.assertRaises(SystemExit),
        ):
            main(
                [
                    "--config",
                    "participant.toml",
                    "submit",
                    "--wdl",
                    "workflow.wdl",
                    "--weights",
                    "weights.bin",
                    "--anvil-email",
                    "person@example.org",
                ]
            )

    def test_submit_ignores_an_old_home_directory_participant_toml(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / ".dream" / "participant.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                '[synapse]\nqueue_name = "old queue"\nevaluation_id = "wrong"\n',
                encoding="utf-8",
            )
            with (
                patch("pathlib.Path.home", return_value=Path(directory)),
                patch("dream_participant.cli.participant._submit_bundle", return_value=0) as submit,
            ):
                self.assertEqual(
                    main(
                        [
                            "submit",
                            "--wdl",
                            "workflow.wdl",
                            "--weights",
                            "weights.bin",
                            "--anvil-email",
                            "person@example.org",
                        ]
                    ),
                    0,
                )
        self.assertEqual(submit.call_args.args[0].synapse.evaluation_id, "9620030")

    def test_registration_commands_are_website_only(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["register"])


if __name__ == "__main__":
    unittest.main()
