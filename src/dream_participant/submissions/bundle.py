"""Streaming implementation for deterministic Synapse submission bundles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import tarfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import WDL

from ..workflows.wdl import (
    ParticipantWorkflow,
    WorkflowContractError,
    validate_container_image,
    validate_workflow_images,
)

SUBMISSION_BUNDLE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = SUBMISSION_BUNDLE_SCHEMA_VERSION
SUBMISSION_BUNDLE_KIND = "participant-prediction-bundle"
ARCHIVE_MANIFEST = "manifest.json"
ARCHIVE_WDL = "submission/workflow.wdl"
ARCHIVE_WEIGHTS = "submission/weights"
ARCHIVE_OTHER_PREFIX = "other/"
RUNTIME_ASSETS_FILENAME = "__dream_runtime_assets.tar"
RUNTIME_RESERVED_PATHS = frozenset({RUNTIME_ASSETS_FILENAME, "predictions"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COPY_CHUNK_BYTES = 1024 * 1024


class BundleValidationError(ValueError):
    """A submission bundle violates the participant artifact contract."""


@dataclass(frozen=True)
class BundleLimits:
    max_members: int = 4096
    max_member_bytes: int = 20 * 1024**3
    max_total_bytes: int = 30 * 1024**3
    max_manifest_bytes: int = 4 * 1024**2

    def __post_init__(self) -> None:
        values = (
            self.max_members,
            self.max_member_bytes,
            self.max_total_bytes,
            self.max_manifest_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise ValueError("bundle limits must be positive integers")


DEFAULT_BUNDLE_LIMITS = BundleLimits()


def _canonical_email(value: object) -> str:
    email = str(value or "").strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise BundleValidationError("anvil identity email must be a valid email address")
    return email


def _digest(value: object, field: str) -> str:
    digest = str(value or "").strip().casefold()
    if not _SHA256.fullmatch(digest):
        raise BundleValidationError(f"{field} must be a 64-character SHA-256 digest")
    return digest


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _path(value: object, field: str) -> str:
    text = str(value or "")
    parsed = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or "\\" in text
        or parsed.as_posix() != text
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise BundleValidationError(f"{field} must be a normalized relative POSIX path")
    return text


def _runtime_asset_path(archive_path: str) -> str:
    if not archive_path.startswith(ARCHIVE_OTHER_PREFIX):
        raise BundleValidationError("runtime asset paths must be under other/")
    relative = _path(
        archive_path.removeprefix(ARCHIVE_OTHER_PREFIX),
        "runtime asset path",
    )
    top_level = PurePosixPath(relative).parts[0]
    if top_level in RUNTIME_RESERVED_PATHS:
        raise BundleValidationError(
            f"other files path {relative!r} conflicts with DREAM runtime path "
            f"{top_level!r}"
        )
    return relative


def _strict_object(value: object, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleValidationError(f"{field} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise BundleValidationError(f"unknown {field} field(s): {', '.join(unknown)}")
    if missing:
        raise BundleValidationError(f"{field} is missing required field(s): {', '.join(missing)}")
    return value


def _size(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BundleValidationError(f"{field} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise BundleValidationError(f"{field} must be a non-negative integer") from error
    if result < 0:
        raise BundleValidationError(f"{field} must be a non-negative integer")
    return result


@dataclass(frozen=True)
class BundleIdentity:
    email: str
    subject_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", _canonical_email(self.email))
        subject = str(self.subject_id or "").strip()
        if not subject:
            raise BundleValidationError("anvil identity subject_id is required")
        object.__setattr__(self, "subject_id", subject)

    @classmethod
    def from_dict(cls, value: object) -> "BundleIdentity":
        raw = _strict_object(value, {"email", "subject_id"}, "anvil_identity")
        return cls(raw["email"], raw["subject_id"])

    def as_dict(self) -> dict[str, str]:
        return {"email": self.email, "subject_id": self.subject_id}


@dataclass(frozen=True)
class BundleFile:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path(self.path, "file path"))
        object.__setattr__(self, "sha256", _digest(self.sha256, "file sha256"))
        object.__setattr__(self, "size_bytes", _size(self.size_bytes, "file size_bytes"))

    @classmethod
    def from_dict(cls, value: object, field: str) -> "BundleFile":
        raw = _strict_object(value, {"path", "sha256", "size_bytes"}, field)
        return cls(raw["path"], raw["sha256"], raw["size_bytes"])

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class SubmissionBundleManifest:
    schema_version: int
    submission_kind: str
    synapse_user_id: str
    anvil_identity: BundleIdentity
    wdl: BundleFile
    weights: BundleFile
    other_files: tuple[BundleFile, ...]
    container_images: tuple[str, ...]
    core_fingerprint: str
    full_fingerprint: str
    submission_name: str | None = None
    submission_team: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SUBMISSION_BUNDLE_SCHEMA_VERSION:
            raise BundleValidationError(f"schema_version must be {SUBMISSION_BUNDLE_SCHEMA_VERSION}")
        if self.submission_kind != SUBMISSION_BUNDLE_KIND:
            raise BundleValidationError(f"submission_kind must be {SUBMISSION_BUNDLE_KIND}")
        user_id = str(self.synapse_user_id or "").strip()
        if not user_id.isdigit():
            raise BundleValidationError("synapse_user_id must be a numeric Synapse user ID")
        object.__setattr__(self, "synapse_user_id", user_id)
        if self.wdl.path != ARCHIVE_WDL:
            raise BundleValidationError(f"wdl.path must be {ARCHIVE_WDL}")
        if self.weights.path != ARCHIVE_WEIGHTS:
            raise BundleValidationError(f"weights.path must be {ARCHIVE_WEIGHTS}")
        paths = [item.path for item in self.other_files]
        if any(not path.startswith(ARCHIVE_OTHER_PREFIX) for path in paths):
            raise BundleValidationError("other file paths must be under other/")
        for path in paths:
            _runtime_asset_path(path)
        if len(paths) != len(set(paths)):
            raise BundleValidationError("other file paths must be unique")
        if tuple(paths) != tuple(sorted(paths)):
            raise BundleValidationError("other file entries must be sorted by path")
        images = tuple(str(image).strip() for image in self.container_images)
        if any(not image for image in images):
            raise BundleValidationError("container_images cannot contain empty values")
        try:
            images = tuple(validate_container_image(image) for image in images)
        except WorkflowContractError as error:
            raise BundleValidationError(str(error)) from error
        object.__setattr__(self, "container_images", images)
        _digest(self.core_fingerprint, "core_fingerprint")
        _digest(self.full_fingerprint, "full_fingerprint")

    @classmethod
    def from_dict(cls, value: object) -> "SubmissionBundleManifest":
        if isinstance(value, dict):
            value = {"submission_name": None, "submission_team": None, **value}
        raw = _strict_object(
            value,
            {
                "schema_version", "submission_kind", "synapse_user_id", "anvil_identity",
                "wdl", "weights", "other_files", "container_images", "core_fingerprint",
                "full_fingerprint", "submission_name", "submission_team",
            },
            "manifest",
        )
        if not isinstance(raw["other_files"], list):
            raise BundleValidationError("other_files must be an array")
        if not isinstance(raw["container_images"], list):
            raise BundleValidationError("container_images must be an array")
        try:
            schema_version = int(raw["schema_version"])
        except (TypeError, ValueError) as error:
            raise BundleValidationError("schema_version must be an integer") from error
        return cls(
            schema_version=schema_version,
            submission_kind=str(raw["submission_kind"]),
            synapse_user_id=str(raw["synapse_user_id"]),
            anvil_identity=BundleIdentity.from_dict(raw["anvil_identity"]),
            wdl=BundleFile.from_dict(raw["wdl"], "wdl"),
            weights=BundleFile.from_dict(raw["weights"], "weights"),
            other_files=tuple(BundleFile.from_dict(item, f"other_files[{i}]") for i, item in enumerate(raw["other_files"])),
            container_images=tuple(str(item) for item in raw["container_images"]),
            core_fingerprint=str(raw["core_fingerprint"]),
            full_fingerprint=str(raw["full_fingerprint"]),
            submission_name=(str(raw["submission_name"]) if raw["submission_name"] else None),
            submission_team=(str(raw["submission_team"]) if raw["submission_team"] else None),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "submission_kind": self.submission_kind,
            "synapse_user_id": self.synapse_user_id,
            "anvil_identity": self.anvil_identity.as_dict(),
            "wdl": self.wdl.as_dict(),
            "weights": self.weights.as_dict(),
            "other_files": [item.as_dict() for item in self.other_files],
            "container_images": list(self.container_images),
            "core_fingerprint": self.core_fingerprint,
            "full_fingerprint": self.full_fingerprint,
            "submission_name": self.submission_name,
            "submission_team": self.submission_team,
        }

    def canonical_json(self) -> bytes:
        return _canonical_json(self.as_dict())

    def recompute_fingerprints(self) -> tuple[str, str]:
        core_payload = {
            "wdl": self.wdl.as_dict(),
            "weights": self.weights.as_dict(),
            "container_images": list(self.container_images),
        }
        core = hashlib.sha256(_canonical_json(core_payload)).hexdigest()
        full = hashlib.sha256(_canonical_json({"core_fingerprint": core, "other_files": [item.as_dict() for item in self.other_files]})).hexdigest()
        return core, full

    def validate_fingerprints(self) -> None:
        core, full = self.recompute_fingerprints()
        if core != self.core_fingerprint:
            raise BundleValidationError("core_fingerprint does not match manifest contents")
        if full != self.full_fingerprint:
            raise BundleValidationError("full_fingerprint does not match manifest contents")

@dataclass(frozen=True)
class SubmissionBundle:
    manifest: SubmissionBundleManifest
    _files: tuple[tuple[str, Path], ...]

    @classmethod
    def create(
        cls,
        *,
        wdl: Path,
        weights: Path,
        other_files: Path | None,
        synapse_user_id: str,
        anvil_email: str,
        anvil_subject_id: str,
        submission_name: str | None = None,
        submission_team: str | None = None,
    ) -> "SubmissionBundle":
        wdl_path = _regular_path(wdl, "WDL")
        weights_path = _regular_path(weights, "weights")
        wdl_sha, wdl_size = _file_metadata(wdl_path)
        weights_sha, weights_size = _file_metadata(weights_path)
        images = _extract_container_images(wdl_path)
        sources: dict[str, Path] = {ARCHIVE_WDL: wdl_path, ARCHIVE_WEIGHTS: weights_path}
        other_entries: list[BundleFile] = []
        if other_files is not None:
            directory = _regular_directory(other_files)
            for candidate in sorted(directory.rglob("*")):
                relative = candidate.relative_to(directory).as_posix()
                if candidate.is_symlink():
                    raise BundleValidationError(f"other files must not contain symbolic links: {relative}")
                if candidate.is_dir():
                    continue
                if not _is_regular(candidate):
                    raise BundleValidationError(f"other files contains a non-regular entry: {relative}")
                archive_path = f"{ARCHIVE_OTHER_PREFIX}{_path(relative, 'other file path')}"
                if archive_path in sources or archive_path == ARCHIVE_MANIFEST:
                    raise BundleValidationError(f"archive path collides with reserved path: {archive_path}")
                digest, size = _file_metadata(candidate)
                sources[archive_path] = candidate.resolve()
                other_entries.append(BundleFile(archive_path, digest, size))
        provisional = SubmissionBundleManifest(
            schema_version=SUBMISSION_BUNDLE_SCHEMA_VERSION,
            submission_kind=SUBMISSION_BUNDLE_KIND,
            synapse_user_id=synapse_user_id,
            anvil_identity=BundleIdentity(anvil_email, anvil_subject_id),
            wdl=BundleFile(ARCHIVE_WDL, wdl_sha, wdl_size),
            weights=BundleFile(ARCHIVE_WEIGHTS, weights_sha, weights_size),
            other_files=tuple(sorted(other_entries, key=lambda item: item.path)),
            container_images=tuple(images),
            core_fingerprint="0" * 64,
            full_fingerprint="0" * 64,
            submission_name=submission_name,
            submission_team=submission_team,
        )
        core, full = provisional.recompute_fingerprints()
        manifest = replace(
            provisional,
            core_fingerprint=core,
            full_fingerprint=full,
        )
        manifest.validate_fingerprints()
        ordered_paths = (ARCHIVE_WDL, ARCHIVE_WEIGHTS, *sorted(path for path in sources if path.startswith(ARCHIVE_OTHER_PREFIX)))
        return cls(manifest, tuple((path, sources[path]) for path in ordered_paths))

    @staticmethod
    def archive_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with Path(path).open("rb") as handle:
                _copy_chunks(handle, digest=digest)
        except OSError as error:
            raise BundleValidationError(f"cannot read bundle archive: {path}") from error
        return digest.hexdigest()

    def write_archive(self, destination: Path) -> Path:
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as output, tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                _add_bytes(archive, ARCHIVE_MANIFEST, self.manifest.canonical_json())
                for path, source in self._files:
                    digest, size = _file_metadata(source)
                    expected = self._entry(path)
                    if digest != expected.sha256 or size != expected.size_bytes:
                        raise BundleValidationError(f"source file {path} changed while building the archive")
                    _add_path(archive, path, source, size)
        except BundleValidationError:
            raise
        except (OSError, tarfile.TarError) as error:
            raise BundleValidationError(f"could not write submission archive: {target}") from error
        return target


    def _entry(self, path: str) -> BundleFile:
        for entry in (self.manifest.wdl, self.manifest.weights, *self.manifest.other_files):
            if entry.path == path:
                return entry
        raise BundleValidationError(f"bundle does not contain {path}")


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _regular_path(path: Path, label: str) -> Path:
    source = Path(path).expanduser()
    if source.is_symlink() or not _is_regular(source):
        raise BundleValidationError(f"{label} must be a regular file and not a symbolic link: {source}")
    return source.resolve()


def _regular_directory(path: Path) -> Path:
    directory = Path(path).expanduser()
    if directory.is_symlink() or not directory.is_dir():
        raise BundleValidationError(f"other files must be a regular directory: {directory}")
    return directory.resolve()


def _copy_chunks(source: BinaryIO, *, destination: BinaryIO | None = None, digest: Any | None = None, limit: int | None = None) -> int:
    total = 0
    while True:
        chunk = source.read(_COPY_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if limit is not None and total > limit:
            raise BundleValidationError("archive member exceeds the configured size limit")
        if digest is not None:
            digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return total


def _file_metadata(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            size = _copy_chunks(source, digest=digest)
    except OSError as error:
        raise BundleValidationError(f"cannot read regular file: {path}") from error
    return digest.hexdigest(), size


def _add_bytes(archive: tarfile.TarFile, path: str, data: bytes) -> None:
    archive.addfile(_tar_info(path, len(data)), io.BytesIO(data))


def _add_path(archive: tarfile.TarFile, path: str, source: Path, size: int) -> None:
    try:
        with source.open("rb") as handle:
            archive.addfile(_tar_info(path, size), handle)
    except OSError as error:
        raise BundleValidationError(f"cannot add bundle member: {path}") from error


def _tar_info(path: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(path)
    info.size = size
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _extract_container_images(wdl: Path) -> tuple[str, ...]:
    try:
        document = WDL.load(str(wdl))
        images = validate_workflow_images(
            ParticipantWorkflow("bundle", tuple(document.tasks))
        )
    except Exception as error:
        raise BundleValidationError(str(error)) from error
    if not images:
        raise BundleValidationError("WDL must specify at least one runtime.docker image")
    return images
