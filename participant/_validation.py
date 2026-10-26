"""Strict parsing rules for untrusted external documents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Reject coercion, unknown keys, and mutation at an external boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


__all__ = ["StrictModel"]
