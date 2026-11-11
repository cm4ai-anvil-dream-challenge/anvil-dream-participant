"""Compatibility entrypoint for participant CLI callers."""

from __future__ import annotations

from . import main


def run(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    """Run the current CLI through the published Python entrypoint."""
    try:
        return main(argv, prog=prog)
    except SystemExit as error:
        if error.code in (None, 0):
            return 0
        raise


if __name__ == "__main__":
    raise SystemExit(run())
