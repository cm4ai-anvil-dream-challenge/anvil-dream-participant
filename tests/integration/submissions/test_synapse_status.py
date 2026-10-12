import unittest
from unittest.mock import Mock

from anvil_dream_participant.integrations.synapse_status import read_submission_status


class SynapseStatusTests(unittest.TestCase):
    def test_reads_only_the_participant_safe_status_projection(self) -> None:
        client = Mock()
        client.getSubmissionStatus.return_value = {
            "status": "PROCESSING",
            "submissionAnnotations": {
                "dream_state": "evaluating",
                "comparison_status": "running",
                "comparison_shard_count": "4",
                "error": "",
                "private_internal_value": "hidden",
            },
        }

        status = read_submission_status(client, " 42 ")

        self.assertEqual(client.getSubmissionStatus.call_args.args, ("42",))
        self.assertEqual(
            status.as_dict(),
            {
                "submission_id": "42",
                "status": "PROCESSING",
                "dream_state": "evaluating",
                "comparison_status": "running",
                "comparison_shard_count": 4,
                "error": None,
            },
        )

    def test_rejects_blank_id_before_synapse_access(self) -> None:
        client = Mock()

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            read_submission_status(client, "  ")

        client.getSubmissionStatus.assert_not_called()
