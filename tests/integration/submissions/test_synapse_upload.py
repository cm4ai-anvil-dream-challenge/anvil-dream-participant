import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from dream_participant.integrations.synapse import (
    ParticipantSynapseGateway,
    SubmissionPrivacyError,
)


class SynapseUploadTests(unittest.TestCase):
    def test_identical_private_tar_reuses_entity_and_submits_new_queue_record(self) -> None:
        payload = b"private bundle"
        existing = SimpleNamespace(
            id="synExisting",
            parentId="synParent",
            createdBy="123",
            md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            fileSize=len(payload),
            annotations={"dreamBundleSha256": hashlib.sha256(payload).hexdigest()},
            versionNumber=5,
        )
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.client.findEntityId.return_value = "synExisting"
        gateway.client.get.return_value = existing
        gateway.client.submit.return_value = SimpleNamespace(id="55", entityId="synExisting")
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(
            side_effect=lambda entity_id: SimpleNamespace(submitter_id="123", entity_id=entity_id)
        )

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(payload)
            result = gateway.upload_and_submit(str(archive), "synParent", "run", None)

        self.assertTrue(result.reused)
        self.assertEqual(result.submission.id, "55")
        gateway.client.findEntityId.assert_called_once_with("submission.tar", parent="synParent")
        gateway.client.get.assert_called_once_with("synExisting", downloadFile=False)
        gateway.client.store.assert_not_called()
        gateway.client.submit.assert_called_once_with("synQueue", existing, name="run")
        self.assertEqual(
            [call.args[0] for call in gateway.assert_private_to_submitter.call_args_list],
            ["synParent", "synExisting"],
        )

    def test_changed_private_tar_uploads_with_sha256_annotation(self) -> None:
        existing_payload = b"older bundle"
        new_payload = b"changed bundle"
        existing = SimpleNamespace(
            id="synExisting",
            parentId="synParent",
            createdBy="123",
            md5=hashlib.md5(existing_payload, usedforsecurity=False).hexdigest(),
            fileSize=len(existing_payload),
            annotations={},
            versionNumber=1,
        )
        uploaded = SimpleNamespace(id="synUploaded")
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.client.findEntityId.return_value = "synExisting"
        gateway.client.get.return_value = existing
        gateway.client.store.return_value = uploaded
        gateway.client.submit.return_value = SimpleNamespace(id="56", entityId="synUploaded")
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(return_value=SimpleNamespace(submitter_id="123"))

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(new_payload)
            result = gateway.upload_and_submit(str(archive), "synParent", "run", "team7")

        self.assertFalse(result.reused)
        stored_file = gateway.client.store.call_args.args[0]
        self.assertEqual(stored_file.path, str(archive))
        self.assertEqual(stored_file.parentId, "synParent")
        self.assertEqual(
            stored_file.annotations["dreamBundleSha256"],
            hashlib.sha256(new_payload).hexdigest(),
        )
        gateway.client.submit.assert_called_once_with(
            "synQueue", uploaded, name="run", team="team7"
        )

    def test_legacy_private_tar_is_reuploaded_with_sha256_annotation(self) -> None:
        payload = b"legacy bundle"
        existing = SimpleNamespace(
            id="synExisting",
            parentId="synParent",
            createdBy="123",
            md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            fileSize=len(payload),
            annotations={},
            versionNumber=5,
        )
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.client.findEntityId.return_value = "synExisting"
        gateway.client.get.return_value = existing
        uploaded = SimpleNamespace(id="synExisting", versionNumber=6)
        gateway.client.store.return_value = uploaded
        gateway.client.submit.return_value = SimpleNamespace(id="57", entityId="synExisting")
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(return_value=SimpleNamespace(submitter_id="123"))

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(payload)
            result = gateway.upload_and_submit(str(archive), "synParent", "run", None)

        self.assertFalse(result.reused)
        stored_file = gateway.client.store.call_args.args[0]
        self.assertEqual(
            stored_file.annotations["dreamBundleSha256"],
            hashlib.sha256(payload).hexdigest(),
        )
        gateway.client.submit.assert_called_once_with("synQueue", uploaded, name="run")

    def test_conflicting_sha256_annotation_prevents_reuse(self) -> None:
        payload = b"matching MD5 and size"
        existing = SimpleNamespace(
            id="synExisting",
            parentId="synParent",
            createdBy="123",
            md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            fileSize=len(payload),
            annotations={"dreamBundleSha256": "f" * 64},
            versionNumber=2,
        )
        uploaded = SimpleNamespace(id="synUploaded")
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.client.findEntityId.return_value = "synExisting"
        gateway.client.get.return_value = existing
        gateway.client.store.return_value = uploaded
        gateway.client.submit.return_value = SimpleNamespace(id="58", entityId="synUploaded")
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(return_value=SimpleNamespace(submitter_id="123"))

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(payload)
            result = gateway.upload_and_submit(str(archive), "synParent", "run", None)

        self.assertFalse(result.reused)
        gateway.client.store.assert_called_once()
        gateway.client.submit.assert_called_once_with("synQueue", uploaded, name="run")

    def test_entity_from_another_parent_or_submitter_fails_closed(self) -> None:
        payload = b"private bundle"
        cases = (
            ("different parent", "synOtherParent", "123"),
            ("different submitter", "synParent", "456"),
            ("unexpected entity ID", "synParent", "123", "synOtherEntity"),
        )
        for case in cases:
            with self.subTest(case=case[0]), tempfile.TemporaryDirectory() as directory:
                _, parent_id, creator_id, *entity_ids = case
                existing = SimpleNamespace(
                    id=entity_ids[0] if entity_ids else "synExisting",
                    parentId=parent_id,
                    createdBy=creator_id,
                    md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
                    fileSize=len(payload),
                    annotations={},
                    versionNumber=1,
                )
                gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
                gateway.client = Mock()
                gateway.client.findEntityId.return_value = "synExisting"
                gateway.client.get.return_value = existing
                gateway.evaluation = Mock(return_value="synQueue")
                gateway.assert_private_to_submitter = Mock(
                    return_value=SimpleNamespace(submitter_id="123")
                )
                archive = Path(directory) / "submission.tar"
                archive.write_bytes(payload)

                with self.assertRaisesRegex(SubmissionPrivacyError, "Refusing to use"):
                    gateway.upload_and_submit(str(archive), "synParent", "run", None)

                gateway.client.store.assert_not_called()
                gateway.client.submit.assert_not_called()

    def test_parent_privacy_failure_stops_lookup_and_upload(self) -> None:
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(
            side_effect=SubmissionPrivacyError("not private")
        )

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(b"bundle")
            with self.assertRaisesRegex(SubmissionPrivacyError, "not private"):
                gateway.upload_and_submit(str(archive), "synParent", "run", None)

        gateway.client.findEntityId.assert_not_called()
        gateway.client.store.assert_not_called()
        gateway.client.submit.assert_not_called()

    def test_privacy_failure_deletes_only_new_version_of_existing_tar(self) -> None:
        gateway = ParticipantSynapseGateway.__new__(ParticipantSynapseGateway)
        gateway.client = Mock()
        gateway.client.findEntityId.return_value = "synExisting"
        gateway.client.get.return_value = SimpleNamespace(
            id="synExisting",
            parentId="synParent",
            createdBy="123",
            md5="old-hash",
            fileSize=1,
            annotations={},
            versionNumber=5,
        )
        new_version = SimpleNamespace(id="synExisting", versionNumber=6)
        gateway.client.store.return_value = new_version
        gateway.evaluation = Mock(return_value="synQueue")
        gateway.assert_private_to_submitter = Mock(
            side_effect=[
                SimpleNamespace(submitter_id="123"),
                SimpleNamespace(submitter_id="123"),
                SubmissionPrivacyError("not private"),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "submission.tar"
            archive.write_bytes(b"new bundle")
            with self.assertRaisesRegex(SubmissionPrivacyError, "not private"):
                gateway.upload_and_submit(str(archive), "synParent", "run", None)

        gateway.client.delete.assert_called_once_with(new_version, version=6)
        gateway.client.submit.assert_not_called()
