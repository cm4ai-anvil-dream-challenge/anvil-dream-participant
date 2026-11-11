from __future__ import annotations

import os
from pathlib import Path
import importlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from anvil_dream_participant.cli.root import cli


ROOT = Path(__file__).resolve().parents[3]


class ChallengeDispatchTests(unittest.TestCase):
    def test_help_and_generic_core_imports_do_not_load_default_profile(self) -> None:
        script = textwrap.dedent(
            """
            import importlib.abc
            import sys

            class DefaultProfileBlocker(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "anvil_dream_participant.profile" or fullname.startswith(
                        "anvil_dream_participant.profile."
                    ):
                        raise AssertionError(f"unexpected default profile import: {fullname}")
                    return None

            sys.meta_path.insert(0, DefaultProfileBlocker())
            from click.testing import CliRunner
            from anvil_dream_participant.cli import main
            from anvil_dream_participant.cli.root import cli
            from anvil_dream_participant.config import participant_config_for_evaluation
            from anvil_dream_participant.workflows.wdl import (
                WorkflowContractError, parse_participant_workflow,
            )

            config = participant_config_for_evaluation("12345")
            assert config["synapse"]["evaluation_id"] == "12345"
            assert "Synapse Evaluation 12345" == config["synapse"]["queue_name"]
            assert issubclass(WorkflowContractError, ValueError)

            result = CliRunner().invoke(cli, ["--help"])
            assert result.exit_code == 0, result.output + repr(result.exception)
            assert "submit-anvil" in result.output
            challenge_option = next(
                parameter for parameter in cli.params if parameter.name == "challenge_reference"
            )
            assert challenge_option.default == "anvil_dream_participant.profile:PROFILE"
            command_help = CliRunner().invoke(cli, ["submit", "--help"])
            assert command_help.exit_code == 0, command_help.output + repr(command_help.exception)
            assert set(cli.commands) == {"submit", "submit-anvil", "submission-status"}
            try:
                main(["--help"])
            except SystemExit as error:
                assert error.code == 0
            else:
                raise AssertionError("public CLI help did not exit")
            assert not any(
                name == "anvil_dream_participant.profile"
                or name.startswith("anvil_dream_participant.profile.")
                for name in sys.modules
            )
            """
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            cwd=ROOT,
            env=environment,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_config_defaults_to_the_official_queue(self) -> None:
        from anvil_dream_participant.config import participant_config_for_evaluation

        config = participant_config_for_evaluation()
        self.assertEqual(config["synapse"]["evaluation_id"], "9620108")
        self.assertEqual(config["synapse"]["queue_name"], "CM4AI-AnVIL DREAM Challenge Submissions")
        self.assertEqual(participant_config_for_evaluation("123")["synapse"]["queue_name"], "Synapse Evaluation 123")

    def test_published_cli_runner_preserves_help_and_program_name(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from anvil_dream_participant.cli.participant import run

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run(["-h"], prog="custom-participant"), 0)
        self.assertIn("Usage: custom-participant", output.getvalue())
        self.assertIn("submit-anvil", output.getvalue())
        with self.assertRaises(SystemExit) as error:
            run(["unknown-command"])
        self.assertEqual(error.exception.code, 2)

    def test_short_help_matches_long_help_for_all_submission_commands(self) -> None:
        runner = CliRunner()
        for command in ([], ["submit"], ["submit-anvil"], ["submission-status"]):
            with self.subTest(command=command):
                short = runner.invoke(cli, [*command, "-h"])
                long = runner.invoke(cli, [*command, "--help"])
                self.assertEqual(short.exit_code, 0, short.output)
                self.assertEqual(short.output, long.output)

    def test_hpa_profile_defaults_to_the_official_submission_queue(self) -> None:
        from anvil_dream_participant.profile import PROFILE

        self.assertEqual(PROFILE.evaluation_id, "9620108")
        self.assertEqual(PROFILE.queue_name, "CM4AI-AnVIL DREAM Challenge Submissions")

    def test_alternate_profile_dispatches_without_importing_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture_challenge.py"
            fixture.write_text(
                textwrap.dedent(
                    """
                    from anvil_dream_participant.challenge import ChallengeProfile

                    def validate_workflow(path):
                        return None

                    PROFILE = ChallengeProfile(
                        name="Fixture",
                        evaluation_id="23456",
                        queue_name="Fixture queue",
                        validate_workflow=validate_workflow,
                    )
                    """
                ),
                encoding="utf-8",
            )
            script = textwrap.dedent(
                """
                import importlib.abc
                import sys
                from pathlib import Path
                from unittest.mock import patch

                class DefaultProfileBlocker(importlib.abc.MetaPathFinder):
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname == "anvil_dream_participant.profile" or fullname.startswith(
                            "anvil_dream_participant.profile."
                        ):
                            raise AssertionError(f"unexpected default profile import: {fullname}")
                        return None

                sys.meta_path.insert(0, DefaultProfileBlocker())
                sys.path.insert(0, sys.argv[1])
                from click.testing import CliRunner
                from anvil_dream_participant.cli.root import cli

                with patch(
                    "anvil_dream_participant.cli.submit.submit_bundle",
                    return_value={"submission_id": "fixture-submission"},
                ) as submit:
                    result = CliRunner().invoke(
                        cli,
                        [
                            "--challenge", "fixture_challenge:PROFILE", "submit",
                            "--wdl", "workflow.wdl", "--weights", "weights.bin",
                            "--anvil-email", "person@example.org", "--evaluation-id", "34567",
                        ],
                    )
                    assert result.exit_code == 0, result.output + repr(result.exception)
                    assert '"submission_id": "fixture-submission"' in result.output
                    request = submit.call_args.args[0]
                    assert request.profile.name == "Fixture"
                    assert request.profile.queue_name == "Fixture queue"
                    assert request.evaluation_id_override == "34567"

                assert not any(
                    name == "anvil_dream_participant.profile"
                    or name.startswith("anvil_dream_participant.profile.")
                    for name in sys.modules
                )
                """
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", script, directory],
                capture_output=True,
                check=False,
                cwd=ROOT,
                env=environment,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_profile_fails_before_dispatch(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "--challenge",
                "anvil_dream_participant.profile:MISSING",
                "submit",
                "--wdl",
                "workflow.wdl",
                "--weights",
                "weights.bin",
                "--anvil-email",
                "person@example.org",
            ],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("MISSING", result.output + str(result.exception))

    def test_submit_anvil_files_option_accepts_multiple_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture_files_profile.py"
            fixture.write_text(
                textwrap.dedent(
                    """
                    from anvil_dream_participant.challenge import ChallengeProfile

                    PROFILE = ChallengeProfile(
                        name="Files fixture",
                        evaluation_id="23456",
                        queue_name="Fixture queue",
                        validate_workflow=lambda _path: None,
                    )
                    """
                ),
                encoding="utf-8",
            )
            sys.path.insert(0, directory)
            self.addCleanup(lambda: sys.path.remove(directory) if directory in sys.path else None)
            self.addCleanup(lambda: sys.modules.pop("fixture_files_profile", None))
            importlib.invalidate_caches()

            with patch(
                "anvil_dream_participant.cli.submit_anvil.submit_anvil_bundle",
                return_value={"submission_id": "files-fixture"},
            ) as submit:
                result = CliRunner().invoke(
                    cli,
                    [
                        "--challenge",
                        "fixture_files_profile:PROFILE",
                        "submit-anvil",
                        "--wdl",
                        "gs://source-bucket/workflow.wdl",
                        "--weights",
                        "gs://source-bucket/weights.bin",
                        "--files",
                        "gs://source-bucket/context/one.json",
                        "gs://source-bucket/context/two.json",
                        "--recursive",
                        "--evaluation-id",
                        "34567",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output + repr(result.exception))
        self.assertIn('"submission_id": "files-fixture"', result.output)
        request = submit.call_args.args[0]
        self.assertEqual(
            request.files,
            (
                "gs://source-bucket/context/one.json",
                "gs://source-bucket/context/two.json",
            ),
        )
        self.assertTrue(request.recursive)
        self.assertEqual(request.evaluation_id_override, "34567")

    def test_cli_does_not_expose_organizer_role(self) -> None:
        result = CliRunner().invoke(cli, ["organizer"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not included in the participant package", result.output)

    def test_command_options_reject_legacy_config_and_workspace_arguments(self) -> None:
        runner = CliRunner()
        submit_help = runner.invoke(cli, ["submit", "--help"])
        self.assertEqual(submit_help.exit_code, 0, submit_help.output)
        self.assertIn("--wdl PATH", submit_help.output)
        self.assertIn("--weights PATH", submit_help.output)
        self.assertIn("--anvil-email TEXT", submit_help.output)

        base = [
            "submit",
            "--wdl",
            "workflow.wdl",
            "--weights",
            "weights.bin",
            "--anvil-email",
            "person@example.org",
        ]
        for option in ("--config", "--workspace", "--dockerfile"):
            with self.subTest(option=option):
                result = runner.invoke(cli, [*base, option, "value"])
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("No such option", result.output)

        missing = runner.invoke(cli, ["submit"])
        self.assertNotEqual(missing.exit_code, 0)
        self.assertIn("--wdl", missing.output)
        invalid_evaluation = runner.invoke(cli, [*base, "--evaluation-id", "0"])
        self.assertNotEqual(invalid_evaluation.exit_code, 0)
        self.assertIn("positive number", invalid_evaluation.output)

        anvil_email = runner.invoke(
            cli,
            [
                "submit-anvil",
                "--wdl",
                "gs://bucket/workflow.wdl",
                "--weights",
                "gs://bucket/weights.bin",
                "--anvil-email",
                "person@example.org",
            ],
        )
        self.assertNotEqual(anvil_email.exit_code, 0)
        self.assertIn("No such option", anvil_email.output)

        registration = runner.invoke(cli, ["register"])
        self.assertNotEqual(registration.exit_code, 0)
        self.assertIn("No such command 'register'.", registration.output)

    def test_old_home_configuration_does_not_override_the_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / ".dream" / "participant.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                '[synapse]\nqueue_name = "old queue"\nevaluation_id = "wrong"\n',
                encoding="utf-8",
            )
            with (
                patch("pathlib.Path.home", return_value=Path(directory)),
                patch(
                    "anvil_dream_participant.cli.submit.submit_bundle",
                    return_value={"submission_id": "default-profile"},
                ) as submit,
            ):
                result = CliRunner().invoke(
                    cli,
                    [
                        "submit",
                        "--wdl",
                        "workflow.wdl",
                        "--weights",
                        "weights.bin",
                        "--anvil-email",
                        "person@example.org",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output + repr(result.exception))
        request = submit.call_args.args[0]
        self.assertEqual(request.profile.evaluation_id, "9620108")
        self.assertIsNone(request.evaluation_id_override)


if __name__ == "__main__":
    unittest.main()
