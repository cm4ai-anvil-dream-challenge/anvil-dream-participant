from __future__ import annotations

import unittest
from unittest.mock import patch

from synapseclient.core.exceptions import SynapseNoCredentialsError

from dream_participant.config import ParticipantSynapseConfig
from dream_participant.integrations.synapse import (
    ParticipantSynapseGateway,
    SynapseCredentialsError,
)


class SynapseAuthenticationTests(unittest.TestCase):
    def test_missing_profile_has_recovery_instructions(self) -> None:
        config = ParticipantSynapseConfig(
            queue_name="challenge",
            evaluation_id="9620030",
            submission_parent_id="syn123",
        )
        with patch(
            "dream_participant.integrations.synapse.synapseclient.login",
            side_effect=SynapseNoCredentialsError("expired PAT"),
        ):
            with self.assertRaises(SynapseCredentialsError) as raised:
                ParticipantSynapseGateway(config)
        message = str(raised.exception)
        self.assertIn("synapse config", message)
        self.assertIn("synapse login", message)
        self.assertIn("~/.synapseConfig", message)


if __name__ == "__main__":
    unittest.main()
