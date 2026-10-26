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
from pydantic import ValidationError

from .._validation import StrictModel
from ..workflows.wdl import (
    ParticipantWorkflow,
    WorkflowContractError,
    validate_container_image,
    validate_workflow_images,
)

SUBMISSION_BUNDLE_SCHEMA_VERSION = 1
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


BundleLimits = dict[str, int]
DEFAULT_BUNDLE_LIMITS: BundleLimits = {
    "max_members": 4096,
    "max_member_bytes": 20 * 1024**3,
    "max_total_bytes": 30 * 1024**3,
    "max_manifest_bytes": 4 * 1024**2,
}


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


def path_value(value: object, field: str) -> str:
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


def runtime_asset_path(archive_path: str) -> str:
    if not archive_path.startswith(ARCHIVE_OTHER_PREFIX):
        raise BundleValidationError("runtime asset paths must be under other/")
    relative = path_value(
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


class _BundleFileWire(StrictModel):
    path: str
    sha256: str
    size_bytes: int


class _IdentityWire(StrictModel):
    email: str
    subject_id: str


class _ManifestWire(StrictModel):
    schema_version: int
    submission_kind: str
    synapse_user_id: str
    anvil_identity: _IdentityWire
    wdl: _BundleFileWire
    weights: _BundleFileWire
    other_files: list[_BundleFileWire]
    container_images: list[str]
    core_fingerprint: str
    full_fingerprint: str
    submission_name: str | None
    submission_team: str | None


def _wire_location(location: tuple[object, ...]) -> tuple[str | None, str]:
    if not location:
        return None, ""
    if (
        location[0] == "other_files"
        and len(location) >= 3
        and isinstance(location[1], int)
    ):
        return f"other_files[{location[1]}]", str(location[2])
    if len(location) == 1:
        return None, str(location[0])
    return str(location[0]), str(location[-1])


def _wire_error(error: ValidationError, field: str) -> BundleValidationError:
    errors = error.errors(include_url=False)
    for item in errors:
        if item["type"] != "model_type":
            continue
        location = item["loc"]
        if not location:
            return BundleValidationError(f"{field} must be an object")
        if (
            location[0] == "other_files"
            and len(location) >= 2
            and isinstance(location[1], int)
        ):
            return BundleValidationError(f"other_files[{location[1]}] must be an object")
        return BundleValidationError(f"{location[0]} must be an object")
    unknown = sorted(
        str(item["loc"][0])
        for item in errors
        if item["type"] == "extra_forbidden" and len(item["loc"]) == 1
    )
    if unknown:
        return BundleValidationError(
            f"unknown {field} field(s): {', '.join(unknown)}"
        )
    missing = sorted(
        str(item["loc"][0])
        for item in errors
        if item["type"] == "missing" and len(item["loc"]) == 1
    )
    if missing:
        return BundleValidationError(
            f"{field} is missing required field(s): {', '.join(missing)}"
        )
    for item in errors:
        location = item["loc"]
        if item["type"] == "list_type" and len(location) == 1:
            return BundleValidationError(f"{location[0]} must be an array")
        if item["type"] == "int_type":
            if location == ("schema_version",):
                return BundleValidationError("schema_version must be an integer")
            if location and location[-1] == "size_bytes":
                return BundleValidationError(
                    "file size_bytes must be a non-negative integer"
                )
        if item["type"] in {"missing", "extra_forbidden"}:
            label, leaf = _wire_location(location)
            prefix = label or field
            if item["type"] == "missing":
                return BundleValidationError(
                    f"{prefix} is missing required field(s): {leaf}"
                )
            return BundleValidationError(f"unknown {prefix} field(s): {leaf}")
    location = next((item["loc"] for item in errors), ())
    label, leaf = _wire_location(location)
    if leaf:
        return BundleValidationError(f"{label or field} has an invalid {leaf}")
    return BundleValidationError(str(error))


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


def _identity(value: object) -> dict[str, str]:
    try:
        raw = _IdentityWire.model_validate(value)
    except ValidationError as error:
        raise _wire_error(error, "anvil_identity") from error
    subject = raw.subject_id.strip()
    if not subject:
        raise BundleValidationError("anvil identity subject_id is required")
    return {"email": _canonical_email(raw.email), "subject_id": subject}


@dataclass(frozen=True)
class BundleFile:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", path_value(self.path, "file path"))
        object.__setattr__(self, "sha256", _digest(self.sha256, "file sha256"))
        object.__setattr__(self, "size_bytes", _size(self.size_bytes, "file size_bytes"))

    @classmethod
    def from_dict(cls, value: object, field: str) -> "BundleFile":
        try:
            raw = _BundleFileWire.model_validate(value)
        except ValidationError as error:
            raise _wire_error(error, field) from error
        return cls(raw.path, raw.sha256, raw.size_bytes)

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class SubmissionBundleManifest:
    schema_version: int
    submission_kind: str
    synapse_user_id: str
    anvil_identity: dict[str, str]
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
        object.__setattr__(self, "anvil_identity", _identity(self.anvil_identity))
        if self.wdl.path != ARCHIVE_WDL:
            raise BundleValidationError(f"wdl.path must be {ARCHIVE_WDL}")
        if self.weights.path != ARCHIVE_WEIGHTS:
            raise BundleValidationError(f"weights.path must be {ARCHIVE_WEIGHTS}")
        paths = [item.path for item in self.other_files]
        if any(not path.startswith(ARCHIVE_OTHER_PREFIX) for path in paths):
            raise BundleValidationError("other file paths must be under other/")
        for path in paths:
            runtime_asset_path(path)
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
        try:
            raw = _ManifestWire.model_validate(value)
        except ValidationError as error:
            raise _wire_error(error, "manifest") from error
        return cls(
            schema_version=raw.schema_version,
            submission_kind=raw.submission_kind,
            synapse_user_id=raw.synapse_user_id,
            anvil_identity={"email": raw.anvil_identity.email, "subject_id": raw.anvil_identity.subject_id},
            wdl=BundleFile.from_dict(raw.wdl.model_dump(), "wdl"),
            weights=BundleFile.from_dict(raw.weights.model_dump(), "weights"),
            other_files=tuple(
                BundleFile.from_dict(item.model_dump(), f"other_files[{index}]")
                for index, item in enumerate(raw.other_files)
            ),
            container_images=tuple(raw.container_images),
            core_fingerprint=raw.core_fingerprint,
            full_fingerprint=raw.full_fingerprint,
            submission_name=raw.submission_name,
            submission_team=raw.submission_team,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "submission_kind": self.submission_kind,
            "synapse_user_id": self.synapse_user_id,
            "anvil_identity": self.anvil_identity,
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

    @property
    def files(self) -> tuple[tuple[str, Path], ...]:
        """The archive member paths and their local source files."""
        return self._files

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
        wdl_sha, wdl_size = file_metadata(wdl_path)
        weights_sha, weights_size = file_metadata(weights_path)
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
                archive_path = f"{ARCHIVE_OTHER_PREFIX}{path_value(relative, 'other file path')}"
                if archive_path in sources or archive_path == ARCHIVE_MANIFEST:
                    raise BundleValidationError(f"archive path collides with reserved path: {archive_path}")
                digest, size = file_metadata(candidate)
                sources[archive_path] = candidate.resolve()
                other_entries.append(BundleFile(archive_path, digest, size))
        provisional = SubmissionBundleManifest(
            schema_version=SUBMISSION_BUNDLE_SCHEMA_VERSION,
            submission_kind=SUBMISSION_BUNDLE_KIND,
            synapse_user_id=synapse_user_id,
            anvil_identity={"email": anvil_email, "subject_id": anvil_subject_id},
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
    def archive_sha256(path: Path, *, progress: bool = False) -> str:
        from tqdm import tqdm

        digest = hashlib.sha256()
        try:
            with Path(path).open("rb") as handle:
                with tqdm.wrapattr(handle, "read", total=Path(path).stat().st_size,
                                   desc="Verifying submission TAR digest", disable=not progress,
                                   unit="B", unit_scale=True, unit_divisor=1024) as tracked:
                    copy_chunks(tracked, digest=digest)
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
                    digest, size = file_metadata(source)
                    expected = self._entry(path)
                    if digest != expected.sha256 or size != expected.size_bytes:
                        raise BundleValidationError(f"source file {path} changed while building the archive")
                    add_path(archive, path, source, size)
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


def copy_chunks(source: BinaryIO, *, destination: BinaryIO | None = None, digest: Any | None = None, limit: int | None = None) -> int:
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


def file_metadata(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            size = copy_chunks(source, digest=digest)
    except OSError as error:
        raise BundleValidationError(f"cannot read regular file: {path}") from error
    return digest.hexdigest(), size


def _add_bytes(archive: tarfile.TarFile, path: str, data: bytes) -> None:
    archive.addfile(_tar_info(path, len(data)), io.BytesIO(data))


def add_path(archive: tarfile.TarFile, path: str, source: Path, size: int) -> None:
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
