import unittest
from unittest.mock import Mock, call

from dream_participant.integrations.synapse_privacy import (
    PrivacyReport,
    SubmissionPrivacyError,
    assert_private_to_submitter,
)


class SynapsePrivacyTests(unittest.TestCase):
    def test_rejects_read_or_download_access_for_another_principal(self) -> None:
        for exposed_access in ("READ", "DOWNLOAD"):
            with self.subTest(access=exposed_access):
                client = Mock()
                client.getUserProfile.return_value = {"ownerId": "123"}
                client.restGET.side_effect = [
                    {"id": "synParent"},
                    {
                        "resourceAccess": [
                            {"principalId": "123", "accessType": ["READ", "DOWNLOAD"]},
                            {"principalId": "team7", "accessType": [exposed_access]},
                        ]
                    },
                ]

                with self.assertRaisesRegex(
                    SubmissionPrivacyError,
                    rf"team7 \({exposed_access}\)",
                ):
                    assert_private_to_submitter(client, "synFolder")

                self.assertEqual(
                    client.restGET.call_args_list,
                    [
                        call("/entity/synFolder/benefactor"),
                        call("/entity/synParent/acl"),
                    ],
                )

    def test_requires_direct_read_and_download_for_submitter(self) -> None:
        client = Mock()
        client.getUserProfile.return_value = {"ownerId": "123"}
        client.restGET.side_effect = [
            {"id": "synParent"},
            {"resourceAccess": [{"principalId": "123", "accessType": ["READ"]}]},
        ]

        with self.assertRaisesRegex(SubmissionPrivacyError, "DOWNLOAD"):
            assert_private_to_submitter(client, "synFolder")

    def test_accepts_private_acl_and_ignores_non_confidential_permissions(self) -> None:
        client = Mock()
        client.getUserProfile.return_value = {"ownerId": 123}
        client.restGET.side_effect = [
            {"id": "synParent"},
            {
                "resourceAccess": [
                    {"principalId": 123, "accessType": ["READ", "DOWNLOAD"]},
                    {"principalId": "other", "accessType": ["UPDATE"]},
                ]
            },
        ]

        report = assert_private_to_submitter(client, "synFolder")

        self.assertEqual(report, PrivacyReport("synFolder", "synParent", "123"))
