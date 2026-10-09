from __future__ import annotations

import unittest

from dream_participant.submissions.status import ParticipantSubmissionStatus


class ParticipantStatusTests(unittest.TestCase):
    def test_projection_contains_only_public_annotation_fields(self) -> None:
        status = ParticipantSubmissionStatus.from_status(
            "123",
            {
                "status": "OPEN",
                "submissionAnnotations": {
                    "dream_state": "succeeded",
                    "comparison_status": "SUCCEEDED",
                    "comparison_shard_count": "10",
                    "error": None,
                    "private_table_id": "must-not-escape",
                },
                "entityId": "private-entity",
            },
        )
        self.assertEqual(
            status.as_dict(),
            {
                "submission_id": "123",
                "status": "OPEN",
                "dream_state": "succeeded",
                "comparison_status": "SUCCEEDED",
                "comparison_shard_count": 10,
                "error": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
