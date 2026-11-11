from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests.integration.packaging.verify_public_artifacts import (
    _assert_artifact_import_boundary,
    _assert_architecture_boundaries,
    _assert_metadata_dependency_boundary,
    _assert_no_forbidden_organizer_helper_files,
    _assert_project_dependency_boundary,
    _assert_import_boundary,
)


ROOT = Path(__file__).resolve().parents[3]


class ParticipantImportBoundaryTests(unittest.TestCase):
    def test_participant_pyproject_has_no_direct_organizer_dependencies(self) -> None:
        project = ROOT / "pyproject.toml"
        _assert_project_dependency_boundary(project.read_text(encoding="utf-8"), str(project))

    def test_participant_source_modules_do_not_import_organizer_side_namespaces(self) -> None:
        generated = {".ruff_cache", "__pycache__", "build", "dist", ".venv", "venv"}
        sources = (
            path
            for path in ROOT.rglob("*.py")
            if not generated.intersection(path.relative_to(ROOT).parts)
            and not any(part.endswith(".egg-info") for part in path.relative_to(ROOT).parts)
        )
        for source in sources:
            with self.subTest(source=source.relative_to(ROOT)):
                _assert_import_boundary(str(source.relative_to(ROOT)), source.read_bytes())

    def test_public_integrations_do_not_import_or_expose_organizer_adapters(self) -> None:
        script = """
import sys
from anvil_dream_participant.integrations.synapse import ParticipantSynapseGateway
from anvil_dream_participant.challenges.hpa.profile import PROFILE
from anvil_dream_participant.integrations.terra import ParticipantTerraClient
from anvil_dream_participant.progress import ProgressReporter
from anvil_dream_participant.submissions.bundle import SubmissionBundle, SubmissionBundleManifest
import anvil_dream_participant.workflows.wdl as participant_wdl

from tests.integration.packaging.verify_public_artifacts import is_forbidden_import

loaded = sorted(module for module in sys.modules if is_forbidden_import(module))
if loaded:
    raise AssertionError(f"public participant imports loaded internal modules: {loaded}")
if hasattr(PROFILE, "benchmark"):
    raise AssertionError("participant profile exposes an evaluation operation")

organizer_synapse = ("all_submission_bundles", "download_submission", "resolve_submitter_identity", "submitted_bundle_sha256")
organizer_terra = ("workspace_path", "register_agora_method", "create_workspace", "workspace_bucket", "object_metadata", "upload_local")
for name in organizer_synapse:
    if hasattr(ParticipantSynapseGateway, name):
        raise AssertionError(f"public Synapse adapter exposes organizer operation: {name}")
for name in organizer_terra:
    if hasattr(ParticipantTerraClient, name):
        raise AssertionError(f"public Terra adapter exposes organizer operation: {name}")
for name in ("from_archive", "file_path", "validate_queue_identity", "write_runtime_assets_archive"):
    if hasattr(SubmissionBundle, name):
        raise AssertionError(f"public bundle exposes organizer operation: {name}")
if hasattr(SubmissionBundleManifest, "validate_queue_identity"):
    raise AssertionError("public manifest exposes organizer queue validation")
for name in ("start_heartbeat", "suppress_heartbeat"):
    if hasattr(ProgressReporter, name):
        raise AssertionError(f"public progress exposes organizer operation: {name}")
if hasattr(participant_wdl, "normalize_participant_inputs"):
    raise AssertionError("public WDL exposes organizer input normalization")
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            cwd=ROOT,
            env=env,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_cli_help_does_not_import_or_accept_organizer_commands(self) -> None:
        script = """
import sys
from anvil_dream_participant.cli import main

try:
    main(["submit", "--help"])
except SystemExit as error:
    if error.code not in (None, 0):
        raise
else:
    raise AssertionError("participant help did not exit")

from tests.integration.packaging.verify_public_artifacts import is_forbidden_import

loaded = sorted(module for module in sys.modules if is_forbidden_import(module))
if loaded:
    raise AssertionError(f"participant help imported internal modules: {loaded}")
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            cwd=ROOT,
            env=env,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: anvil-dream-participant submit", result.stdout)


