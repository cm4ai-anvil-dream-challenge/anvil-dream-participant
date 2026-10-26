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


__all__ = [
    "ChallengeReadiness",
    "ChallengeReadinessError",
    "ParticipantSynapseGateway",
    "PrivacyReport",
    "SubmissionPrivacyError",
    "SynapseCredentialsError",
]


class SynapseCredentialsError(RuntimeError):
    """Raised when the local Synapse client profile cannot authenticate."""


def ParticipantSynapseGateway(config: Any = None):
    """Build the Synapse operations used by participant commands."""
    from types import SimpleNamespace

    if config is None:
        result = SimpleNamespace(client=None, readiness=None)
    else:
        try:
            client = synapseclient.login(silent=True)
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
        result = SimpleNamespace(
            client=client,
            readiness=SynapseChallengeReadiness(client, config),
        )

    def preflight_submission(
        parent_id: str, submission_team: str | None = None
    ) -> ChallengeReadiness:
        readiness = result.challenge_readiness(submission_team)
        if not readiness.ready:
            raise ChallengeReadinessError(result.readiness.blocked_message(readiness), readiness)
        result.assert_private_to_submitter(parent_id)
        return readiness

    def challenge_readiness(submission_team: str | None = None) -> ChallengeReadiness:
        return result.readiness.challenge_readiness(submission_team)

    def evaluation() -> Any:
        """Resolve the configured queue by explicit ID or fallback name."""
        return result.readiness.evaluation()

    def setup_private_submission_project(
        name: str | None = None
    ) -> tuple[Any, PrivacyReport]:
        """Create or reuse a submitter-owned private project and verify its ACL."""
        profile = result.client.getUserProfile()
        project_name = name or (f"CM4AI DREAM private submissions - {profile['ownerId']}")
        project_id = result.client.findEntityId(project_name)
        if project_id:
            project = result.client.get(project_id, downloadFile=False)
        else:
            project = result.client.store(Project(name=project_name))
        report = result.assert_private_to_submitter(str(project.id))
        return project, report

    def read_submission_status(submission_id: str):
        """Return only the organizer-managed participant status projection."""
        return synapse_status.read_submission_status(result.client, submission_id)

    def assert_private_to_submitter(entity_id: str) -> PrivacyReport:
        """Validate the effective ACL inherited by a Synapse entity."""
        return synapse_privacy.assert_private_to_submitter(result.client, entity_id)

    def upload_and_submit(
        path: str,
        parent_id: str,
        submission_name: str,
        team: str | None,
    ) -> dict[str, object]:
        """Reuse or privately upload a deterministic bundle, then submit it."""
        return synapse_upload.upload_and_submit(
            result.client,
            result.evaluation(),
            path,
            parent_id,
            submission_name,
            team,
            privacy_check=result.assert_private_to_submitter,
        )


    result.preflight_submission = preflight_submission
    result.challenge_readiness = challenge_readiness
    result.evaluation = evaluation
    result.setup_private_submission_project = setup_private_submission_project
    result.read_submission_status = read_submission_status
    result.assert_private_to_submitter = assert_private_to_submitter
    result.upload_and_submit = upload_and_submit
    return result