from __future__ import annotations

from pathlib import Path
import importlib.util
import subprocess
import tempfile
import tomllib
import unittest

release_path = Path(__file__).resolve().parents[3] / "scripts/release.py"
release_spec = importlib.util.spec_from_file_location("participant_release", release_path)
if release_spec is None or release_spec.loader is None:
    raise ImportError(f"Cannot load release helper at {release_path}")
release_module = importlib.util.module_from_spec(release_spec)
release_spec.loader.exec_module(release_module)
git = release_module.git
release_participant = release_module.release_participant


class ParticipantReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / "source"
        self.root.mkdir()
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        git(self.root, "config", "user.name", "Release Test")
        git(self.root, "config", "user.email", "release@example.org")
        git(self.root, "config", "commit.gpgsign", "false")
        git(self.root, "config", "tag.gpgsign", "false")
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "anvil-dream-participant"\nversion = "0.1.0"\n'
        )
        (self.root / "README.md").write_text("Participant package\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Initial package")
        git(self.root, "remote", "add", "origin", str(self.remote))

    def test_dry_run_changes_neither_source_nor_remote(self) -> None:
        before = git(self.root, "rev-parse", "HEAD")
        tag = release_participant(self.root, "0.1.1", "origin", dry_run=True)

        self.assertEqual(tag, "participant-v0.1.1")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.root, "tag", "--list"), "")
        self.assertEqual(git(self.root, "ls-remote", "origin"), "")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_release_bumps_only_project_version_and_pushes_an_annotated_tag(self) -> None:
        release_participant(self.root, "0.1.1", "origin")

        project = tomllib.loads((self.root / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["version"], "0.1.1")
        self.assertEqual(git(self.root, "diff", "--name-only", "HEAD^", "HEAD"), "pyproject.toml")
        self.assertEqual(git(self.root, "cat-file", "-t", "participant-v0.1.1"), "tag")
        remote_head = git(
            self.root, "ls-remote", "origin", "refs/tags/participant-v0.1.1^{}"
        ).split()[0]
        self.assertEqual(remote_head, git(self.root, "rev-parse", "HEAD"))
        before = git(self.root, "rev-parse", "HEAD")
        release_participant(self.root, "0.1.1", "origin")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)

    def test_retry_after_failed_push_reuses_the_local_tag(self) -> None:
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            release_participant(self.root, "0.1.0", "origin")
        tag_object = git(self.root, "rev-parse", "participant-v0.1.0")
        hook.unlink()

        release_participant(self.root, "0.1.0", "origin")

        self.assertEqual(git(self.root, "rev-parse", "participant-v0.1.0"), tag_object)
        self.assertTrue(git(self.root, "ls-remote", "origin", "refs/tags/participant-v0.1.0"))

    def test_dirty_package_changes_are_not_released(self) -> None:
        (self.root / "README.md").write_text("Changed package source\n")
        with self.assertRaisesRegex(ValueError, "Commit all package"):
            release_participant(self.root, "0.1.0", "origin", dry_run=True)
        self.assertEqual(git(self.root, "tag", "--list"), "")

    def test_invalid_versions_and_downgrades_are_rejected(self) -> None:
        for version in ("", "0.0.9", "01.1.0", "../outside", "0.1.0; exit 0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release_participant(self.root, version, "origin", dry_run=True)

    def test_existing_tag_cannot_be_reused_for_changed_source(self) -> None:
        release_participant(self.root, "0.1.0", "origin")
        (self.root / "README.md").write_text("Changed package source\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Changed package")
        with self.assertRaisesRegex(ValueError, "another source"):
            release_participant(self.root, "0.1.0", "origin")
