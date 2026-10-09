"""Verify that built public artifacts contain only participant modules and imports."""

from __future__ import annotations

import argparse
import ast
from pathlib import PurePosixPath
import tarfile
import zipfile


PARTICIPANT_GUIDES = {
    "README.md",
    "docs/quickstart.md",
    "docs/contract.md",
    "docs/container-images.md",
    "docs/proticelli.md",
    "benchmarking/README.md",
    "examples/proticelli/README.md",
}


def _public_module(name: str, archive_kind: str) -> bool:
    parts = PurePosixPath(name).parts
    if archive_kind == "wheel":
        return len(parts) > 1 and parts[0] == "dream_participant" and name.endswith(".py")
    return len(parts) > 1 and name.endswith(".py")


def _assert_import_boundary(name: str, source: bytes) -> None:
    tree = ast.parse(source.decode("utf-8"), filename=name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "organizer" and not alias.name.startswith("organizer."), name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module != "organizer" and not module.startswith("organizer."), name


def _wheel_path_is_public(name: str) -> bool:
    first = PurePosixPath(name).parts[0]
    return first == "dream_participant" or first.endswith(".dist-info")


def _sdist_path_is_public(name: str) -> bool:
    parts = PurePosixPath(name).parts[1:]
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in {
            "PKG-INFO",
            "README.md",
            "pyproject.toml",
            "setup.cfg",
            "MANIFEST.in",
            "__init__.py",
            "config.py",
            "progress.py",
            "cli",
            "benchmarking",
            "integrations",
            "submissions",
            "workflows",
            "docs",
            "examples",
            "tests",
        } or parts[0].endswith(".egg-info")
    if parts[0] == "docs":
        return len(parts) == 2 and "/".join(parts) in PARTICIPANT_GUIDES
    return (
        parts[0] in {"benchmarking", "cli", "integrations", "submissions", "workflows", "examples", "tests"}
        or parts[0].endswith(".egg-info")
    )


def verify_wheel(path: str) -> tuple[int, list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        missing_guides = {"dream_participant/" + path for path in PARTICIPANT_GUIDES} - set(names)
        assert not missing_guides, f"participant wheel omits guides: {sorted(missing_guides)}"
        unexpected_guides = {name for name in names if name.endswith(".md")} - {
            "dream_participant/" + path for path in PARTICIPANT_GUIDES
        }
        assert not unexpected_guides, (
            f"participant wheel includes unexpected documentation: {sorted(unexpected_guides)}"
        )
        foreign_paths = [name for name in names if not _wheel_path_is_public(name)]
        assert not foreign_paths, f"wheel contains nonparticipant paths: {foreign_paths}"
        modules = [name for name in names if name.endswith(".py")]
        invalid_modules = [name for name in modules if not _public_module(name, "wheel")]
        assert not invalid_modules, (
            f"wheel contains nonparticipant Python modules: {invalid_modules}"
        )
        assert not any("dream" in PurePosixPath(name).parts for name in names)
        assert not any("tests" in PurePosixPath(name).parts for name in names), (
            "participant wheel includes development tests"
        )
        assert "dream_participant/examples/proticelli/participant.wdl" in names
        assert "dream_participant/examples/proticelli/scripts/build_weights.py" in names
        for name in modules:
            _assert_import_boundary(name, archive.read(name))
        required_benchmark_modules = {
            "dream_participant/benchmarking/__init__.py",
            "dream_participant/benchmarking/aggregate.py",
            "dream_participant/benchmarking/contract.py",
            "dream_participant/benchmarking/evaluation.py",
            "dream_participant/benchmarking/image_results.py",
            "dream_participant/benchmarking/report.py",
            "dream_participant/benchmarking/score.py",
        }
        missing_benchmark = required_benchmark_modules - set(modules)
        assert not missing_benchmark, (
            f"participant wheel omits benchmark modules: {sorted(missing_benchmark)}"
        )
        assert "dream_participant/benchmarking/README.md" in names, (
            "participant wheel omits its benchmark input guide"
        )
        forbidden_modules = {
            name
            for name in names
            if name.startswith(("organizer/", "dream_participant/grading/"))
            or "/tasks/gather" in name
        }
        assert not forbidden_modules, (
            f"participant wheel includes organizer grading/gather code: {sorted(forbidden_modules)}"
        )
        assert "dream_participant/benchmarking/_evidence.py" not in names, (
            "participant wheel includes organizer provenance helpers"
        )

        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        dependencies = {"google-auth", "synapseclient"}
        missing = {
            dependency
            for dependency in dependencies
            if not any(
                line.startswith(f"Requires-Dist: {dependency}") for line in metadata.splitlines()
            )
        }
        assert not missing, f"wheel metadata omits dependencies: {sorted(missing)}"

        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode("utf-8")
        assert "dream = dream_participant.cli:main" in entry_points
        console_scripts = [
            line for line in entry_points.splitlines() if line and not line.startswith("[")
        ]
        assert console_scripts == ["dream = dream_participant.cli:main"], (
            f"participant wheel exposes unexpected console scripts: {console_scripts}"
        )
    return len(names), modules


def verify_sdist(path: str) -> tuple[int, list[str]]:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        relative_names = {"/".join(PurePosixPath(name).parts[1:]) for name in names}
        missing_guides = PARTICIPANT_GUIDES - relative_names
        assert not missing_guides, f"participant sdist omits guides: {sorted(missing_guides)}"
        unexpected_guides = {name for name in relative_names if name.endswith(".md")} - PARTICIPANT_GUIDES
        assert not unexpected_guides, (
            f"participant sdist includes unexpected documentation: {sorted(unexpected_guides)}"
        )
        foreign_paths = [name for name in names if not _sdist_path_is_public(name)]
        assert not foreign_paths, f"sdist contains nonparticipant paths: {foreign_paths}"
        modules = [name for name in names if name.endswith(".py")]
        invalid_modules = [name for name in modules if not _public_module(name, "sdist")]
        assert not invalid_modules, (
            f"sdist contains nonparticipant Python modules: {invalid_modules}"
        )
        assert not any("dream" in PurePosixPath(name).parts for name in names)
        for name in modules:
            member = archive.extractfile(name)
            assert member is not None
            _assert_import_boundary(name, member.read())

        pyproject_name = next(name for name in names if name.endswith("/pyproject.toml"))
        pyproject = archive.extractfile(pyproject_name)
        assert pyproject is not None
        source = pyproject.read().decode("utf-8")
        assert 'dream = "dream_participant.cli:main"' in source
    return len(names), modules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel")
    parser.add_argument("sdist")
    args = parser.parse_args()

    wheel_paths, wheel_modules = verify_wheel(args.wheel)
    sdist_paths, sdist_modules = verify_sdist(args.sdist)
    print(
        f"Verified participant wheel ({len(wheel_modules)} Python modules, "
        f"{wheel_paths} paths) and sdist ({len(sdist_modules)} Python modules, "
        f"{sdist_paths} paths)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
