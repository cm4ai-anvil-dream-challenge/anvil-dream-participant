from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path


from anvil_dream_participant.workflows.wdl import (
    WorkflowContractError,
    parse_participant_workflow,
    validate_workflow_images,
)


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/proticelli/participant.wdl"


class WorkflowContractTests(unittest.TestCase):

    def test_reference_implements_model_neutral_interface(self) -> None:
        image = "ghcr.io/example/participant-model@sha256:" + "b" * 64
        source, replacements = re.subn(
            r'(?m)^(\s*docker:\s*)"[^"]+"$',
            rf'\1"{image}"',
            EXAMPLE.read_text(encoding="utf-8"),
            count=1,
        )
        self.assertEqual(replacements, 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            workflow = parse_participant_workflow(path)
        self.assertEqual(workflow.name, "Predict")
        self.assertEqual(validate_workflow_images(workflow), (image,))
        self.assertEqual(set(workflow.tasks[0].runtime), {"docker"})

    def test_submitted_runtime_rejects_every_execution_attribute(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8")
        execution_attributes = (
            "cpu: 8",
            'memory: "32 GB"',
            'disks: "local-disk 75 SSD"',
            "gpuCount: 1",
            'gpuType: "nvidia-l4"',
            "preemptible: 100",
            "maxRetries: 0",
            "noAddress: false",
            'predefinedMachineType: "g2-standard-8"',
            'zones: "us-central1-a"',
            'timeout: "60"',
        )
        for attribute in execution_attributes:
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "participant.wdl"
                path.write_text(
                    source.replace("  runtime {", f"  runtime {{\n    {attribute}"),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(WorkflowContractError, "unsupported"):
                    parse_participant_workflow(path)

    def test_model_specific_top_level_input_is_rejected(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "    File weights\n  }",
            "    File weights\n    Int inference_steps\n  }",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "unsupported inference_steps"):
                parse_participant_workflow(path)

    def test_participant_cannot_declare_the_organizer_timeout(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "    File weights\n  }",
            "    File weights\n    Int max_runtime_seconds\n  }",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                WorkflowContractError,
                "unsupported max_runtime_seconds",
            ):
                parse_participant_workflow(path)


    def test_exact_contract_rejects_precomputed_inputs_manifest(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            '--inputs-manifest "~{write_json(inputs)}"',
            '--inputs-manifest "~{inputs_manifest}"',
        ).replace(
            "  command <<<",
            "  File inputs_manifest = write_json(inputs)\n\n  command <<<",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "post-input declarations"):
                parse_participant_workflow(path, require_exact_participant_shape=True)

    def test_exact_contract_rejects_missing_command_manifest(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            '--inputs-manifest "~{write_json(inputs)}"',
            '--inputs-manifest "inputs.json"',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "write_json\\(inputs\\)"):
                parse_participant_workflow(path, require_exact_participant_shape=True)

    def test_participant_contract_rejects_non_structured_inputs(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "    Array[PredictionInput] inputs\n",
            "    Array[String] inputs\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, r"Array\[PredictionInput\]"):
                parse_participant_workflow(path)

    def test_participant_contract_rejects_invalid_input_and_output_shapes(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8")
        variants = (
            (
                "input",
                source.replace(
                    "Array[PredictionInput] inputs",
                    "Array[PredictionInput]+ inputs",
                    1,
                ),
                r"input inputs must be a non-optional Array\[PredictionInput\]",
            ),
            (
                "output",
                source.replace(
                    "Array[File] predictions = glob",
                    "Array[File]+ predictions = glob",
                    1,
                ),
                r"predictions must be a non-optional Array\[File\]",
            ),
            (
                "optional-input",
                source.replace("File weights", "File? weights", 1),
                r"input weights must be a non-optional File",
            ),
            (
                "wrong-output-item-type",
                source.replace("Array[File] predictions", "Array[String] predictions", 1),
                r"predictions must be a non-optional Array\[File\]",
            ),
            (
                "optional-output",
                source.replace("Array[File] predictions", "Array[File]? predictions", 1),
                r"predictions must be a non-optional Array\[File\]",
            ),
            (
                "extra-output",
                source.replace(
                    'Array[File] predictions = glob("predictions/*.png")',
                    'Array[File] predictions = glob("predictions/*.png")\n    String status = "ok"',
                    1,
                ),
                "must expose only the predictions output",
            ),
        )
        for name, candidate, message in variants:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "participant.wdl"
                path.write_text(candidate, encoding="utf-8")
                with self.assertRaisesRegex(WorkflowContractError, message):
                    parse_participant_workflow(path)

    def test_participant_contract_rejects_changed_sequence_input_shape(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "  File fasta_file\n",
            "  String fasta_file\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                WorkflowContractError,
                "PredictionInput.fasta_file must be a non-optional File",
            ):
                parse_participant_workflow(path)


    def test_exact_contract_rejects_shell_syntax_from_interpolation(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "python3 /opt/proticelli/predict.py",
            'echo ~{"ok; sleep 999"}',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "interpolations may use only"):
                parse_participant_workflow(path, require_exact_participant_shape=True)

    def test_participant_workflow_wrapper_is_rejected(self) -> None:
        source = """version 1.0
workflow ParticipantWorkflow {
  call Predict
}
struct PredictionInput {
  File image
  String target_protein
  String cell_line
}
task Predict {
  input {
    Array[PredictionInput] inputs
    File weights
  }
  command <<< true >>>
  output { Array[File] predictions = [] }
  runtime {
    docker: "ghcr.io/example/predict@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cpu: 8
    memory: "32 GB"
    disks: "local-disk 75 SSD"
    gpuCount: 1
    gpuType: "nvidia-l4"
    preemptible: 100
    maxRetries: 0
    noAddress: false
  }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant.wdl"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "no workflow"):
                parse_participant_workflow(path)

    def test_imports_and_mutable_images_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imported = root / "imported.wdl"
            imported.write_text('version 1.0\nimport "other.wdl"\n', encoding="utf-8")
            with self.assertRaisesRegex(WorkflowContractError, "imports"):
                parse_participant_workflow(imported)

            mutable = root / "mutable.wdl"
            mutable.write_text(
                re.sub(r"@sha256:[0-9a-f]{64}", ":latest", EXAMPLE.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowContractError, "digest-pinned"):
                parse_participant_workflow(mutable)
