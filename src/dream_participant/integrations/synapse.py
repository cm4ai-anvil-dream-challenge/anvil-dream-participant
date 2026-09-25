"""Public participant Synapse readiness and private submission operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import synapseclient
from synapseclient import File, Project
from synapseclient.core.exceptions import (
    SynapseAuthenticationError as ClientSynapseAuthenticationError,
    SynapseError,
    SynapseHTTPError,
    SynapseNoCredentialsError,
)

from dream_participant.submissions.status import ParticipantSubmissionStatus


class _EvaluationConfig(Protocol):
    queue_name: str
    evaluation_id: str | None


CONFIDENTIAL_ACCESS = frozenset({"READ", "DOWNLOAD"})
BUNDLE_SHA256_ANNOTATION = "dreamBundleSha256"


class SubmissionPrivacyError(RuntimeError):
    """Raised before submission when weights would be visible to another principal."""


class SynapseCredentialsError(RuntimeError):
    """Raised when the local Synapse client profile cannot authenticate."""


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
    evaluation_name: str
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
    can_join: bool | None
    membership_approval_required: bool
    has_unmet_access_requirement: bool
    team_accepts_requests: bool


@dataclass(frozen=True)
class ChallengeReadiness:
    route: ChallengeRoute
    submitter_id: str
    submitter_name: str
    certified: bool
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


@dataclass(frozen=True)
class PrivacyReport:
    """Result of inspecting the ACL inherited by a prospective upload."""

    entity_id: str
    benefactor_id: str
    submitter_id: str


@dataclass(frozen=True)
class SubmissionBundleFingerprint:
    md5: str
    sha256: str
    size_bytes: int

    @classmethod
    def from_path(cls, path: str | Path) -> SubmissionBundleFingerprint:
        md5 = hashlib.md5(usedforsecurity=False)
        sha256 = hashlib.sha256()
        size_bytes = 0
        with Path(path).open("rb") as bundle:
            while chunk := bundle.read(1024 * 1024):
                md5.update(chunk)
                sha256.update(chunk)
                size_bytes += len(chunk)
        return cls(md5.hexdigest(), sha256.hexdigest(), size_bytes)


@dataclass(frozen=True)
class ParticipantSubmissionResult:
    submission: Any
    reused: bool


class ParticipantSynapseGateway:
    """Wrap the Synapse operations used by participant commands."""

    def __init__(
        self,
        config: _EvaluationConfig,
    ):
        self.config = config
        try:
            self.client = synapseclient.login(silent=True)
        except (SynapseNoCredentialsError, ClientSynapseAuthenticationError) as error:
            raise SynapseCredentialsError(
                "Synapse authentication failed because the saved Synapse personal "
                "access token is missing or expired. Fix it, then rerun this command:\n"
                "  1. Run `synapse login`.\n"
                "  2. When prompted, paste a current Synapse personal access token.\n"
                "  3. Rerun the DREAM submission command.\n"
                "If you do not have a token: sign in at https://www.synapse.org, open "
                "Account Settings > Personal Access Tokens, choose Create New Token, "
                "select View, Download, and Modify, then run `synapse login` and paste "
                "the new token.\n"
                "If `synapse login` says your profile is not configured, run "
                "`synapse config` once first. The token is stored in "
                "~/.synapseConfig; never put it in TOML or workflow inputs."
            ) from error

    def preflight_submission(
        self, parent_id: str, submission_team: str | None = None
    ) -> ChallengeReadiness:
        readiness = self.challenge_readiness(submission_team)
        if not readiness.ready:
            raise ChallengeReadinessError(self._blocked_message(readiness), readiness)
        self.assert_private_to_submitter(parent_id)
        return readiness

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        return (
            source.get(name, default)
            if isinstance(source, dict)
            else getattr(source, name, default)
        )

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
        evaluation_id = str(self._value(evaluation, "id"))
        content_source_id = str(self._value(evaluation, "contentSource"))
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
            evaluation_name=str(self._value(evaluation, "name", self.config.queue_name)),
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
        can_join = status.get("canJoin")
        return TeamMembershipSnapshot(
            is_member=bool(status.get("isMember")),
            is_registered=registered,
            has_open_invitation=bool(status.get("hasOpenInvitation")),
            has_open_request=bool(status.get("hasOpenRequest")),
            can_join=bool(can_join) if can_join is not None else None,
            membership_approval_required=bool(status.get("membershipApprovalRequired")),
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
        submitter_name = str(profile.get("userName") or profile.get("username") or submitter_id)
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
            submission_team_id = str(self._value(selected_team, "id"))
            submission_team_name = str(self._value(selected_team, "name", submission_team_id))
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
            submitter_name=submitter_name,
            certified=certified,
            queue_can_submit=queue_can_submit,
            membership=membership,
            state=state,
            next_step=next_step,
            participant_team_acl_can_submit=participant_team_acl_can_submit,
            submission_team_id=submission_team_id,
            submission_team_name=submission_team_name,
        )

    @staticmethod
    def _blocked_message(readiness: ChallengeReadiness) -> str:
        return (
            f"Synapse challenge registration is not ready ({readiness.state.value}). "
            f"{readiness.next_step}"
        )

    def evaluation(self) -> Any:
        """Resolve the configured queue by explicit ID or fallback name."""
        if self.config.evaluation_id:
            return self.client.getEvaluation(self.config.evaluation_id)
        return self.client.getEvaluationByName(self.config.queue_name)

    def setup_private_submission_project(
        self, name: str | None = None
    ) -> tuple[Any, PrivacyReport]:
        """Create or reuse a submitter-owned private project and verify its ACL."""
        profile = self.client.getUserProfile()
        project_name = name or (f"CM4AI DREAM private submissions - {profile['ownerId']}")
        project_id = self.client.findEntityId(project_name)
        if project_id:
            project = self.client.get(project_id, downloadFile=False)
        else:
            project = self.client.store(Project(name=project_name))
        report = self.assert_private_to_submitter(str(project.id))
        return project, report

    def read_submission_status(self, submission_id: str) -> ParticipantSubmissionStatus:
        """Return only the organizer-managed participant status projection."""
        identifier = str(submission_id).strip()
        if not identifier:
            raise ValueError("submission_id cannot be empty")
        status = self.client.getSubmissionStatus(identifier)
        return ParticipantSubmissionStatus.from_status(identifier, status)

    def assert_private_to_submitter(self, entity_id: str) -> PrivacyReport:
        """Fail unless only the logged-in user can read or download an entity.

        A newly stored Synapse FileEntity inherits the ACL of its benefactor. We
        inspect that effective ACL rather than merely checking whether the entity
        has local sharing settings. Any other user or team with READ or DOWNLOAD
        would be able to see the submitted weights and is therefore rejected.

        Challenge evaluators are intentionally not added to this ACL. They receive a
        private snapshot through the Evaluation Queue after submission.
        """
        profile = self.client.getUserProfile()
        submitter_id = str(profile["ownerId"])

        benefactor = self.client.restGET(f"/entity/{entity_id}/benefactor")
        benefactor_id = str(benefactor["id"])
        acl = self.client.restGET(f"/entity/{benefactor_id}/acl")

        exposed_to: list[dict[str, Any]] = []
        submitter_permissions: set[str] = set()
        for entry in acl.get("resourceAccess", []):
            principal_id = str(entry.get("principalId"))
            access = set(entry.get("accessType", []))
            confidential_permissions = sorted(CONFIDENTIAL_ACCESS.intersection(access))
            if principal_id == submitter_id:
                submitter_permissions.update(access)
            if principal_id != submitter_id and confidential_permissions:
                exposed_to.append(
                    {
                        "principal_id": principal_id,
                        "permissions": confidential_permissions,
                    }
                )

        if exposed_to:
            details = ", ".join(
                f"{item['principal_id']} ({'/'.join(item['permissions'])})" for item in exposed_to
            )
            raise SubmissionPrivacyError(
                f"Refusing to upload beneath {entity_id}: ACL benefactor "
                f"{benefactor_id} exposes weights to principal(s) {details}. "
                "Use a private Synapse project/folder whose only READ/DOWNLOAD "
                "principal is the logged-in submitter."
            )

        missing = sorted(CONFIDENTIAL_ACCESS.difference(submitter_permissions))
        if missing:
            raise SubmissionPrivacyError(
                f"Refusing to use {entity_id}: logged-in principal {submitter_id} "
                f"does not have direct {'/'.join(missing)} permission on ACL "
                f"benefactor {benefactor_id}. Use a private project owned directly "
                "by the submitter, not access inherited through a team."
            )

        return PrivacyReport(
            entity_id=str(entity_id),
            benefactor_id=benefactor_id,
            submitter_id=submitter_id,
        )

    def upload_and_submit(
        self,
        path: str,
        parent_id: str,
        submission_name: str,
        team: str | None,
    ) -> ParticipantSubmissionResult:
        """Reuse or privately upload a deterministic bundle, then submit it.

        Privacy is checked on the parent before lookup and on the selected
        entity before submission. New uploads are checked again after storage.
        """
        evaluation = self.evaluation()
        parent_privacy = self.assert_private_to_submitter(parent_id)
        fingerprint = SubmissionBundleFingerprint.from_path(path)
        existing_id = self.client.findEntityId(Path(path).name, parent=parent_id)
        entity = None
        reused = False
        if existing_id is not None:
            existing = self.client.get(str(existing_id), downloadFile=False)
            existing_parent = self._value(existing, "parentId")
            existing_creator = self._value(existing, "createdBy")
            if (
                str(self._value(existing, "id")) != str(existing_id)
                or str(existing_parent) != parent_id
                or str(existing_creator) != parent_privacy.submitter_id
            ):
                raise SubmissionPrivacyError(
                    f"Refusing to use existing submission TAR {existing_id}: it does "
                    "not belong to the configured private parent and logged-in submitter."
                )
            self.assert_private_to_submitter(str(existing_id))
            if self._matches_submission_bundle(existing, fingerprint):
                entity = existing
                reused = True

        if entity is None:
            entity = self.client.store(
                File(
                    path=path,
                    parent=parent_id,
                    annotations={BUNDLE_SHA256_ANNOTATION: fingerprint.sha256},
                )
            )
            try:
                self.assert_private_to_submitter(str(entity.id))
            except SubmissionPrivacyError:
                if existing_id is not None and str(entity.id) == str(existing_id):
                    self.client.delete(entity, version=entity.versionNumber)
                else:
                    self.client.delete(entity)
                raise
        kwargs: dict[str, Any] = {"name": submission_name}
        if team:
            kwargs["team"] = team
        submission = self.client.submit(evaluation, entity, **kwargs)
        return ParticipantSubmissionResult(submission=submission, reused=reused)

    @classmethod
    def _matches_submission_bundle(
        cls, entity: Any, fingerprint: SubmissionBundleFingerprint
    ) -> bool:
        if str(cls._value(entity, "md5", "")).lower() != fingerprint.md5:
            return False
        try:
            size_bytes = int(cls._value(entity, "fileSize"))
        except (TypeError, ValueError):
            return False
        if size_bytes != fingerprint.size_bytes:
            return False

        annotations = cls._value(entity, "annotations", {}) or {}
        sha256 = annotations.get(BUNDLE_SHA256_ANNOTATION)
        if isinstance(sha256, (list, tuple)):
            if not sha256:
                sha256 = None
            elif len(sha256) == 1:
                sha256 = sha256[0]
            else:
                return False
        return str(sha256).lower() == fingerprint.sha256
