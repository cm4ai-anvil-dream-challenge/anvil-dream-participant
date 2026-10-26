"""Run the selected challenge's benchmark operation."""

from __future__ import annotations

from pathlib import Path

import click

from ..challenge import ChallengeProfile
from . import load_profile


@click.command("benchmark", help="benchmark an already merged image-results JSONL file")
@click.option("--image-results", type=click.Path(path_type=Path), required=True)
@click.option("--dataset-id", default="local", show_default=True, help="output dataset label")
@click.option("--comparison-report", type=click.Path(path_type=Path), required=True)
@click.option("--aggregate", type=click.Path(path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="score-result.json output path")
@click.option(
    "--challenge",
    "challenge_reference",
    default=None,
    help="challenge profile for direct module invocation",
)
@click.pass_context
def command(
    ctx: click.Context,
    *,
    image_results: Path,
    dataset_id: str,
    comparison_report: Path,
    aggregate: Path,
    output: Path,
    challenge_reference: str | None,
) -> None:
    profile = load_profile(ctx, challenge_reference)
    if not isinstance(profile, ChallengeProfile):
        raise click.ClickException("the selected challenge is not a valid profile")
    if profile.benchmark is None:
        raise click.ClickException(f"challenge {profile.name!r} does not define a benchmark")
    try:
        profile.benchmark(
            image_results,
            dataset_id=dataset_id,
            comparison_report_path=comparison_report,
            aggregate_path=aggregate,
            score_result_path=output,
        )
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error


def main(argv: list[str] | None = None) -> int:
    result = command.main(
        args=argv,
        prog_name="python -m anvil_dream_participant.cli.benchmark",
        standalone_mode=True,
    )
    return 0 if result is None else int(result)


if __name__ == "__main__":
    raise SystemExit(main())
