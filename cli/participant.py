"""Participant commands for authored-WDL submissions."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from synapseclient.core.exceptions import SynapseError

from dream_participant.config import (
    PARTICIPANT_EVALUATION_ID,
    ParticipantConfig,
    participant_config_for_evaluation,
    require_no_placeholders,
)
from dream_participant.submissions.bundle import (
    DEFAULT_BUNDLE_LIMITS,
    BundleValidationError,
    SubmissionBundle,
)
from dream_participant.submissions.gcs_staging import (
    GcsStagingError,
    GcsSubmissionSources,
    stage_gcs_submission_files,
)
from dream_participant.workflows.wdl import (
    WorkflowContractError,
    parse_participant_workflow,
)
from dream_participant.integrations.synapse import (
    ChallengeReadinessError,
    ParticipantSynapseGateway,
    SubmissionPrivacyError,
    SynapseCredentialsError,
)
from dream_participant.integrations.terra import (
    AnvilIdentity,
    ParticipantTerraClient,
    TerraApiError,
)
from dream_participant.integrations.container_registry import (
    ContainerRegistryError,
    verify_public_container_image,
)
from dream_participant.progress import close_progress, progress


def _evaluation_id(value: str) -> str:
    if not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("evaluation ID must be a positive number")
    return value


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Validate and submit participant-authored WDL workflows"
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{submit,submit-anvil,submission-status}",
    )
    submit = commands.add_parser(
        "submit",
        help="validate and submit participant artifacts",
        description=(
            "Authenticate the registered AnVIL account, validate the public container image, "
            "package the WDL, weights, and optional files, and submit one private TAR entity "
            "to the Synapse Evaluation Queue."
        ),
    )
    submit.add_argument(
        "--wdl",
        type=Path,
        required=True,
        help="participant WDL file",
    )
    submit.add_argument(
        "--weights",
        required=True,
        type=Path,
        help="participant weights file",
    )
    submit.add_argument(
        "--other-files",
        type=Path,
        help=(
            "optional directory whose files become relative to the prediction task's "
            "working directory"
        ),
    )
    submit.add_argument(
        "--anvil-email",
        required=True,
        help="email of the authenticated, registered AnVIL account",
    )
    submit.add_argument(
        "--evaluation-id",
        type=_evaluation_id,
        default=PARTICIPANT_EVALUATION_ID,
        help=f"Synapse Evaluation ID (default: {PARTICIPANT_EVALUATION_ID})",
    )
    submit.add_argument("--name", help="optional Synapse submission name")
    submit.add_argument("--team", help="optional registered Synapse submission team")
    submit_anvil = commands.add_parser(
        "submit-anvil",
        help="submit from GCS objects in an AnVIL RStudio VM",
        description=(
            "Use the authenticated AnVIL VM identity to read the WDL, weights, and optional "
            "GCS files, stage them locally, then build and submit one private TAR to Synapse."
        ),
    )
    submit_anvil.add_argument(
        "--wdl",
        required=True,
        help="gs://bucket/object path to the participant WDL",
    )
    submit_anvil.add_argument(
        "--weights",
        required=True,
        help="gs://bucket/object path to participant weights",
    )
    submit_anvil.add_argument(
        "--files",
        nargs="+",
        metavar="GS_PATH",
        help="supporting GCS objects, separated by spaces; use --recursive for directories",
    )
    submit_anvil.add_argument(
        "--recursive",
        action="store_true",
        help="read all files below each --files GCS directory, preserving relative paths",
    )
    submit_anvil.add_argument(
        "--evaluation-id",
        type=_evaluation_id,
        default=PARTICIPANT_EVALUATION_ID,
        help=f"Synapse Evaluation ID (default: {PARTICIPANT_EVALUATION_ID})",
    )
    submit_anvil.add_argument("--name", help="optional Synapse submission name")
    submit_anvil.add_argument("--team", help="optional registered Synapse submission team")
    status = commands.add_parser(
        "submission-status",
        help="show the participant-safe status of one submission",
    )
    status.add_argument("--submission-id", required=True)
    return parser


def _submit_bundle(
    config: ParticipantConfig,
    args: argparse.Namespace,
    *,
    identity: AnvilIdentity | None = None,
) -> int:
    try:
        parse_participant_workflow(args.wdl, require_exact_participant_shape=True)
    except WorkflowContractError as error:
        raise SystemExit(f"submission validation failed: {error}") from error

    require_no_placeholders(config.synapse)
    if identity is None:
        identity = ParticipantTerraClient(config.terra).authenticated_anvil_identity(
            args.anvil_email
        )
    synapse = ParticipantSynapseGateway(config.synapse)
    parent_id = str(config.synapse.submission_parent_id or "").strip()
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
        readiness = synapse.preflight_submission(parent_id, args.team)
    except (
        ChallengeReadinessError,
        SubmissionPrivacyError,
        SynapseCredentialsError,
        SynapseError,
    ) as error:
        raise SystemExit(f"participant readiness check failed: {error}") from error
    try:
        bundle = SubmissionBundle.create(
            wdl=args.wdl,
            weights=args.weights,
            other_files=args.other_files,
            synapse_user_id=readiness.submitter_id,
            anvil_email=identity.email,
            anvil_subject_id=identity.subject_id,
            submission_name=args.name,
            submission_team=args.team,
        )
        if len(bundle.manifest.canonical_json()) > DEFAULT_BUNDLE_LIMITS.max_manifest_bytes:
            raise BundleValidationError("submission manifest exceeds the configured size limit")
        for image in bundle.manifest.container_images:
            progress(f"Checking anonymous access and linux/amd64 support for {image}")
            verify_public_container_image(image)
    except (BundleValidationError, ContainerRegistryError, WorkflowContractError) as error:
        raise SystemExit(f"submission validation failed: {error}") from error
    submission_name = args.name or "participant-prediction"
    progress("Submitting the private deterministic bundle to Synapse")
    with tempfile.TemporaryDirectory(prefix="dream-submission-bundle-") as directory:
        archive = Path(directory) / "submission.tar"
        bundle.write_archive(archive)
        try:
            result = synapse.upload_and_submit(str(archive), parent_id, submission_name, args.team)
        except (SubmissionPrivacyError, SynapseError) as error:
            raise SystemExit(f"participant submission failed: {error}") from error
    progress(
        "Reused the existing private submission TAR"
        if result.reused
        else "Uploaded a new private submission TAR"
    )
    submission = result.submission
    print(
        json.dumps(
            {
                "submission_id": str(submission.id),
                "submission_name": submission_name,
                "entity_id": str(submission.entityId),
                "status": "RECEIVED",
                "synapse_user_id": readiness.submitter_id,
                "anvil_email": identity.email,
                "core_fingerprint": bundle.manifest.core_fingerprint,
                "full_fingerprint": bundle.manifest.full_fingerprint,
                "tar_reused": result.reused,
            },
            indent=2,
        )
    )
    return 0


def _submit_anvil_bundle(config: ParticipantConfig, args: argparse.Namespace) -> int:
    try:
        sources = GcsSubmissionSources.parse(
            args.wdl, args.weights, args.files, recursive=args.recursive
        )
    except GcsStagingError as error:
        raise SystemExit(f"submission validation failed: {error}") from error

    terra = ParticipantTerraClient(config.terra)
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
            raise SystemExit(f"submission staging failed: {error}") from error
        staged_args = argparse.Namespace(**vars(args))
        staged_args.command = "submit"
        staged_args.wdl = staged.wdl
        staged_args.weights = staged.weights
        staged_args.other_files = staged.other_files
        return _submit_bundle(config, staged_args, identity=identity)


def _run(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    try:
        result = _run_impl(argv, prog=prog)
    except SystemExit as error:
        close_progress(failed=error.code not in (None, 0))
        raise
    except BaseException:
        close_progress(failed=True)
        raise
    close_progress()
    return result


def _run_impl(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    args = build_parser(prog).parse_args(argv)
    if args.command == "submit":
        return _submit_bundle(participant_config_for_evaluation(args.evaluation_id), args)
    if args.command == "submit-anvil":
        return _submit_anvil_bundle(participant_config_for_evaluation(args.evaluation_id), args)
    if args.command == "submission-status":
        config = participant_config_for_evaluation()
        require_no_placeholders(config.synapse)
        try:
            status = ParticipantSynapseGateway(config.synapse).read_submission_status(
                args.submission_id
            )
        except (SynapseCredentialsError, SynapseError, ValueError) as error:
            raise SystemExit(f"submission status lookup failed: {error}") from error
        print(json.dumps(status.as_dict(), indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled participant command: {args.command}")


def run(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    try:
        return _run(argv, prog=prog)
    except TerraApiError as error:
        raise SystemExit(f"Terra operation failed: {error}") from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
