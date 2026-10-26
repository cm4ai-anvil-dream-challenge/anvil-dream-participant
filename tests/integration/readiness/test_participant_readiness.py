import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from anvil_dream_participant.integrations.synapse import ParticipantSynapseGateway
from anvil_dream_participant.integrations.synapse_readiness import (
    ChallengeAccessState,
    ChallengeReadinessError,
    SynapseChallengeReadiness,
)


def readiness_gateway(
    *,
    permissions: dict[str, bool],
    membership: dict[str, bool],
    acl: dict[str, object] | None = None,
    team_eligibility: dict[str, bool] | None = None,
) -> ParticipantSynapseGateway:
    gateway = ParticipantSynapseGateway()
    gateway.client = Mock()
    config = SimpleNamespace(queue_name="test queue")
    gateway.readiness = SynapseChallengeReadiness(gateway.client, config)
    route = {
        "evaluation_id": "evaluation-1",
        "content_source_id": "challenge-entity",
        "challenge_id": "challenge-1",
        "participant_team_id": "participant-team-1",
        "participant_team_name": "Participants",
        "participant_team_accepts_requests": True,
    }
    gateway.readiness.evaluation = Mock(return_value={"id": route["evaluation_id"]})
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
            responses[
                "/evaluation/evaluation-1/team/submission-team-1/submissionEligibility"
            ] = {"teamEligibility": team_eligibility}
        try:
            return responses[path]
        except KeyError as error:
            raise AssertionError(f"Unexpected Synapse request {path}") from error

    gateway.client.restGET.side_effect = rest_get
    return gateway


class SynapseReadinessTests(unittest.TestCase):
    def test_admin_permission_does_not_mask_missing_participant_team_acl(self) -> None:
        gateway = readiness_gateway(
            permissions={"canSubmit": True, "canChangePermissions": True},
            membership={"isMember": True},
            acl={"resourceAccess": [{"principalId": "another-team", "accessType": ["SUBMIT"]}]},
        )

        with self.assertRaises(ChallengeReadinessError) as raised:
            gateway.preflight_submission("private-parent")

        readiness = raised.exception.readiness
        self.assertIsNotNone(readiness)
        self.assertEqual(readiness.state, ChallengeAccessState["PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED"])
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
            any("/submissionTeams?" in call.args[0] for call in gateway.client.restGET.call_args_list)
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
                ChallengeAccessState["MEMBERSHIP_REQUEST_REQUIRED"],
            ),
            (
                "denied user",
                {"canSubmit": False, "canChangePermissions": False},
                {"isMember": True},
                ChallengeAccessState["QUEUE_SUBMIT_DENIED"],
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
            ChallengeAccessState["SUBMISSION_TEAM_INELIGIBLE"],
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


if __name__ == "__main__":
    unittest.main()
