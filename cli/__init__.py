"""Participant-only command-line entry point."""

from __future__ import annotations

import sys


_HELP = """usage: dream participant <command> [options]

Public participant commands:
  submit             validate and submit local participant files
  submit-anvil       submit participant files from GCS on AnVIL
  submission-status  show participant-safe status for one submission

Run `dream participant <command> --help` for command arguments.
This distribution contains participant commands only.
"""


def main(argv: list[str] | None = None) -> int:
    """Run the participant CLI without importing organizer code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(_HELP, end="")
        return 0

    role = arguments.pop(0)
    if role == "organizer":
        raise SystemExit(
            "Organizer commands are not included in the participant package. "
            "Install the organizer package and use `dream-organizer`."
        )
    if role != "participant":
        raise SystemExit(f"Unknown command group {role!r}. Use `dream participant --help`.")
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(_HELP, end="")
        return 0

    from .participant import run

    return run(arguments, prog="dream participant")


__all__ = ["main"]
