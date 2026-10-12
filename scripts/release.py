"""Cut a participant version tag that triggers the PyPI publishing workflow."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def _version_key(value: str, pattern: str) -> tuple[int, ...]:
    match = re.fullmatch(pattern, value)
    if match is None:
        raise ValueError(f"Unsupported existing version: {value}")
    major, minor, patch, stage, number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        {"a": 0, "b": 1, "rc": 2, None: 3}[stage],
        int(number or 0),
    )


def release_participant(root: Path, version: str, remote: str, *, dry_run: bool = False) -> str:
    version_pattern = (
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:(a|b|rc)(0|[1-9][0-9]*))?"
    )
    if not re.fullmatch(version_pattern, version):
        raise ValueError("Set VERSION to a release version, for example VERSION=0.2.0")
    if not remote or remote.startswith("-"):
        raise ValueError("RELEASE_REMOTE must name a Git remote")

    project_path = root / "pyproject.toml"
    source = project_path.read_text(encoding="utf-8")
    project = tomllib.loads(source)["project"]
    if project["name"] != "anvil-dream-participant":
        raise ValueError("Expected the anvil-dream-participant project")
    if git(root, "status", "--porcelain"):
        raise ValueError("Commit all package and release-tool changes before releasing")
    git(root, "remote", "get-url", remote)

    tag = f"participant-v{version}"
    head = git(root, "rev-parse", "HEAD")
    local_tag = git(root, "tag", "--list", tag)
    remote_lines = git(
        root,
        "ls-remote",
        "--tags",
        remote,
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    remote_refs = {
        fields[1]: fields[0]
        for line in remote_lines.splitlines()
        if (fields := line.split())
    }
    remote_commit = remote_refs.get(f"refs/tags/{tag}^{{}}") or remote_refs.get(
        f"refs/tags/{tag}"
    )
    if local_tag or remote_commit:
        local_commit = git(root, "rev-parse", f"{tag}^{{commit}}") if local_tag else head
        if (
            local_commit != head
            or remote_commit not in (None, head)
            or project["version"] != version
        ):
            raise ValueError(f"Release tag {tag} already exists for another source; use a new version")
        if remote_commit:
            print(f"{tag} is already pushed; inspect its GitHub publishing run")
        elif not dry_run:
            subprocess.run(["git", "-C", str(root), "push", remote, f"refs/tags/{tag}"], check=True)
        return tag

    git(root, "var", "GIT_AUTHOR_IDENT")
    git(root, "var", "GIT_COMMITTER_IDENT")
    bump = project["version"] != version
    if bump and _version_key(version, version_pattern) <= _version_key(
        project["version"], version_pattern
    ):
        raise ValueError("The next release version must be greater than the current version")

    short_head = git(root, "rev-parse", "--short", "HEAD")
    print(f"Release anvil-dream-participant {version} from {short_head}")
    if bump:
        print(f"Update pyproject.toml version {project['version']} -> {version} and commit it")
    print(f"Create {tag} and push it to {remote}; GitHub validates and publishes to PyPI")
    if dry_run:
        return tag

    if bump:
        updated, count = re.subn(
            r'(?m)^version\s*=\s*"[^"]+"\s*$',
            f'version = "{version}"',
            source,
        )
        if count != 1:
            raise ValueError("Expected one literal project version in pyproject.toml")
        project_path.write_text(updated, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--", "pyproject.toml"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "commit",
                "--only",
                "-m",
                f"Release anvil-dream-participant {version}",
                "--",
                "pyproject.toml",
            ],
            check=True,
        )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "tag",
            "-a",
            tag,
            "-m",
            f"anvil-dream-participant {version}",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "push", remote, f"refs/tags/{tag}"],
        check=True,
    )
    print(
        "Publishing run: https://github.com/cm4ai-anvil-dream-challenge/"
        "anvil-dream-participant/actions/workflows/publish-participant.yml"
    )
    return tag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=os.environ.get("VERSION", ""))
    parser.add_argument("--remote", default=os.environ.get("RELEASE_REMOTE", "origin"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        release_participant(Path(__file__).resolve().parents[1], args.version, args.remote, dry_run=args.dry_run)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
