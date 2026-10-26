"""The AnVIL GCS participant submission command."""

from __future__ import annotations

import json

import click
from synapseclient.core.exceptions import SynapseError

from ..challenge import ChallengeProfile
from ..submissions.bundle import BundleValidationError
from ..submissions.operations import (
    AnvilSubmissionRequest,
    SubmissionReadinessError,
    SubmissionStagingError,
    SubmissionUploadError,
    submit_anvil_bundle,
)
from ..workflows.wdl import WorkflowContractError
from .submit import _evaluation_id
from . import load_profile


@click.command("submit-anvil", help="submit from GCS objects in an AnVIL RStudio VM")
@click.option("--wdl", required=True, help="gs://bucket/object path to the participant WDL")
@click.option("--weights", required=True, help="gs://bucket/object path to participant weights")
@click.option(
    "--files",
    "has_files",
    is_flag=True,
    help="supporting GCS objects follow this option and are separated by spaces",
)
@click.argument("file_paths", nargs=-1)
@click.option(
    "--recursive",
    is_flag=True,
    help="read all files below each --files GCS directory, preserving relative paths",
)
@click.option(
    "--evaluation-id",
    callback=lambda _ctx, _param, value: _evaluation_id(value),
    help="Synapse Evaluation ID (defaults to the selected challenge profile)",
)
@click.option("--name", help="optional Synapse submission name")
@click.option("--team", help="optional registered Synapse submission team")
@click.pass_context
def command(
    ctx: click.Context,
    *,
    wdl: str,
    weights: str,
    has_files: bool,
    file_paths: tuple[str, ...],
    recursive: bool,
    evaluation_id: str | None,
    name: str | None,
    team: str | None,
) -> None:
    if file_paths and not has_files:
        raise click.UsageError("GCS file paths must follow --files")
    if has_files and not file_paths:
        raise click.UsageError("--files requires at least one GCS path")
    profile: ChallengeProfile = load_profile(ctx)
    request = AnvilSubmissionRequest(
        profile=profile,
        wdl=wdl,
        weights=weights,
        files=file_paths if has_files else None,
        recursive=recursive,
        evaluation_id_override=evaluation_id,
        name=name,
        team=team,
    )
    try:
        receipt = submit_anvil_bundle(request)
    except (WorkflowContractError, BundleValidationError) as error:
        raise SystemExit(f"submission validation failed: {error}") from error
    except SubmissionStagingError as error:
        raise SystemExit(f"submission staging failed: {error}") from error
    except SubmissionReadinessError as error:
        raise SystemExit(f"participant readiness check failed: {error}") from error
    except SubmissionUploadError as error:
        raise SystemExit(f"participant submission failed: {error}") from error
    except SynapseError as error:
        raise SystemExit(f"participant submission failed: {error}") from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(receipt, indent=2))
