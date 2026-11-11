"""Exercise the installed participant wheel without contacting external services."""

from __future__ import annotations

import json
import importlib.util
from importlib.metadata import distribution, distributions
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_public_artifacts import (
    FORBIDDEN_DISTRIBUTIONS,
    FORBIDDEN_IMPORT_ROOTS,
    _assert_no_forbidden_organizer_helper_files,
    is_forbidden_import,
    normalize_distribution_name,
)

from anvil_dream_participant.submissions.bundle import (
    ARCHIVE_MANIFEST,
    ARCHIVE_WDL,
    ARCHIVE_WEIGHTS,
    SubmissionBundle,
)
from anvil_dream_participant.workflows.wdl import parse_participant_workflow
from anvil_dream_participant.profile import PROFILE


def main(wdl_path: Path) -> None:
    modules_found = [
        name for name in FORBIDDEN_IMPORT_ROOTS if importlib.util.find_spec(name) is not None
    ]
    if modules_found:
        raise AssertionError(
            f"the installed participant environment includes organizer modules: {modules_found}"
        )
    participant_distribution = distribution("anvil-dream-participant")
    _assert_no_forbidden_organizer_helper_files(
        [str(path) for path in participant_distribution.files or ()],
        "installed participant wheel",
    )
    installed_distributions = {
        normalize_distribution_name(dist.metadata["Name"])
        for dist in distributions()
        if dist.metadata.get("Name")
    }
    distributions_found = sorted(FORBIDDEN_DISTRIBUTIONS & installed_distributions)
    if distributions_found:
        raise AssertionError(
            "the installed participant environment includes organizer distributions: "
            f"{distributions_found}"
        )
    entry_points = {
        entry_point.name: entry_point.value
        for entry_point in participant_distribution.entry_points
        if entry_point.group == "console_scripts"
    }
    if entry_points != {"anvil-dream-participant": "anvil_dream_participant.cli:main"}:
        raise AssertionError(f"participant wheel exposes unexpected commands: {entry_points}")
    if importlib.util.find_spec("anvil_dream_participant.benchmarking") is not None:
        raise AssertionError("participant wheel includes retired evaluation code")
    loaded = sorted(name for name in sys.modules if is_forbidden_import(name))
    if loaded:
        raise AssertionError(f"participant wheel smoke imported organizer-side modules: {loaded}")
    if hasattr(PROFILE, "benchmark"):
        raise AssertionError("participant profile exposes an evaluation operation")

    workflow = parse_participant_workflow(wdl_path)
    assert workflow.name == "Predict"

    with TemporaryDirectory() as directory:
        root = Path(directory)
        environment = os.environ.copy()
        environment["HOME"] = str(root)
        environment.pop("PYTHONPATH", None)
        for name in (
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
            "CLOUDSDK_CONFIG",
            "AWS_PROFILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_CONFIG_FILE",
            "SYNAPSE_AUTH_TOKEN",
            "SYNAPSE_CONFIG",
            "SYNAPSE_CACHE_DIR",
        ):
            environment.pop(name, None)

        participant_command = Path(sys.executable).with_name("anvil-dream-participant")
        if not participant_command.is_file():
            raise AssertionError(f"participant console command is missing: {participant_command}")
        for arguments, expected in (
            (["--help"], "Usage: anvil-dream-participant"),
            (["submit", "--help"], "Usage: anvil-dream-participant submit"),
            (["submit-anvil", "--help"], "Usage: anvil-dream-participant submit-anvil"),
            (
                ["submission-status", "--help"],
                "Usage: anvil-dream-participant submission-status",
            ),
        ):
            result = subprocess.run(
                [str(participant_command), *arguments],
                capture_output=True,
                check=False,
                cwd=root,
                env=environment,
                text=True,
                timeout=30,
            )
            if result.returncode != 0 or expected not in result.stdout:
                raise AssertionError(
                    f"participant command {arguments} failed: {result.returncode}\n{result.stderr}"
                )

        weights = root / "weights.bin"
        weights.write_bytes(b"offline test weights")
        other_files = root / "other-files"
        config = other_files / "model" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"batch_size": 4}', encoding="utf-8")

        bundle = SubmissionBundle.create(
            wdl=wdl_path,
            weights=weights,
            other_files=other_files,
            synapse_user_id="123456",
            anvil_email="participant@example.org",
            anvil_subject_id="offline-test-subject",
        )
        archive_path = bundle.write_archive(root / "submission.tar")
        with tarfile.open(archive_path, mode="r:") as archive:
            assert [member.name for member in archive] == [
                ARCHIVE_MANIFEST,
                ARCHIVE_WDL,
                ARCHIVE_WEIGHTS,
                "other/model/config.json",
            ]
            assert archive.extractfile(ARCHIVE_WDL).read() == wdl_path.read_bytes()
            assert archive.extractfile(ARCHIVE_WEIGHTS).read() == weights.read_bytes()
            assert archive.extractfile("other/model/config.json").read() == config.read_bytes()
            manifest = json.load(archive.extractfile(ARCHIVE_MANIFEST))
            assert manifest["synapse_user_id"] == "123456"
            assert manifest["full_fingerprint"] == bundle.manifest.full_fingerprint


    print(
        "Installed participant wheel created a submission bundle without evaluation code."
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
