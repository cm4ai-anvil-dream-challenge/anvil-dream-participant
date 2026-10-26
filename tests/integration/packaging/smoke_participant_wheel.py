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
from anvil_dream_participant.challenges.hpa.workflows import parse_participant_workflow
from anvil_dream_participant.challenges.hpa.benchmarking.aggregate import EvaluationAggregate
from anvil_dream_participant.challenges.hpa.benchmarking.image_results import (
    ImageResult,
    PIXELS_PER_IMAGE,
)
from anvil_dream_participant.challenges.hpa.benchmarking.metrics import ComparisonMetrics
from anvil_dream_participant.challenges.hpa.benchmarking.score import ScoreResult
from anvil_dream_participant.challenges.hpa.benchmarking import evaluation as benchmark
from anvil_dream_participant.challenges.hpa.benchmarking import image_results as image_result_contract


def main(wdl_path: Path) -> None:
    modules_found = [
        name for name in FORBIDDEN_IMPORT_ROOTS if importlib.util.find_spec(name) is not None
    ]
    if modules_found:
        raise AssertionError(
            "the installed participant environment includes organizer modules: "
            f"{modules_found}"
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
    if (
        importlib.util.find_spec("anvil_dream_participant.challenges.hpa.benchmarking.tasks")
        is not None
    ):
        raise AssertionError("participant wheel includes organizer grading task commands")
    loaded = sorted(name for name in sys.modules if is_forbidden_import(name))
    if loaded:
        raise AssertionError(f"participant wheel smoke imported organizer-side modules: {loaded}")
    if not callable(benchmark.benchmark_image_results):
        raise AssertionError("participant wheel does not expose the benchmark implementation")
    if hasattr(image_result_contract, "write_image_results"):
        raise AssertionError("participant wheel exposes the organizer grader's JSONL writer")

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

        image_result = ImageResult(
            sample_id="cell-0",
            metrics=ComparisonMetrics.from_sums(
                absolute_error_sum=0.0,
                squared_error_sum=0.0,
                dot_product_sum=0.0,
                prediction_square_sum=0.0,
                target_square_sum=0.0,
                maximum_absolute_error=0.0,
                element_count=PIXELS_PER_IMAGE,
            ),
        )
        image_results = root / "image-results.jsonl"
        image_results.write_text(
            json.dumps(image_result.as_dict(), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "anvil_dream_participant.cli.benchmark",
                "--image-results",
                str(image_results),
                "--comparison-report",
                str(root / "comparison-report.json"),
                "--aggregate",
                str(root / "evaluation-aggregate.json"),
                "--output",
                str(root / "score-result.json"),
            ],
            check=True,
            cwd=root,
            env=environment,
        )
        aggregate = EvaluationAggregate.from_dict(
            json.loads((root / "evaluation-aggregate.json").read_text(encoding="utf-8"))
        )
        score = ScoreResult.from_dict(
            json.loads((root / "score-result.json").read_text(encoding="utf-8"))
        )
        report = json.loads((root / "comparison-report.json").read_text(encoding="utf-8"))
        assert aggregate.record_count == 1
        assert aggregate.metrics.mean_absolute_error == 0.0
        assert report["compared_pixels"] == 512 * 512
        assert score.score == 1.0
        assert score.higher_is_better

    print("Installed participant wheel created a submission bundle and ran the benchmark offline.")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
