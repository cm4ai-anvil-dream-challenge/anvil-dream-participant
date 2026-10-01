"""Validate and stream Google Cloud Storage submission inputs to local staging files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from google.cloud import storage

from .bundle import DEFAULT_BUNDLE_LIMITS, BundleLimits, RUNTIME_RESERVED_PATHS


class GcsStagingError(ValueError):
    """A Google Cloud Storage submission source cannot be staged safely."""


@dataclass(frozen=True)
class GcsObject:
    bucket: str
    name: str

    @classmethod
    def parse(cls, value: str, field: str) -> GcsObject:
        bucket, name = _uri_parts(value, field)
        if name.endswith("/"):
            raise GcsStagingError(f"{field} must name a GCS object, not a prefix")
        _relative_path(name, field)
        return cls(bucket, name)

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.name}"


@dataclass(frozen=True)
class GcsPrefix:
    bucket: str
    name: str

    @classmethod
    def parse(cls, value: str, field: str) -> GcsPrefix:
        bucket, name = _uri_parts(value, field)
        if not name.endswith("/"):
            raise GcsStagingError(f"{field} must end with '/' to identify a GCS prefix")
        _relative_path(name[:-1], field)
        return cls(bucket, name)

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.name}"


@dataclass(frozen=True)
class GcsSubmissionSources:
    wdl: GcsObject
    weights: GcsObject
    other_files: GcsPrefix | None

    @classmethod
    def parse(
        cls,
        wdl: str,
        weights: str,
        other_files: str | None,
    ) -> GcsSubmissionSources:
        return cls(
            wdl=GcsObject.parse(wdl, "--wdl"),
            weights=GcsObject.parse(weights, "--weights"),
            other_files=(
                GcsPrefix.parse(other_files, "--other-files")
                if other_files is not None
                else None
            ),
        )


@dataclass(frozen=True)
class StagedGcsSubmissionFiles:
    wdl: Path
    weights: Path
    other_files: Path | None


@dataclass(frozen=True)
class _ObjectVersion:
    source: GcsObject
    generation: int
    size_bytes: int


def _uri_parts(value: str, field: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise GcsStagingError(f"{field} must be a gs://bucket/object URI")
    if "\\" in value or "?" in value or "#" in value:
        raise GcsStagingError(f"{field} must be a plain gs://bucket/object URI")
    try:
        parsed = urlsplit(value)
        name = unquote(parsed.path.removeprefix("/"), errors="strict")
    except (UnicodeDecodeError, ValueError) as error:
        raise GcsStagingError(f"{field} is not a valid GCS URI") from error
    bucket = parsed.netloc
    if (
        parsed.scheme != "gs"
        or not bucket
        or any(char in bucket for char in "/@:")
        or not name
    ):
        raise GcsStagingError(f"{field} must be a gs://bucket/object URI")
    return bucket, name


def _relative_path(value: str, field: str) -> str:
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or PurePosixPath(value).as_posix() != value
    ):
        raise GcsStagingError(f"{field} contains an unsafe GCS object path")
    return value


def stage_gcs_submission_files(
    sources: GcsSubmissionSources,
    destination: Path,
    *,
    credentials: Any,
    project: str | None,
    limits: BundleLimits = DEFAULT_BUNDLE_LIMITS,
) -> StagedGcsSubmissionFiles:
    """Download a bounded, generation-pinned source set under destination."""
    root = Path(destination).expanduser()
    if root.is_symlink():
        raise GcsStagingError("GCS staging destination must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    try:
        client = storage.Client(credentials=credentials, project=project)
    except Exception as error:
        raise GcsStagingError(
            "could not create a GCS client from the active AnVIL credentials; run this "
            "command in the authenticated RStudio Bioconductor VM and confirm that the "
            "VM identity can read the source bucket"
        ) from error

    fixed = [
        (_load_object(client, sources.wdl), "workflow.wdl"),
        (_load_object(client, sources.weights), "weights"),
    ]
    optional = _list_prefix(client, sources.other_files, limits) if sources.other_files else []
    selected = [*fixed, *optional]
    _validate_limits(selected, limits)
    _validate_tree_paths([relative for _, relative in optional])

    wdl_path = root / "workflow.wdl"
    weights_path = root / "weights"
    other_root = root / "other" if sources.other_files is not None else None
    for version, relative in selected:
        target = root / relative if relative in {"workflow.wdl", "weights"} else root / "other" / relative
        _download_object(client, version, target, root)
    return StagedGcsSubmissionFiles(wdl_path, weights_path, other_root)


def _load_object(client: Any, source: GcsObject) -> _ObjectVersion:
    try:
        blob = client.bucket(source.bucket).blob(source.name)
        blob.reload()
        return _object_version(source, blob)
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(f"could not read GCS object metadata for {source.uri}: {error}") from error


def _list_prefix(
    client: Any,
    prefix: GcsPrefix,
    limits: BundleLimits,
) -> list[tuple[_ObjectVersion, str]]:
    result: list[tuple[_ObjectVersion, str]] = []
    try:
        for blob in client.list_blobs(prefix.bucket, prefix=prefix.name):
            name = str(getattr(blob, "name", ""))
            if not name.startswith(prefix.name):
                raise GcsStagingError(
                    f"GCS returned an object outside the requested prefix {prefix.uri}"
                )
            relative = name[len(prefix.name):]
            size = _size_bytes(getattr(blob, "size", None), f"gs://{prefix.bucket}/{name}")
            if not relative or relative.endswith("/"):
                if size == 0:
                    continue
                raise GcsStagingError(
                    f"GCS directory marker must be empty: gs://{prefix.bucket}/{name}"
                )
            _relative_path(relative, f"gs://{prefix.bucket}/{name}")
            if PurePosixPath(relative).parts[0] in RUNTIME_RESERVED_PATHS:
                raise GcsStagingError(
                    f"other-files path {relative!r} conflicts with a DREAM runtime path"
                )
            source = GcsObject(prefix.bucket, name)
            version = _object_version(source, blob, known_size=size)
            result.append((version, relative))
            if len(result) > limits.max_members - 3:
                raise GcsStagingError("GCS prefix contains too many files for a submission bundle")
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(f"could not list GCS prefix {prefix.uri}: {error}") from error
    if not result:
        raise GcsStagingError(f"GCS prefix {prefix.uri} contains no files")
    return result


def _object_version(
    source: GcsObject,
    blob: Any,
    *,
    known_size: int | None = None,
) -> _ObjectVersion:
    generation = getattr(blob, "generation", None)
    if isinstance(generation, bool) or generation is None:
        raise GcsStagingError(f"GCS object has no generation number: {source.uri}")
    try:
        generation_number = int(generation)
    except (TypeError, ValueError) as error:
        raise GcsStagingError(f"GCS object has an invalid generation number: {source.uri}") from error
    if generation_number <= 0:
        raise GcsStagingError(f"GCS object has an invalid generation number: {source.uri}")
    size = known_size if known_size is not None else _size_bytes(
        getattr(blob, "size", None), source.uri
    )
    return _ObjectVersion(source, generation_number, size)


def _size_bytes(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise GcsStagingError(f"GCS object has no valid size: {label}")
    try:
        size = int(value)
    except (TypeError, ValueError) as error:
        raise GcsStagingError(f"GCS object has no valid size: {label}") from error
    if size < 0:
        raise GcsStagingError(f"GCS object has an invalid size: {label}")
    return size


def _validate_limits(
    selected: list[tuple[_ObjectVersion, str]],
    limits: BundleLimits,
) -> None:
    if len(selected) + 1 > limits.max_members:
        raise GcsStagingError("GCS submission contains too many files for a submission bundle")
    if any(version.size_bytes > limits.max_member_bytes for version, _ in selected):
        raise GcsStagingError("a GCS submission file exceeds the configured member size limit")
    if sum(version.size_bytes for version, _ in selected) > limits.max_total_bytes:
        raise GcsStagingError("GCS submission files exceed the configured total size limit")


def _validate_tree_paths(paths: list[str]) -> None:
    path_set = set(paths)
    if len(path_set) != len(paths):
        raise GcsStagingError("GCS prefix contains duplicate relative file paths")
    for path in paths:
        for parent in PurePosixPath(path).parents:
            if parent != PurePosixPath(".") and str(parent) in path_set:
                raise GcsStagingError(f"GCS files contain a file/directory path conflict: {parent}")


def _download_object(
    client: Any,
    version: _ObjectVersion,
    target: Path,
    root: Path,
) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
        resolved_parent.relative_to(root)
        blob = client.bucket(version.source.bucket).blob(
            version.source.name,
            generation=version.generation,
        )
        from tqdm import tqdm

        with target.open("wb") as output:
            with tqdm.wrapattr(output, "write", total=version.size_bytes,
                               desc=f"Downloading {target.name}", unit="B",
                               unit_scale=True, unit_divisor=1024) as tracked:
                blob.download_to_file(tracked, if_generation_match=version.generation)
        actual_size = target.stat().st_size
        if actual_size != version.size_bytes:
            raise GcsStagingError(
                f"downloaded GCS object size changed for {version.source.uri}: "
                f"expected {version.size_bytes}, got {actual_size}"
            )
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(
            f"could not download generation {version.generation} of "
            f"{version.source.uri}: {error}"
        ) from error
