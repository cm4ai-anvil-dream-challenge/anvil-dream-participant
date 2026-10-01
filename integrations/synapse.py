"""Public participant Synapse readiness and private submission operations."""

from __future__ import annotations

from typing import Any

import synapseclient
from synapseclient import Project
from synapseclient.core.exceptions import (
    SynapseAuthenticationError as ClientSynapseAuthenticationError,
    SynapseNoCredentialsError,
)

from . import synapse_privacy, synapse_status, synapse_upload
from .synapse_privacy import PrivacyReport, SubmissionPrivacyError
from .synapse_readiness import (
    ChallengeReadiness,
    ChallengeReadinessError,
    SynapseChallengeReadiness,
)
from .synapse_upload import ParticipantSubmissionResult


__all__ = [
    "ChallengeReadiness",
    "ChallengeReadinessError",
    "ParticipantSubmissionResult",
    "ParticipantSynapseGateway",
    "PrivacyReport",
    "SubmissionPrivacyError",
    "SynapseCredentialsError",
]


class SynapseCredentialsError(RuntimeError):
    """Raised when the local Synapse client profile cannot authenticate."""


class ParticipantSynapseGateway:
    """Wrap the Synapse operations used by participant commands."""

    def __init__(self, config: Any):
        try:
            self.client = synapseclient.login(silent=True)
        except (SynapseNoCredentialsError, ClientSynapseAuthenticationError) as error:
            raise SynapseCredentialsError(
                """Synapse authentication failed because the saved Synapse personal
access token is missing or expired. Fix it, then rerun this command:
  1. Run `synapse login`.
  2. When prompted, paste a current Synapse personal access token.
  3. Rerun the DREAM submission command.
If you do not have a token: sign in at https://www.synapse.org, open Account Settings >
Personal Access Tokens, choose Create New Token, select View, Download, and Modify, then
run `synapse login` and paste the new token.
If `synapse login` says your profile is not configured, run `synapse config` once first.
The token is stored in ~/.synapseConfig; never put it in TOML or workflow inputs."""
            ) from error
        self.readiness = SynapseChallengeReadiness(self.client, config)

    def preflight_submission(
        self, parent_id: str, submission_team: str | None = None
    ) -> ChallengeReadiness:
        readiness = self.challenge_readiness(submission_team)
        if not readiness.ready:
            raise ChallengeReadinessError(self.readiness.blocked_message(readiness), readiness)
        self.assert_private_to_submitter(parent_id)
        return readiness

    def challenge_readiness(self, submission_team: str | None = None) -> ChallengeReadiness:
        return self.readiness.challenge_readiness(submission_team)

    def evaluation(self) -> Any:
        """Resolve the configured queue by explicit ID or fallback name."""
        return self.readiness.evaluation()

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

    def read_submission_status(self, submission_id: str):
        """Return only the organizer-managed participant status projection."""
        return synapse_status.read_submission_status(self.client, submission_id)

    def assert_private_to_submitter(self, entity_id: str) -> PrivacyReport:
        """Validate the effective ACL inherited by a Synapse entity."""
        return synapse_privacy.assert_private_to_submitter(self.client, entity_id)

    def upload_and_submit(
        self,
        path: str,
        parent_id: str,
        submission_name: str,
        team: str | None,
    ) -> ParticipantSubmissionResult:
        """Reuse or privately upload a deterministic bundle, then submit it."""
        return synapse_upload.upload_and_submit(
            self.client,
            self.evaluation(),
            path,
            parent_id,
            submission_name,
            team,
            privacy_check=self.assert_private_to_submitter,
        )
