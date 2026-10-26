"""Participant submission operations independent of CLI argument objects."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from synapseclient.core.exceptions import SynapseError

from ..challenge import ChallengeProfile
from ..config import (
    ParticipantConfig,
    participant_config_for_evaluation,
    require_no_placeholders,
)
from ..integrations.container_registry import verify_public_container_image
from ..integrations.synapse import (
    ChallengeReadinessError,
    ParticipantSynapseGateway,
    SubmissionPrivacyError,
    SynapseCredentialsError,
)
from ..integrations.terra import AnvilIdentity, ParticipantTerraClient
from ..progress import progress
from .bundle import DEFAULT_BUNDLE_LIMITS, BundleValidationError, SubmissionBundle
from .gcs_staging import (
    GcsStagingError,
    parse_gcs_submission_sources,
    stage_gcs_submission_files,
)


class SubmissionReadinessError(RuntimeError):
    """The private submission project or Synapse readiness check failed."""


class SubmissionUploadError(RuntimeError):
    """Synapse did not accept the private submission bundle."""


class SubmissionStagingError(RuntimeError):
    """An AnVIL source object could not be staged locally."""


@dataclass(frozen=True)
class SubmissionRequest:
    profile: ChallengeProfile
    wdl: Path
    weights: Path
    anvil_email: str
    evaluation_id_override: str | None = None
    other_files: Path | None = None
    name: str | None = None
    team: str | None = None


@dataclass(frozen=True)
class AnvilSubmissionRequest:
    profile: ChallengeProfile
    wdl: str
    weights: str
    files: tuple[str, ...] | None
    recursive: bool
    evaluation_id_override: str | None = None
    name: str | None = None
    team: str | None = None


@dataclass(frozen=True)
class SubmissionStatusRequest:
    profile: ChallengeProfile
    submission_id: str


def _participant_config(
    profile: ChallengeProfile,
    evaluation_id_override: str | None,
) -> ParticipantConfig:
    evaluation_id = evaluation_id_override or profile.evaluation_id
    queue_name = profile.queue_name if evaluation_id == profile.evaluation_id else None
    return participant_config_for_evaluation(evaluation_id, queue_name=queue_name)


def submit_bundle(
    request: SubmissionRequest,
    *,
    identity: AnvilIdentity | None = None,
) -> dict[str, object]:
    request.profile.validate_workflow(request.wdl)

    config = _participant_config(request.profile, request.evaluation_id_override)
    require_no_placeholders(config["synapse"])
    if identity is None:
        identity = ParticipantTerraClient(config["terra"]).authenticated_anvil_identity(
            request.anvil_email
        )
    synapse = ParticipantSynapseGateway(config["synapse"])
    parent_id = str(config["synapse"].get("submission_parent_id") or "").strip()
    try:
        if not parent_id:
            project, _ = synapse.setup_private_submission_project()
            parent_id = str(
                project.get("id") if isinstance(project, dict) else getattr(project, "id", "")
            ).strip()
            if not parent_id:
                raise SubmissionPrivacyError(
                    "Synapse created a private submission project without returning its ID"
                )
        readiness = synapse.preflight_submission(parent_id, request.team)
    except (
        ChallengeReadinessError,
        SubmissionPrivacyError,
        SynapseCredentialsError,
        SynapseError,
    ) as error:
        raise SubmissionReadinessError(str(error)) from error

    bundle = SubmissionBundle.create(
        wdl=request.wdl,
        weights=request.weights,
        other_files=request.other_files,
        synapse_user_id=readiness.submitter_id,
        anvil_email=identity.email,
        anvil_subject_id=identity.subject_id,
        submission_name=request.name,
        submission_team=request.team,
    )
    if len(bundle.manifest.canonical_json()) > DEFAULT_BUNDLE_LIMITS["max_manifest_bytes"]:
        raise BundleValidationError("submission manifest exceeds the configured size limit")
    for image in bundle.manifest.container_images:
        progress(f"Checking anonymous access and linux/amd64 support for {image}")
        verify_public_container_image(image)

    submission_name = request.name or "participant-prediction"
    progress("Submitting the private deterministic bundle to Synapse")
    with tempfile.TemporaryDirectory(prefix="dream-submission-bundle-") as directory:
        archive = Path(directory) / "submission.tar"
        bundle.write_archive(archive)
        try:
            result = synapse.upload_and_submit(
                str(archive), parent_id, submission_name, request.team
            )
        except (SubmissionPrivacyError, SynapseError) as error:
            raise SubmissionUploadError(str(error)) from error

    progress(
        "Reused the existing private submission TAR"
        if result["reused"]
        else "Uploaded a new private submission TAR"
    )
    submission = result["submission"]
    return {
        "submission_id": str(submission.id),
        "submission_name": submission_name,
        "entity_id": str(submission.entityId),
        "status": "RECEIVED",
        "synapse_user_id": readiness.submitter_id,
        "anvil_email": identity.email,
        "core_fingerprint": bundle.manifest.core_fingerprint,
        "full_fingerprint": bundle.manifest.full_fingerprint,
        "tar_reused": result["reused"],
    }


def submit_anvil_bundle(request: AnvilSubmissionRequest) -> dict[str, object]:
    sources = parse_gcs_submission_sources(
        request.wdl,
        request.weights,
        list(request.files) if request.files is not None else None,
        recursive=request.recursive,
    )

    config = _participant_config(request.profile, request.evaluation_id_override)
    terra = ParticipantTerraClient(config["terra"])
    identity = terra.authenticated_anvil_identity_from_sam()
    progress("Staging generation-pinned GCS submission files")
    with tempfile.TemporaryDirectory(prefix="dream-anvil-submission-sources-") as directory:
        try:
            staged = stage_gcs_submission_files(
                sources,
                Path(directory),
                credentials=terra.credentials,
                project=terra.google_project,
            )
        except GcsStagingError as error:
            raise SubmissionStagingError(str(error)) from error
        return submit_bundle(
            SubmissionRequest(
                profile=request.profile,
                wdl=staged["wdl"],
                weights=staged["weights"],
                anvil_email=identity.email,
                evaluation_id_override=request.evaluation_id_override,
                other_files=staged["other_files"],
                name=request.name,
                team=request.team,
            ),
            identity=identity,
        )


def read_submission_status(request: SubmissionStatusRequest) -> dict[str, object]:
    config = participant_config_for_evaluation(
        request.profile.evaluation_id,
        queue_name=request.profile.queue_name,
    )
    require_no_placeholders(config["synapse"])
    status = ParticipantSynapseGateway(config["synapse"]).read_submission_status(
        request.submission_id
    )
    return status.as_dict()
