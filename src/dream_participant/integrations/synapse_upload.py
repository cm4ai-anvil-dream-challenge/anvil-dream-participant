"""Store private participant bundles and submit them to a Synapse queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapseclient import File

from .synapse_privacy import PrivacyReport, SubmissionPrivacyError
from .synapse_reuse import (
    BUNDLE_SHA256_ANNOTATION,
    SubmissionBundleFingerprint,
    entity_value,
    matches_submission_bundle,
)


@dataclass(frozen=True)
class ParticipantSubmissionResult:
    submission: Any
    reused: bool


def upload_and_submit(
    client: Any,
    evaluation: Any,
    path: str,
    parent_id: str,
    submission_name: str,
    team: str | None,
    *,
    privacy_check: Callable[[str], PrivacyReport],
) -> ParticipantSubmissionResult:
    """Reuse or privately upload a deterministic bundle, then submit it.

    Check the parent before lookup and the selected entity before submission.
    Check every new upload again after Synapse stores it.
    """
    parent_privacy = privacy_check(parent_id)
    fingerprint = SubmissionBundleFingerprint.from_path(path)
    existing_id = client.findEntityId(Path(path).name, parent=parent_id)
    entity = None
    reused = False
    if existing_id is not None:
        existing = client.get(str(existing_id), downloadFile=False)
        existing_parent = entity_value(existing, "parentId")
        existing_creator = entity_value(existing, "createdBy")
        if (
            str(entity_value(existing, "id")) != str(existing_id)
            or str(existing_parent) != parent_id
            or str(existing_creator) != parent_privacy.submitter_id
        ):
            raise SubmissionPrivacyError(
                f"Refusing to use existing submission TAR {existing_id}: it does "
                "not belong to the configured private parent and logged-in submitter."
            )
        privacy_check(str(existing_id))
        if matches_submission_bundle(existing, fingerprint):
            entity = existing
            reused = True

    if entity is None:
        previous_silent = getattr(client, "silent", False)
        client.silent = False
        try:
            entity = client.store(
                File(
                    path=path,
                    parent=parent_id,
                    annotations={BUNDLE_SHA256_ANNOTATION: fingerprint.sha256},
                )
            )
        finally:
            client.silent = previous_silent
        try:
            privacy_check(str(entity.id))
        except SubmissionPrivacyError:
            if existing_id is not None and str(entity.id) == str(existing_id):
                client.delete(entity, version=entity.versionNumber)
            else:
                client.delete(entity)
            raise

    kwargs: dict[str, Any] = {"name": submission_name}
    if team:
        kwargs["team"] = team
    submission = client.submit(evaluation, entity, **kwargs)
    return ParticipantSubmissionResult(submission=submission, reused=reused)
