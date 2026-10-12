from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]


class ParticipantImportBoundaryTests(unittest.TestCase):
    def test_public_integrations_do_not_import_or_expose_organizer_adapters(self) -> None:
        script = """
import sys
from anvil_dream_participant.integrations.synapse import ParticipantSynapseGateway
import anvil_dream_participant.benchmarking.evaluation as benchmark
from anvil_dream_participant.benchmarking.image_results import ImageResult
from anvil_dream_participant.integrations.terra import ParticipantTerraClient
from anvil_dream_participant.progress import ProgressReporter
from anvil_dream_participant.submissions.bundle import SubmissionBundle, SubmissionBundleManifest
import anvil_dream_participant.workflows.wdl as participant_wdl

loaded = sorted(module for module in sys.modules if module == "organizer" or module.startswith("organizer."))
if loaded:
    raise AssertionError(f"public participant imports loaded internal modules: {loaded}")
if not callable(benchmark.benchmark_image_results) or ImageResult.__module__.startswith("organizer"):
    raise AssertionError("public benchmark code is not owned by the participant package")

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

try:
    main(["organizer", "--help"])
except SystemExit as error:
    if "not included in the participant package" not in str(error):
        raise
else:
    raise AssertionError("participant package accepted the organizer role")

loaded = sorted(module for module in sys.modules if module == "organizer" or module.startswith("organizer."))
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
        self.assertIn("usage: anvil-dream-participant submit", result.stdout)


if __name__ == "__main__":
    unittest.main()
