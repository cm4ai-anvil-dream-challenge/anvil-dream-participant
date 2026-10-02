"""Shared validation rules for evaluation result artifacts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping


class EvaluationResultContractError(ValueError):
    """A gathered evaluation result or grade violates its contract."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationResultContractError(f"{field} must be a non-empty string")
    return value


def _strict_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationResultContractError(f"{field} must be an integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _strict_int(value, field)
    if result < 1:
        raise EvaluationResultContractError(f"{field} must be positive")
    return result


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationResultContractError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise EvaluationResultContractError(f"{field} must be finite")
    return result


def _nonnegative_number(value: object, field: str) -> float:
    result = _number(value, field)
    if result < 0:
        raise EvaluationResultContractError(f"{field} must be non-negative")
    return result


def _digest(value: object, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise EvaluationResultContractError(f"{field} must be a SHA-256 hex digest")
    return result


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
