"""Participant-only command-line entry point."""

from __future__ import annotations

import sys


_HELP = """usage: anvil-dream-participant <command> [options]

Public participant commands:
  submit             validate and submit local participant files
  submit-anvil       submit participant files from GCS on AnVIL
  submission-status  show participant-safe status for one submission

Run `anvil-dream-participant <command> --help` for command arguments.
This distribution contains participant commands only.
"""


def main(argv: list[str] | None = None) -> int:
    """Run the participant CLI without importing organizer code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(_HELP, end="")
        return 0

    if arguments[0] == "organizer":
        raise SystemExit(
            "Organizer commands are not included in the participant package. "
            "Install the organizer package and use `dream-organizer`."
        )

    from .participant import run

    return run(arguments, prog="anvil-dream-participant")


__all__ = ["main"]
