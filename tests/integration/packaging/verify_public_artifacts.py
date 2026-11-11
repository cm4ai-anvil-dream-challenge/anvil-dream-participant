"""Verify participant artifact contents and package boundaries."""

from __future__ import annotations

import argparse
import ast
from pathlib import PurePosixPath
import re
import tarfile
import tomllib
import zipfile


PARTICIPANT_GUIDES = frozenset(
    {
        "docs/README.md",
        "docs/anvil-compute.md",
        "docs/benchmark.md",
        "docs/challenge-rules.md",
        "docs/challenge.md",
        "docs/cli.md",
        "docs/container-images.md",
        "docs/contract.md",
        "docs/data.md",
        "docs/evaluation.md",
        "docs/how-to-participate.md",
        "docs/motivation-and-background.md",
        "docs/news-and-timeline.md",
        "docs/overview.md",
        "docs/participants-and-teams.md",
        "docs/proticelli.md",
        "docs/quickstart.md",
        "docs/reading-list.md",
        "docs/results.md",
        "docs/participant-releases.md",
        "challenges/hpa/examples/proticelli/README.md",
    }
)
SDIST_GUIDES = PARTICIPANT_GUIDES | {"README.md"}
SDIST_MAINTAINER_FILES = frozenset({"Makefile", "scripts/release.py"})
SDIST_DOC_ASSETS = frozenset(
    {
        "docs/synapse-pages.json",
        "docs/assets",
        "docs/assets/challenge-banner.png",
        "docs/assets/data-hpa-channels-mask.png",
        "docs/assets/data-hpa-gallery.png",
        "docs/assets/data-hpa-masking.png",
    }
)
FORBIDDEN_ORGANIZER_HELPER_FILES = frozenset({"_organizer_progress.py", "_organizer_bundle.py"})
FORBIDDEN_IMPORT_ROOTS = (
    "organizer",
    "cli",
    "hpa",
    "anvil_dream_organizer",
    "challenges",
    "cm4ai_anvil_dream_tools",
)
FORBIDDEN_DISTRIBUTIONS = frozenset(
    {"cm4ai-anvil-dream-tools", "cm4ai-organizer-cli", "cm4ai-hpa-challenge"}
)
CORE_PACKAGE_PREFIX = "anvil_dream_participant/"
CLI_PACKAGE_PREFIX = "anvil_dream_participant/cli/"
CHALLENGE_PACKAGE_PREFIX = "anvil_dream_participant/challenges/"


def _public_module(name: str, archive_kind: str) -> bool:
    parts = PurePosixPath(name).parts
    if archive_kind == "wheel":
        return len(parts) > 1 and parts[0] == "anvil_dream_participant" and name.endswith(".py")
    return len(parts) > 1 and name.endswith(".py")


def is_forbidden_import(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_ROOTS)


