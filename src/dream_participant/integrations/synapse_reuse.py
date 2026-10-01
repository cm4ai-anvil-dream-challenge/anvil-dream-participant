"""Bundle fingerprints and checks for reusing previously uploaded TAR files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any


BUNDLE_SHA256_ANNOTATION = "dreamBundleSha256"


@dataclass(frozen=True)
class SubmissionBundleFingerprint:
    md5: str
    sha256: str
    size_bytes: int

    @classmethod
    def from_path(cls, path: str | Path) -> SubmissionBundleFingerprint:
        from tqdm import tqdm

        md5 = hashlib.md5(usedforsecurity=False)
        sha256 = hashlib.sha256()
        size_bytes = 0
        with Path(path).open("rb") as bundle:
            with tqdm.wrapattr(
                bundle,
                "read",
                total=Path(path).stat().st_size,
                desc="Verifying submission bundle",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
            ) as tracked:
                while chunk := tracked.read(1024 * 1024):
                    md5.update(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)
        return cls(md5.hexdigest(), sha256.hexdigest(), size_bytes)


def entity_value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, dict) else getattr(source, name, default)


def matches_submission_bundle(entity: Any, fingerprint: SubmissionBundleFingerprint) -> bool:
    if str(entity_value(entity, "md5", "")).lower() != fingerprint.md5:
        return False
    try:
        size_bytes = int(entity_value(entity, "fileSize"))
    except (TypeError, ValueError):
        return False
    if size_bytes != fingerprint.size_bytes:
        return False

    annotations = entity_value(entity, "annotations", {}) or {}
    sha256 = annotations.get(BUNDLE_SHA256_ANNOTATION)
    if isinstance(sha256, (list, tuple)):
        if not sha256:
            sha256 = None
        elif len(sha256) == 1:
            sha256 = sha256[0]
        else:
            return False
    return str(sha256).lower() == fingerprint.sha256
