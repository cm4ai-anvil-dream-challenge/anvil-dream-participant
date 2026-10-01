"""Synapse challenge route, membership, and queue readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from synapseclient.core.exceptions import SynapseError, SynapseHTTPError

from .synapse_reuse import entity_value


class _EvaluationConfig(Protocol):
    queue_name: str
    evaluation_id: str | None


class ChallengeAccessState(StrEnum):
    READY = "ready"
    UNCERTIFIED = "uncertified"
    QUEUE_SUBMIT_DENIED = "queue_submit_denied"
    MEMBERSHIP_REQUEST_REQUIRED = "membership_request_required"
    MEMBERSHIP_REQUEST_PENDING = "membership_request_pending"
    INVITATION_PENDING = "invitation_pending"
    ACCESS_REQUIREMENTS_UNMET = "access_requirements_unmet"
    TEAM_REQUESTS_DISABLED = "team_requests_disabled"
    REGISTRATION_INCOMPLETE = "registration_incomplete"
    PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED = "participant_team_queue_access_denied"
    SUBMISSION_TEAM_INELIGIBLE = "submission_team_ineligible"


@dataclass(frozen=True)
class ChallengeRoute:
    evaluation_id: str
    content_source_id: str
    challenge_id: str
    participant_team_id: str
    participant_team_name: str
    participant_team_accepts_requests: bool

    @property
    def challenge_url(self) -> str:
        return f"https://www.synapse.org/Synapse:{self.content_source_id}/challenge"


@dataclass(frozen=True)
class TeamMembershipSnapshot:
    is_member: bool
    is_registered: bool
    has_open_invitation: bool
    has_open_request: bool
    has_unmet_access_requirement: bool
    team_accepts_requests: bool


@dataclass(frozen=True)
class ChallengeReadiness:
    route: ChallengeRoute
    submitter_id: str
    queue_can_submit: bool
    membership: TeamMembershipSnapshot
    state: ChallengeAccessState
    next_step: str
    participant_team_acl_can_submit: bool | None = None
    submission_team_id: str | None = None
    submission_team_name: str | None = None

    @property
    def ready(self) -> bool:
        return self.state is ChallengeAccessState.READY


class ChallengeReadinessError(RuntimeError):
    def __init__(self, message: str, readiness: ChallengeReadiness | None = None):
        super().__init__(message)
        self.readiness = readiness


class SynapseChallengeReadiness:
    def __init__(self, client: Any, config: _EvaluationConfig):
        self.client = client
        self.config = config

    @staticmethod
    def _http_status(error: SynapseHTTPError) -> int | None:
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None)

    def _is_certified(self, submitter_id: str) -> bool:
        try:
            record = self.client.restGET(f"/user/{submitter_id}/certifiedUserPassingRecord")
        except SynapseHTTPError as error:
            if self._http_status(error) == 404:
                return False
            raise
        if "certified" in record:
            return bool(record["certified"])
        return bool(record.get("passed")) and not bool(record.get("revoked"))

    def _challenge_route(self, evaluation: Any) -> ChallengeRoute:
        evaluation_id = str(entity_value(evaluation, "id"))
        content_source_id = str(entity_value(evaluation, "contentSource"))
        if not content_source_id or content_source_id == "None":
            raise ChallengeReadinessError(
                f"Evaluation {evaluation_id} has no challenge content source configured. "
                "Ask the challenge organizer to connect the Evaluation to its Challenge."
            )
        challenge = self.client.restGET(f"/entity/{content_source_id}/challenge")
        participant_team_id = str(challenge["participantTeamId"])
        team = self.client.restGET(f"/team/{participant_team_id}")
        return ChallengeRoute(
            evaluation_id=evaluation_id,
            content_source_id=content_source_id,
            challenge_id=str(challenge["id"]),
            participant_team_id=participant_team_id,
            participant_team_name=str(team.get("name", participant_team_id)),
            participant_team_accepts_requests=bool(team.get("canRequestMembership")),
        )

    def _registered(self, submitter_id: str, challenge_id: str) -> bool:
        limit = 100
        offset = 0
        while True:
            page = self.client.restGET(
                f"/challenge?participantId={submitter_id}&limit={limit}&offset={offset}"
            )
            results = page.get("results", [])
            if any(str(item.get("id")) == challenge_id for item in results):
                return True
            if len(results) < limit:
                return False
            offset += limit

    def _membership(
        self,
        route: ChallengeRoute,
        submitter_id: str,
    ) -> TeamMembershipSnapshot:
        status = self.client.restGET(
            f"/team/{route.participant_team_id}/member/{submitter_id}/membershipStatus"
        )
        registered = self._registered(submitter_id, route.challenge_id)
        return TeamMembershipSnapshot(
            is_member=bool(status.get("isMember")),
            is_registered=registered,
            has_open_invitation=bool(status.get("hasOpenInvitation")),
            has_open_request=bool(status.get("hasOpenRequest")),
            has_unmet_access_requirement=bool(status.get("hasUnmetAccessRequirement")),
            team_accepts_requests=route.participant_team_accepts_requests,
        )

    @staticmethod
    def _state_and_next_step(
        route: ChallengeRoute,
        certified: bool,
        queue_can_submit: bool,
        membership: TeamMembershipSnapshot,
        participant_team_acl_can_submit: bool | None,
    ) -> tuple[ChallengeAccessState, str]:
        if not certified:
            return (
                ChallengeAccessState.UNCERTIFIED,
                "Complete Synapse Certified User status, then rerun this check: "
                "https://www.synapse.org/#!Profile:certified",
            )
        if membership.has_unmet_access_requirement:
            return (
                ChallengeAccessState.ACCESS_REQUIREMENTS_UNMET,
                f"Open {route.challenge_url}, review the challenge terms and rules, and "
                "complete the listed access requirements.",
            )
        if membership.has_open_invitation:
            return (
                ChallengeAccessState.INVITATION_PENDING,
                f"Accept the pending Challenge registration invitation in Synapse "
                f"({route.participant_team_name}), then rerun this check.",
            )
        if membership.has_open_request:
            return (
                ChallengeAccessState.MEMBERSHIP_REQUEST_PENDING,
                "Your Challenge registration request is waiting for an organizer. "
                "No new request is needed.",
            )
        if not membership.is_member:
            if membership.team_accepts_requests:
                return (
                    ChallengeAccessState.MEMBERSHIP_REQUEST_REQUIRED,
                    "This Synapse account is not registered for the Challenge. Open the "
                    "challenge website and complete Synapse registration, then retry submission.",
                )
            return (
                ChallengeAccessState.TEAM_REQUESTS_DISABLED,
                "The Challenge is not accepting registration requests. Contact the organizers.",
            )
        if not membership.is_registered:
            return (
                ChallengeAccessState.REGISTRATION_INCOMPLETE,
                f"You belong to {route.participant_team_name}, but Synapse does not list the "
                f"account as registered for challenge {route.challenge_id}. Open "
                f"{route.challenge_url} or contact the challenge organizers.",
            )
        if participant_team_acl_can_submit is False:
            return (
                ChallengeAccessState.PARTICIPANT_TEAM_QUEUE_ACCESS_DENIED,
                f"Evaluation {route.evaluation_id} does not grant SUBMIT to the Challenge "
                f"participant team {route.participant_team_name}. Ask the challenge organizer "
                "to grant that team Can Submit before participant submissions.",
            )
        if not queue_can_submit:
            return (
                ChallengeAccessState.QUEUE_SUBMIT_DENIED,
                "Ask the challenge organizer to grant the participant team Can Submit "
                f"on Evaluation {route.evaluation_id}.",
            )
        return ChallengeAccessState.READY, "Synapse challenge registration is ready."

    def challenge_readiness(self, submission_team: str | None = None) -> ChallengeReadiness:
        evaluation = self.evaluation()
        route = self._challenge_route(evaluation)
        profile = self.client.restGET("/userProfile")
        submitter_id = str(profile["ownerId"])
        certified = self._is_certified(submitter_id)
        permissions = self.client.restGET(f"/evaluation/{route.evaluation_id}/permissions")
        queue_can_submit = bool(permissions.get("canSubmit"))
        participant_team_acl_can_submit = None
        if bool(permissions.get("canChangePermissions")):
            acl = self.client.restGET(f"/evaluation/{route.evaluation_id}/acl")
            participant_team_acl_can_submit = any(
                str(entry.get("principalId")) == route.participant_team_id
                and "SUBMIT" in {str(access).upper() for access in entry.get("accessType", [])}
                for entry in acl.get("resourceAccess", [])
            )
        membership = self._membership(route, submitter_id)
        state, next_step = self._state_and_next_step(
            route,
            certified,
            queue_can_submit,
            membership,
            participant_team_acl_can_submit,
        )
        submission_team_id: str | None = None
        submission_team_name: str | None = None
        if submission_team and state is ChallengeAccessState.READY:
            try:
                selected_team = self.client.getTeam(submission_team)
            except (SynapseError, ValueError) as error:
                raise ChallengeReadinessError(
                    f"Synapse submission team {submission_team!r} was not found or cannot "
                    f"be used: {error}"
                ) from error
            submission_team_id = str(entity_value(selected_team, "id"))
            submission_team_name = str(entity_value(selected_team, "name", submission_team_id))
            eligibility = self.client.restGET(
                f"/evaluation/{route.evaluation_id}/team/{submission_team_id}/submissionEligibility"
            )
            team_eligibility = eligibility.get("teamEligibility") or {}
            if not bool(team_eligibility.get("isEligible")):
                state = ChallengeAccessState.SUBMISSION_TEAM_INELIGIBLE
                if not bool(team_eligibility.get("isRegistered")):
                    reason_text = "The team is not registered for this challenge."
                elif bool(team_eligibility.get("isQuotaFilled")):
                    reason_text = "The team has reached its submission quota."
                else:
                    reason_text = "Ask the organizer to correct this team's Challenge eligibility."
                next_step = (
                    f"Submission team {submission_team_name} is not eligible for Evaluation "
                    f"{route.evaluation_id}. {reason_text}"
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

    @staticmethod
    def blocked_message(readiness: ChallengeReadiness) -> str:
        return (
            f"Synapse challenge registration is not ready ({readiness.state.value}). "
            f"{readiness.next_step}"
        )

    def evaluation(self) -> Any:
        """Resolve the configured queue by explicit ID or fallback name."""
        if self.config.evaluation_id:
            return self.client.getEvaluation(self.config.evaluation_id)
        return self.client.getEvaluationByName(self.config.queue_name)
