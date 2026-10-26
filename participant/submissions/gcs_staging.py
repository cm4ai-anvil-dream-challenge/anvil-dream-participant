"""Validate and stream Google Cloud Storage submission inputs to local staging files."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from google.cloud import storage

from .bundle import (
    DEFAULT_BUNDLE_LIMITS,
    BundleLimits,
    BundleValidationError,
    RUNTIME_RESERVED_PATHS,
)


GcsStagingError = BundleValidationError


GcsSource = dict[str, object]


def _parse_source(value: str, field: str, *, prefix: bool = False) -> GcsSource:
    bucket, name = _uri_parts(value, field)
    if prefix:
        if not name.endswith("/"):
            name += "/"
        _relative_path(name[:-1], field)
    else:
        if name.endswith("/"):
            raise GcsStagingError(f"{field} must name a GCS object, not a prefix")
        _relative_path(name, field)
    return {"bucket": bucket, "name": name, "prefix": prefix}


def _source_uri(source: GcsSource) -> str:
    return f"gs://{source['bucket']}/{source['name']}"


def parse_gcs_submission_sources(
    wdl: str,
    weights: str,
    files: list[str] | None = None,
    *,
    recursive: bool = False,
) -> dict[str, object]:
    if recursive and not files:
        raise GcsStagingError("--recursive requires --files with at least one GCS directory")
    return {
        "wdl": _parse_source(wdl, "--wdl"),
        "weights": _parse_source(weights, "--weights"),
        "files": tuple(
            _parse_source(value, "--files", prefix=recursive) for value in (files or [])
        ),
    }


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
    sources: dict[str, object],
    destination: Path,
    *,
    credentials: Any,
    project: str | None,
    limits: BundleLimits = DEFAULT_BUNDLE_LIMITS,
) -> dict[str, Path | None]:
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
        (_load_object(client, sources["wdl"]), "workflow.wdl"),
        (_load_object(client, sources["weights"]), "weights"),
    ]
    optional: list[tuple[tuple[GcsSource, int, int], str]] = []
    for source in sources["files"]:
        if source["prefix"]:
            optional.extend(_list_prefix(client, source, limits))
        else:
            optional.append((_load_object(client, source), PurePosixPath(str(source["name"])).name))
        _validate_limits([*fixed, *optional], limits)
    selected = [*fixed, *optional]
    _validate_limits(selected, limits)
    _validate_tree_paths([relative for _, relative in optional])

    wdl_path = root / "workflow.wdl"
    weights_path = root / "weights"
    other_root = root / "other" if sources["files"] else None
    for version, relative in fixed:
        _download_object(client, version, root / relative, root)
    for version, relative in optional:
        _download_object(client, version, root / "other" / relative, root)
    return {"wdl": wdl_path, "weights": weights_path, "other_files": other_root}


def _load_object(client: Any, source: GcsSource) -> tuple[GcsSource, int, int]:
    try:
        blob = client.bucket(source["bucket"]).blob(source["name"])
        blob.reload()
        return _object_version(source, blob)
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(f"could not read GCS object metadata for {_source_uri(source)}: {error}") from error


def _list_prefix(
    client: Any,
    prefix: GcsSource,
    limits: BundleLimits,
) -> list[tuple[tuple[GcsSource, int, int], str]]:
    result: list[tuple[tuple[GcsSource, int, int], str]] = []
    try:
        for blob in client.list_blobs(prefix["bucket"], prefix=prefix["name"]):
            name = str(getattr(blob, "name", ""))
            if not name.startswith(str(prefix["name"])):
                raise GcsStagingError(
                    f"GCS returned an object outside the requested prefix {_source_uri(prefix)}"
                )
            relative = name[len(str(prefix["name"])):]
            size = _size_bytes(getattr(blob, "size", None), f"gs://{prefix['bucket']}/{name}")
            if not relative or relative.endswith("/"):
                if size == 0:
                    continue
                raise GcsStagingError(
                    f"GCS directory marker must be empty: gs://{prefix['bucket']}/{name}"
                )
            _relative_path(relative, f"gs://{prefix['bucket']}/{name}")
            source = {"bucket": prefix["bucket"], "name": name, "prefix": False}
            version = _object_version(source, blob, known_size=size)
            result.append((version, relative))
            if len(result) > limits["max_members"] - 3:
                raise GcsStagingError("GCS prefix contains too many files for a submission bundle")
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(f"could not list GCS prefix {_source_uri(prefix)}: {error}") from error
    if not result:
        raise GcsStagingError(f"GCS prefix {_source_uri(prefix)} contains no files")
    return result


def _object_version(
    source: GcsSource,
    blob: Any,
    *,
    known_size: int | None = None,
) -> tuple[GcsSource, int, int]:
    generation = getattr(blob, "generation", None)
    if isinstance(generation, bool) or generation is None:
        raise GcsStagingError(f"GCS object has no generation number: {_source_uri(source)}")
    try:
        generation_number = int(generation)
    except (TypeError, ValueError) as error:
        raise GcsStagingError(f"GCS object has an invalid generation number: {_source_uri(source)}") from error
    if generation_number <= 0:
        raise GcsStagingError(f"GCS object has an invalid generation number: {_source_uri(source)}")
    size = known_size if known_size is not None else _size_bytes(
        getattr(blob, "size", None), _source_uri(source)
    )
    return source, generation_number, size


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
    selected: list[tuple[tuple[GcsSource, int, int], str]],
    limits: BundleLimits,
) -> None:
    if len(selected) + 1 > limits["max_members"]:
        raise GcsStagingError("GCS submission contains too many files for a submission bundle")
    if any(version[2] > limits["max_member_bytes"] for version, _ in selected):
        raise GcsStagingError("a GCS submission file exceeds the configured member size limit")
    if sum(version[2] for version, _ in selected) > limits["max_total_bytes"]:
        raise GcsStagingError("GCS submission files exceed the configured total size limit")


def _validate_tree_paths(paths: list[str]) -> None:
    path_set = set(paths)
    if len(path_set) != len(paths):
        raise GcsStagingError("GCS files contain duplicate relative file paths")
    for path in paths:
        if PurePosixPath(path).parts[0] in RUNTIME_RESERVED_PATHS:
            raise GcsStagingError(
                f"supporting file path {path!r} conflicts with a DREAM runtime path"
            )
        for parent in PurePosixPath(path).parents:
            if parent != PurePosixPath(".") and str(parent) in path_set:
                raise GcsStagingError(f"GCS files contain a file/directory path conflict: {parent}")


def _download_object(
    client: Any,
    version: tuple[GcsSource, int, int],
    target: Path,
    root: Path,
) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
        resolved_parent.relative_to(root)
        blob = client.bucket(version[0]["bucket"]).blob(
            version[0]["name"],
            generation=version[1],
        )
        from tqdm import tqdm

        with target.open("wb") as output:
            with tqdm.wrapattr(output, "write", total=version[2],
                               desc=f"Downloading {target.name}", unit="B",
                               unit_scale=True, unit_divisor=1024) as tracked:
                blob.download_to_file(tracked, if_generation_match=version[1])
        actual_size = target.stat().st_size
        if actual_size != version[2]:
            raise GcsStagingError(
                f"downloaded GCS object size changed for {_source_uri(version[0])}: "
                f"expected {version[2]}, got {actual_size}"
            )
    except GcsStagingError:
        raise
    except Exception as error:
        raise GcsStagingError(
            f"could not download generation {version[1]} of "
            f"{_source_uri(version[0])}: {error}"
        ) from error
