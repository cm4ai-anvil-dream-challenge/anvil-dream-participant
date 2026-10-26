"""The local participant submission command."""

from __future__ import annotations

import json
from pathlib import Path

import click
from synapseclient.core.exceptions import SynapseError

from ..challenge import ChallengeProfile
from ..submissions.bundle import BundleValidationError
from ..submissions.operations import (
    SubmissionReadinessError,
    SubmissionRequest,
    SubmissionUploadError,
    submit_bundle,
)
from ..workflows.wdl import WorkflowContractError
from . import load_profile


def _evaluation_id(value: str | None) -> str | None:
    if value is not None and (not value.isdecimal() or int(value) <= 0):
        raise click.BadParameter("evaluation ID must be a positive number")
    return value


@click.command("submit", help="validate and submit participant artifacts")
@click.option("--wdl", type=click.Path(path_type=Path), required=True, help="participant WDL file")
@click.option("--weights", type=click.Path(path_type=Path), required=True, help="participant weights file")
@click.option(
    "--other-files",
    type=click.Path(path_type=Path),
    help="optional directory whose files become relative to the prediction task's working directory",
)
@click.option("--anvil-email", required=True, help="email of the authenticated, registered AnVIL account")
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
    wdl: Path,
    weights: Path,
    other_files: Path | None,
    anvil_email: str,
    evaluation_id: str | None,
    name: str | None,
    team: str | None,
) -> None:
    profile: ChallengeProfile = load_profile(ctx)
    request = SubmissionRequest(
        profile=profile,
        wdl=wdl,
        weights=weights,
        anvil_email=anvil_email,
        evaluation_id_override=evaluation_id,
        other_files=other_files,
        name=name,
        team=team,
    )
    try:
        receipt = submit_bundle(request)
    except (WorkflowContractError, BundleValidationError) as error:
        raise SystemExit(f"submission validation failed: {error}") from error
    except SubmissionReadinessError as error:
        raise SystemExit(f"participant readiness check failed: {error}") from error
    except SubmissionUploadError as error:
        raise SystemExit(f"participant submission failed: {error}") from error
    except SynapseError as error:
        raise SystemExit(f"participant submission failed: {error}") from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(receipt, indent=2))
