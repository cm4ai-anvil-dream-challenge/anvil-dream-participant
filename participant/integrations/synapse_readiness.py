"""Synapse challenge route, membership, and queue readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from synapseclient.core.exceptions import SynapseError, SynapseHTTPError

from .synapse_reuse import entity_value


ChallengeAccessState = {
    "READY": "ready",
    "UNCERTIFIED": "uncertified",
    "QUEUE_SUBMIT_DENIED": "queue_submit_denied",
    "MEMBERSHIP_REQUEST_REQUIRED": "membership_request_required",
    "MEMBERSHIP_REQUEST_PENDING": "membership_request_pending",
    "INVITATION_PENDING": "invitation_pending",
    "ACCESS_REQUIREMENTS_UNMET": "access_requirements_unmet",
    "TEAM_REQUESTS_DISABLED": "team_requests_disabled",
    "REGISTRATION_INCOMPLETE": "registration_incomplete",
    "PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED": "participant_team_queue_access_denied",
    "SUBMISSION_TEAM_INELIGIBLE": "submission_team_ineligible",
}


def _challenge_url(route: dict[str, object]) -> str:
    return f"https://www.synapse.org/Synapse:{route['content_source_id']}/challenge"


@dataclass(frozen=True)
class ChallengeReadiness:
    route: dict[str, object]
    submitter_id: str
    queue_can_submit: bool
    membership: dict[str, object]
    state: str
    next_step: str
    participant_team_acl_can_submit: bool | None = None
    submission_team_id: str | None = None
    submission_team_name: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == ChallengeAccessState["READY"]


class ChallengeReadinessError(RuntimeError):
    def __init__(self, message: str, readiness: ChallengeReadiness | None = None):
        super().__init__(message)
        self.readiness = readiness


def SynapseChallengeReadiness(client: Any, config: Mapping[str, object]):
    from types import SimpleNamespace

    result = SimpleNamespace(client=client, config=config)

    def _http_status(error: SynapseHTTPError) -> int | None:
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None)

    def _is_certified(submitter_id: str) -> bool:
        try:
            record = result.client.restGET(f"/user/{submitter_id}/certifiedUserPassingRecord")
        except SynapseHTTPError as error:
            if result._http_status(error) == 404:
                return False
            raise
        if "certified" in record:
            return bool(record["certified"])
        return bool(record.get("passed")) and not bool(record.get("revoked"))

    def _challenge_route(evaluation: Any) -> dict[str, object]:
        evaluation_id = str(entity_value(evaluation, "id"))
        content_source_id = str(entity_value(evaluation, "contentSource"))
        if not content_source_id or content_source_id == "None":
            raise ChallengeReadinessError(
                f"Evaluation {evaluation_id} has no challenge content source configured. "
                "Ask the challenge organizer to connect the Evaluation to its Challenge."
            )
        challenge = result.client.restGET(f"/entity/{content_source_id}/challenge")
        participant_team_id = str(challenge["participantTeamId"])
        team = result.client.restGET(f"/team/{participant_team_id}")
        return {
            "evaluation_id": evaluation_id,
            "content_source_id": content_source_id,
            "challenge_id": str(challenge["id"]),
            "participant_team_id": participant_team_id,
            "participant_team_name": str(team.get("name", participant_team_id)),
            "participant_team_accepts_requests": bool(team.get("canRequestMembership")),
        }

    def _registered(submitter_id: str, challenge_id: str) -> bool:
        limit = 100
        offset = 0
        while True:
            page = result.client.restGET(
                f"/challenge?participantId={submitter_id}&limit={limit}&offset={offset}"
            )
            results = page.get("results", [])
            if any(str(item.get("id")) == challenge_id for item in results):
                return True
            if len(results) < limit:
                return False
            offset += limit

    def _membership(
        route: dict[str, object],
        submitter_id: str,
    ) -> dict[str, object]:
        status = result.client.restGET(
            f"/team/{route["participant_team_id"]}/member/{submitter_id}/membershipStatus"
        )
        registered = result._registered(submitter_id, route["challenge_id"])
        return {
            "is_member": bool(status.get("isMember")),
            "is_registered": registered,
            "has_open_invitation": bool(status.get("hasOpenInvitation")),
            "has_open_request": bool(status.get("hasOpenRequest")),
            "has_unmet_access_requirement": bool(status.get("hasUnmetAccessRequirement")),
            "team_accepts_requests": route["participant_team_accepts_requests"],
        }

    def _state_and_next_step(
        route: dict[str, object],
        certified: bool,
        queue_can_submit: bool,
        membership: dict[str, object],
        participant_team_acl_can_submit: bool | None,
    ) -> tuple[str, str]:
        if not certified:
            return (
                ChallengeAccessState["UNCERTIFIED"],
                "Complete Synapse Certified User status, then rerun this check: "
                "https://www.synapse.org/#!Profile:certified",
            )
        if membership["has_unmet_access_requirement"]:
            return (
                ChallengeAccessState["ACCESS_REQUIREMENTS_UNMET"],
                f"Open {_challenge_url(route)}, review the challenge terms and rules, and "
                "complete the listed access requirements.",
            )
        if membership["has_open_invitation"]:
            return (
                ChallengeAccessState["INVITATION_PENDING"],
                f"Accept the pending Challenge registration invitation in Synapse "
                f"({route["participant_team_name"]}), then rerun this check.",
            )
        if membership["has_open_request"]:
            return (
                ChallengeAccessState["MEMBERSHIP_REQUEST_PENDING"],
                "Your Challenge registration request is waiting for an organizer. "
                "No new request is needed.",
            )
        if not membership["is_member"]:
            if membership["team_accepts_requests"]:
                return (
                    ChallengeAccessState["MEMBERSHIP_REQUEST_REQUIRED"],
                    "This Synapse account is not registered for the Challenge. Open the "
                    "challenge website and complete Synapse registration, then retry submission.",
                )
            return (
                ChallengeAccessState["TEAM_REQUESTS_DISABLED"],
                "The Challenge is not accepting registration requests. Contact the organizers.",
            )
        if not membership["is_registered"]:
            return (
                ChallengeAccessState["REGISTRATION_INCOMPLETE"],
                f"You belong to {route["participant_team_name"]}, but Synapse does not list the "
                f"account as registered for challenge {route["challenge_id"]}. Open "
                f"{_challenge_url(route)} or contact the challenge organizers.",
            )
        if participant_team_acl_can_submit is False:
            return (
                ChallengeAccessState["PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED"],
                f"Evaluation {route["evaluation_id"]} does not grant SUBMIT to the Challenge "
                f"participant team {route["participant_team_name"]}. Ask the challenge organizer "
                "to grant that team Can Submit before participant submissions.",
            )
        if not queue_can_submit:
            return (
                ChallengeAccessState["QUEUE_SUBMIT_DENIED"],
                "Ask the challenge organizer to grant the participant team Can Submit "
                f"on Evaluation {route["evaluation_id"]}.",
            )
        return ChallengeAccessState["READY"], "Synapse challenge registration is ready."

    def challenge_readiness(submission_team: str | None = None) -> ChallengeReadiness:
        evaluation = result.evaluation()
        route = result._challenge_route(evaluation)
        profile = result.client.restGET("/userProfile")
        submitter_id = str(profile["ownerId"])
        certified = result._is_certified(submitter_id)
        permissions = result.client.restGET(f"/evaluation/{route["evaluation_id"]}/permissions")
        queue_can_submit = bool(permissions.get("canSubmit"))
        participant_team_acl_can_submit = None
        if bool(permissions.get("canChangePermissions")):
            acl = result.client.restGET(f"/evaluation/{route["evaluation_id"]}/acl")
            participant_team_acl_can_submit = any(
                str(entry.get("principalId")) == route["participant_team_id"]
                and "SUBMIT" in {str(access).upper() for access in entry.get("accessType", [])}
                for entry in acl.get("resourceAccess", [])
            )
        membership = result._membership(route, submitter_id)
        state, next_step = result._state_and_next_step(
            route,
            certified,
            queue_can_submit,
            membership,
            participant_team_acl_can_submit,
        )
        submission_team_id: str | None = None
        submission_team_name: str | None = None
        if submission_team and state == ChallengeAccessState["READY"]:
            try:
                selected_team = result.client.getTeam(submission_team)
            except (SynapseError, ValueError) as error:
                raise ChallengeReadinessError(
                    f"Synapse submission team {submission_team!r} was not found or cannot "
                    f"be used: {error}"
                ) from error
            submission_team_id = str(entity_value(selected_team, "id"))
            submission_team_name = str(entity_value(selected_team, "name", submission_team_id))
            eligibility = result.client.restGET(
                f"/evaluation/{route["evaluation_id"]}/team/{submission_team_id}/submissionEligibility"
            )
            team_eligibility = eligibility.get("teamEligibility") or {}
            if not bool(team_eligibility.get("isEligible")):
                state = ChallengeAccessState["SUBMISSION_TEAM_INELIGIBLE"]
                if not bool(team_eligibility.get("isRegistered")):
                    reason_text = "The team is not registered for this challenge."
                elif bool(team_eligibility.get("isQuotaFilled")):
                    reason_text = "The team has reached its submission quota."
                else:
                    reason_text = "Ask the organizer to correct this team's Challenge eligibility."
                next_step = (
                    f"Submission team {submission_team_name} is not eligible for Evaluation "
                    f"{route["evaluation_id"]}. {reason_text}"
                )
        return ChallengeReadiness(
            route=route,
            submitter_id=submitter_id,
            queue_can_submit=queue_can_submit,
            membership=membership,
            state=state,
            next_step=next_step,
            participant_team_acl_can_submit=participant_team_acl_can_submit,
            submission_team_id=submission_team_id,
            submission_team_name=submission_team_name,
        )

    def blocked_message(readiness: ChallengeReadiness) -> str:
        return (
            f"Synapse challenge registration is not ready ({readiness.state}). "
            f"{readiness.next_step}"
        )

    def evaluation() -> Any:
        """Resolve the configured queue by explicit ID or fallback name."""
        if evaluation_id := result.config.get("evaluation_id"):
            return result.client.getEvaluation(str(evaluation_id))
        return result.client.getEvaluationByName(str(result.config["queue_name"]))


    result._http_status = _http_status
    result._is_certified = _is_certified
    result._challenge_route = _challenge_route
    result._registered = _registered
    result._membership = _membership
    result._state_and_next_step = _state_and_next_step
    result.challenge_readiness = challenge_readiness
    result.blocked_message = blocked_message
    result.evaluation = evaluation
    return result