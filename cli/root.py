"""Participant-only Click command group."""

from __future__ import annotations

import click

from . import DEFAULT_CHALLENGE_REFERENCE
from .benchmark import command as benchmark_command
from .submission_status import command as submission_status_command
from .submit import command as submit_command
from .submit_anvil import command as submit_anvil_command


class ParticipantGroup(click.Group):
    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name == "organizer":
            raise click.UsageError(
                "Organizer commands are not included in the participant package. "
                "Install the organizer package and use `dream-organizer`."
            )
        return super().get_command(ctx, cmd_name)


@click.group(cls=ParticipantGroup, invoke_without_command=True)
@click.option(
    "--challenge",
    "challenge_reference",
    default=DEFAULT_CHALLENGE_REFERENCE,
    show_default=True,
    help="challenge profile as module:attribute",
)
@click.pass_context
def cli(ctx: click.Context, challenge_reference: str) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help(), nl=False)
        return
    ctx.ensure_object(dict)
    ctx.obj["challenge_reference"] = challenge_reference


cli.add_command(submit_command)
cli.add_command(submit_anvil_command)
cli.add_command(submission_status_command)
cli.add_command(benchmark_command)
