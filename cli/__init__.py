"""Public participant command-line entry point."""

from __future__ import annotations


DEFAULT_CHALLENGE_REFERENCE = "anvil_dream_participant.challenges.hpa.profile:PROFILE"


def load_profile(ctx):
    """Resolve the root challenge selection only inside an invoked command."""
    import click

    from ..challenge import load_challenge

    root_obj = ctx.find_root().obj
    root_reference = (
        root_obj.get("challenge_reference")
        if isinstance(root_obj, dict)
        else None
    )
    if root_reference is not None:
        reference = root_reference
    else:
        reference = DEFAULT_CHALLENGE_REFERENCE
    try:
        return load_challenge(reference)
    except (ImportError, ValueError) as error:
        raise click.BadParameter(str(error), param_hint="--challenge") from error


def main(argv: list[str] | None = None) -> int:
    """Run the participant CLI without importing it on library import."""
    from ..progress import close_progress
    from ..integrations.terra import TerraApiError
    from .root import cli

    try:
        cli.main(args=argv, prog_name="anvil-dream-participant", standalone_mode=True)
    except SystemExit as error:
        close_progress(failed=error.code not in (None, 0))
        raise
    except TerraApiError as error:
        close_progress(failed=True)
        raise SystemExit(f"Terra operation failed: {error}") from None
    except ValueError as error:
        close_progress(failed=True)
        raise SystemExit(str(error)) from None
    except BaseException:
        close_progress(failed=True)
        raise
    close_progress()
    return 0


__all__ = ["main"]
