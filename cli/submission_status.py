"""The participant-safe Synapse submission status command."""

from __future__ import annotations

import json

import click
from synapseclient.core.exceptions import SynapseError

from ..challenge import ChallengeProfile
from ..integrations.synapse import SynapseCredentialsError
from ..submissions.operations import (
    SubmissionStatusRequest,
    read_submission_status,
)
from . import load_profile


@click.command("submission-status", help="show participant-safe status for one submission")
@click.option("--submission-id", required=True)
@click.pass_context
def command(ctx: click.Context, *, submission_id: str) -> None:
    profile: ChallengeProfile = load_profile(ctx)
    try:
        status = read_submission_status(
            SubmissionStatusRequest(profile=profile, submission_id=submission_id)
        )
    except (SynapseCredentialsError, SynapseError, ValueError) as error:
        raise SystemExit(f"submission status lookup failed: {error}") from error
    click.echo(json.dumps(status, indent=2, sort_keys=True))