class PublicArtifactBoundaryTests(unittest.TestCase):
    def test_artifact_paths_reject_organizer_helpers_at_any_depth(self) -> None:
        paths = (
            "anvil_dream_participant/_organizer_progress.py",
            "source/anvil_dream_participant/submissions/_organizer_bundle.py",
        )
        for path in paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(AssertionError, "organizer helper modules"):
                    _assert_no_forbidden_organizer_helper_files([path], "fake participant artifact")

    def test_fake_artifact_imports_from_organizer_side_namespaces_fail(self) -> None:
        modules = [
            ("fake/anvil_dream_participant/adapter.py", b"import organizer.runtime\n"),
            ("fake/anvil_dream_participant/adapter.py", b"from cli.root import run\n"),
            ("fake/anvil_dream_participant/adapter.py", b"import hpa.tasks\n"),
            (
                "fake/anvil_dream_participant/adapter.py",
                b"from anvil_dream_organizer import runtime\n",
            ),
            ("fake/anvil_dream_participant/adapter.py", b"from challenges.hpa import problem\n"),
            (
                "fake/anvil_dream_participant/adapter.py",
                b"import cm4ai_anvil_dream_tools.organizer\n",
            ),
        ]
        for name, source in modules:
            with self.subTest(source=source):
                with self.assertRaisesRegex(AssertionError, "organizer-side modules"):
                    _assert_artifact_import_boundary([(name, source)])

    def test_participant_relative_cli_import_remains_valid(self) -> None:
        _assert_artifact_import_boundary(
            [("anvil_dream_participant/adapter.py", b"from .cli import main\n")]
        )

    def test_fake_architecture_violations_are_rejected(self) -> None:
        violations = (
            (
                "anvil_dream_participant/config.py",
                b"from .cli.root import cli\n",
                "core module",
            ),
            (
                "anvil_dream_participant/cli/root.py",
                b"from ..challenges.hpa.profile import PROFILE\n",
                "CLI module",
            ),
            (
                "anvil_dream_participant/challenges/hpa/profile.py",
                b"import click\n",
                "challenge module",
            ),
            (
                "anvil_dream_participant/challenges/hpa/profile.py",
                b"def main():\n    pass\n",
                "command entrypoint",
            ),
        )
        violations += (
            (
                "anvil_dream_participant/config.py",
                b"import anvil_dream_participant.challenges.hpa.profile\n",
                "core module",
            ),
            (
                "anvil_dream_participant/submissions/operations.py",
                b"from ..cli.root import cli\n",
                "core module",
            ),
            (
                "anvil_dream_participant/submissions/operations.py",
                b"from .. import cli\n",
                "core module",
            ),
            (
                "anvil_dream_participant/cli/root.py",
                b"from ..challenges import hpa\n",
                "CLI module",
            ),
            (
                "anvil_dream_participant/challenges/hpa/profile.py",
                b"from ...cli import root\n",
                "challenge module",
            ),
            ("anvil_dream_participant/config.py", b"import click\n", "core module"),
        )
        for name, source, message in violations:
            with self.subTest(name=name, source=source):
                with self.assertRaisesRegex(AssertionError, message):
                    _assert_architecture_boundaries([(name, source)])

    def test_expected_package_dependencies_remain_valid(self) -> None:
        _assert_architecture_boundaries(
            [
                ("anvil_dream_participant/config.py", b"from .challenge import ChallengeProfile\n"),
                (
                    "anvil_dream_participant/cli/root.py",
                    b"from ..challenge import load_challenge\n",
                ),
                (
                    "anvil_dream_participant/challenges/hpa/profile.py",
                    b"from .workflows import parse_participant_workflow\n",
                ),
            ]
        )

    def test_legacy_validator_exception_is_limited_to_its_public_entrypoint(self) -> None:
        parser_import = b"from ..challenges.hpa.workflows import parse_participant_workflow\n"
        _assert_architecture_boundaries(
            [("anvil_dream_participant/workflows/wdl.py", parser_import)]
        )
        for name, source in (
            ("anvil_dream_participant/workflows/other.py", parser_import),
            (
                "anvil_dream_participant/workflows/wdl.py",
                b"from ..challenges.hpa.workflows import unrelated_helper\n",
            ),
            (
                "anvil_dream_participant/workflows/wdl.py",
                b"from ..challenges.hpa.profile import PROFILE\n",
            ),
        ):
            with self.subTest(name=name, source=source):
                with self.assertRaisesRegex(AssertionError, "core module"):
                    _assert_architecture_boundaries([(name, source)])

    def test_project_and_wheel_metadata_reject_direct_organizer_dependencies(self) -> None:
        dependencies = (
            "cm4ai_anvil_dream_tools>=0.1",
            "cm4ai-organizer-cli[cli]>=0.1",
            "cm4ai_hpa_challenge==0.1.0",
        )
        for dependency in dependencies:
            with self.subTest(dependency=dependency, section="dependencies"):
                project = f'[project]\ndependencies = ["{dependency}"]\n'
                with self.assertRaisesRegex(
                    AssertionError, "directly depends on organizer distributions"
                ):
                    _assert_project_dependency_boundary(project, "fake pyproject.toml")
            with self.subTest(dependency=dependency, section="optional-dependencies"):
                project = (
                    '[project]\ndependencies = ["pydantic>=2"]\n'
                    '[project.optional-dependencies]\ndev = ["' + dependency + '"]\n'
                )
                with self.assertRaisesRegex(
                    AssertionError, "directly depends on organizer distributions"
                ):
                    _assert_project_dependency_boundary(project, "fake pyproject.toml")
            with self.subTest(dependency=dependency, artifact="wheel metadata"):
                metadata = (
                    "Metadata-Version: 2.4\nName: anvil-dream-participant\n"
                    f"Requires-Dist: {dependency}\n"
                )
                with self.assertRaisesRegex(
                    AssertionError, "directly depends on organizer distributions"
                ):
                    _assert_metadata_dependency_boundary(metadata, "fake METADATA")

        _assert_project_dependency_boundary(
            '[project]\ndescription = "Participant help for organizer workflows"\n'
            'dependencies = ["pydantic>=2"]\n',
            "fake pyproject.toml with prose",
        )
        with self.assertRaisesRegex(AssertionError, "directly depends on organizer distributions"):
            _assert_project_dependency_boundary(
                '[build-system]\nrequires = ["cm4ai-anvil-dream-tools>=0.1"]\n',
                "fake pyproject.toml build dependencies",
            )


if __name__ == "__main__":
    unittest.main()