def _assert_import_boundary(name: str, source: bytes) -> None:
    tree = ast.parse(source.decode("utf-8"), filename=name)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(alias.name for alias in node.names if is_forbidden_import(alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and is_forbidden_import(module):
                violations.append(module)
    assert not violations, f"{name} imports organizer-side modules: {sorted(set(violations))}"


def _assert_artifact_import_boundary(modules: list[tuple[str, bytes]]) -> None:
    for name, source in modules:
        _assert_import_boundary(name, source)


def _import_targets(name: str, tree: ast.AST) -> set[str]:
    package = PurePosixPath(name).parts[:-1]
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package[: len(package) - node.level + 1] if node.level else ()
            base = ".".join((*parts, *((node.module or "").split(".") if node.module else ())))
            targets.add(base)
            targets.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return targets


def _under(module: str, namespace: str) -> bool:
    return module == namespace or module.startswith(namespace + ".")


def _assert_architecture_boundaries(modules: list[tuple[str, bytes]]) -> None:
    for name, source in modules:
        tree = ast.parse(source.decode("utf-8"), filename=name)
        targets = _import_targets(name, tree)
        cli_imports = {
            target for target in targets if _under(target, "anvil_dream_participant.cli")
        }
        challenge_imports = {
            target for target in targets if _under(target, "anvil_dream_participant.challenges")
        }
        framework_imports = {
            target for target in targets if _under(target, "click") or _under(target, "argparse")
        }
        if name.startswith(CORE_PACKAGE_PREFIX) and not name.startswith(
            (CLI_PACKAGE_PREFIX, CHALLENGE_PACKAGE_PREFIX)
        ):
            if name == "anvil_dream_participant/workflows/wdl.py":
                # The published validator entrypoint delegates lazily to HPA.
                challenge_imports -= {
                    "anvil_dream_participant.challenges.hpa.workflows",
                    "anvil_dream_participant.challenges.hpa.workflows.parse_participant_workflow",
                }
            violations = cli_imports | challenge_imports | framework_imports
            assert not violations, (
                f"core module {name} imports CLI or HPA code: {sorted(violations)}"
            )
        if name.startswith(CLI_PACKAGE_PREFIX):
            assert not challenge_imports, (
                f"CLI module {name} imports HPA code directly: {sorted(challenge_imports)}"
            )
        if name.startswith(CHALLENGE_PACKAGE_PREFIX) and "/examples/" not in name:
            violations = cli_imports | framework_imports
            commands = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
            ]
            assert not violations, (
                f"challenge module {name} imports CLI framework code: {sorted(violations)}"
            )
            assert not commands, f"challenge module {name} defines a command entrypoint"


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _assert_no_forbidden_organizer_helper_files(paths: list[str], artifact_name: str) -> None:
    found = sorted(
        path for path in paths if PurePosixPath(path).name in FORBIDDEN_ORGANIZER_HELPER_FILES
    )
    assert not found, f"{artifact_name} includes organizer helper modules: {found}"


def _assert_no_forbidden_distributions(requirements: list[str], artifact_name: str) -> None:
    found = set()
    for requirement in requirements:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        assert match is not None, f"{artifact_name} has an invalid dependency: {requirement!r}"
        name = normalize_distribution_name(match.group(1))
        if name in FORBIDDEN_DISTRIBUTIONS:
            found.add(name)
    assert not found, (
        f"{artifact_name} directly depends on organizer distributions: {sorted(found)}"
    )


def _assert_project_dependency_boundary(source: str, artifact_name: str) -> None:
    document = tomllib.loads(source)
    project = document.get("project", {})
    requirements = list(document.get("build-system", {}).get("requires", []))
    requirements.extend(project.get("dependencies", []))
    for optional_requirements in project.get("optional-dependencies", {}).values():
        requirements.extend(optional_requirements)
    _assert_no_forbidden_distributions(requirements, artifact_name)


def _assert_metadata_dependency_boundary(source: str, artifact_name: str) -> None:
    requirements = [
        line.partition(":")[2].strip()
        for line in source.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    _assert_no_forbidden_distributions(requirements, artifact_name)


def _wheel_path_is_public(name: str) -> bool:
    first = PurePosixPath(name).parts[0]
    return first == "anvil_dream_participant" or first.endswith(".dist-info")


def _sdist_path_is_public(name: str) -> bool:
    parts = PurePosixPath(name).parts[1:]
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in {
            "PKG-INFO",
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "setup.cfg",
            "MANIFEST.in",
            "Makefile",
            "scripts",
            "participant",
            "cli",
            "challenges",
            "docs",
            "tests",
        } or parts[0].endswith(".egg-info")
    if parts[0] == "docs":
        relative = "/".join(parts)
        return relative in PARTICIPANT_GUIDES or relative in SDIST_DOC_ASSETS
    if parts[0] == "scripts":
        return "/".join(parts) in SDIST_MAINTAINER_FILES
    if parts[0] in {"participant", "cli", "challenges", "tests"}:
        return True
    return parts[0].endswith(".egg-info")


def _assert_guide_inventory(
    names: set[str], prefix: str, guides: frozenset[str], artifact: str
) -> None:
    expected = {prefix + guide for guide in guides}
    missing = expected - names
    assert not missing, f"{artifact} omits guides: {sorted(missing)}"
    unexpected = {name for name in names if name.endswith(".md")} - expected
    assert not unexpected, f"{artifact} includes unexpected documentation: {sorted(unexpected)}"


def _assert_required_modules(modules: set[str], required: set[str], artifact: str) -> None:
    missing = required - modules
    assert not missing, f"{artifact} omits required modules: {sorted(missing)}"


def _assert_public_modules(modules: set[str], artifact: str) -> None:
    required = {
        "anvil_dream_participant/__init__.py",
        "anvil_dream_participant/challenge.py",
        "anvil_dream_participant/config.py",
        "anvil_dream_participant/workflows/wdl.py",
        "anvil_dream_participant/cli/__init__.py",
        "anvil_dream_participant/cli/__main__.py",
        "anvil_dream_participant/cli/root.py",
        "anvil_dream_participant/cli/submit.py",
        "anvil_dream_participant/cli/submit_anvil.py",
        "anvil_dream_participant/cli/submission_status.py",
        "anvil_dream_participant/challenges/__init__.py",
        "anvil_dream_participant/challenges/hpa/__init__.py",
        "anvil_dream_participant/challenges/hpa/profile.py",
        "anvil_dream_participant/challenges/hpa/workflows.py",
    }
    _assert_required_modules(modules, required, artifact)
    assert not any("benchmarking" in PurePosixPath(name).parts for name in modules), (
        f"{artifact} includes retired evaluation modules"
    )
    assert "anvil_dream_participant/cli/benchmark.py" not in modules, (
        f"{artifact} exposes the retired benchmark command"
    )


def verify_wheel(path: str) -> tuple[int, list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _assert_no_forbidden_organizer_helper_files(list(names), "participant wheel")
        _assert_guide_inventory(
            names, "anvil_dream_participant/", PARTICIPANT_GUIDES, "participant wheel"
        )
        foreign_paths = [name for name in names if not _wheel_path_is_public(name)]
        assert not foreign_paths, f"wheel contains nonparticipant paths: {foreign_paths}"
        modules = sorted(name for name in names if name.endswith(".py"))
        module_set = set(modules)
        invalid_modules = [name for name in modules if not _public_module(name, "wheel")]
        assert not invalid_modules, (
            f"wheel contains nonparticipant Python modules: {invalid_modules}"
        )
        assert not any("tests" in PurePosixPath(name).parts for name in names), (
            "participant wheel includes development tests"
        )
        assert "anvil_dream_participant/challenges/hpa/examples/proticelli/participant.wdl" in names
        assert (
            "anvil_dream_participant/challenges/hpa/examples/proticelli/scripts/build_weights.py"
            in names
        )
        _assert_public_modules(module_set, "participant wheel")
        module_sources = [(name, archive.read(name)) for name in modules]
        _assert_artifact_import_boundary(module_sources)
        _assert_architecture_boundaries(module_sources)

        forbidden_modules = {
            name
            for name in names
            if name.startswith(("organizer/", "anvil_dream_participant/grading/"))
            or "/tasks/gather" in name
        }
        assert not forbidden_modules, (
            f"participant wheel includes organizer grading/gather code: {sorted(forbidden_modules)}"
        )

        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        _assert_metadata_dependency_boundary(metadata, metadata_name)
        assert "Name: anvil-dream-participant\n" in metadata
        assert "License-Expression: Apache-2.0\n" in metadata
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        dependencies = {"click", "google-auth", "synapseclient"}
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
        assert "anvil-dream-participant = anvil_dream_participant.cli:main" in entry_points
        console_scripts = [
            line for line in entry_points.splitlines() if line and not line.startswith("[")
        ]
        assert console_scripts == ["anvil-dream-participant = anvil_dream_participant.cli:main"], (
            f"participant wheel exposes unexpected console scripts: {console_scripts}"
        )
    return len(names), modules


def verify_sdist(path: str) -> tuple[int, list[str]]:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        _assert_no_forbidden_organizer_helper_files(names, "participant sdist")
        relative_names = {"/".join(PurePosixPath(name).parts[1:]) for name in names}
        assert "LICENSE" in relative_names, "participant sdist omits its license"
        _assert_guide_inventory(relative_names, "", SDIST_GUIDES, "participant sdist")
        foreign_paths = [name for name in names if not _sdist_path_is_public(name)]
        assert not foreign_paths, f"sdist contains nonparticipant paths: {foreign_paths}"
        modules = sorted(name for name in names if name.endswith(".py"))
        invalid_modules = [name for name in modules if not _public_module(name, "sdist")]
        assert not invalid_modules, (
            f"sdist contains nonparticipant Python modules: {invalid_modules}"
        )
        module_sources = []
        for name in modules:
            member = archive.extractfile(name)
            assert member is not None
            module_sources.append((name, member.read()))
        _assert_artifact_import_boundary(module_sources)
        package_sources = [
            (package_name, source)
            for name, source in module_sources
            if (package_name := _sdist_package_path(name)) is not None
        ]
        _assert_public_modules({name for name, _ in package_sources}, "participant sdist")
        _assert_architecture_boundaries(package_sources)

        pyproject_name = next(name for name in names if name.endswith("/pyproject.toml"))
        pyproject = archive.extractfile(pyproject_name)
        assert pyproject is not None
        source = pyproject.read().decode("utf-8")
        _assert_project_dependency_boundary(source, pyproject_name)
        assert 'anvil-dream-participant = "anvil_dream_participant.cli:main"' in source
        assert 'anvil_dream_participant = "participant"' in source
        assert '"anvil_dream_participant.cli" = "cli"' in source
        assert '"anvil_dream_participant.challenges.hpa" = "challenges/hpa"' in source
    return len(names), modules


def _sdist_package_path(name: str) -> str | None:
    parts = PurePosixPath(name).parts[1:]
    if not parts:
        return None
    source_root, *relative = parts
    if source_root == "participant":
        package_root = "anvil_dream_participant"
    elif source_root in {"cli", "challenges"}:
        package_root = f"anvil_dream_participant/{source_root}"
    else:
        return None
    return "/".join((package_root, *relative))


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
