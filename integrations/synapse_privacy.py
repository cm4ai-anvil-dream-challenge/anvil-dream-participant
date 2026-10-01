"""Validate the effective ACL protecting a participant's Synapse files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONFIDENTIAL_ACCESS = frozenset({"READ", "DOWNLOAD"})


class SubmissionPrivacyError(RuntimeError):
    """Raised before submission when weights would be visible to another principal."""


@dataclass(frozen=True)
class PrivacyReport:
    """Result of inspecting the ACL inherited by a prospective upload."""

    entity_id: str
    benefactor_id: str
    submitter_id: str


def assert_private_to_submitter(client: Any, entity_id: str) -> PrivacyReport:
    """Fail unless only the logged-in user can read or download an entity.

    New Synapse FileEntity records inherit their benefactor's ACL. Check that
    effective ACL so a permissive parent cannot expose submitted weights.
    Challenge evaluators receive a private snapshot through the Evaluation Queue.
    """
    profile = client.getUserProfile()
    submitter_id = str(profile["ownerId"])
    benefactor = client.restGET(f"/entity/{entity_id}/benefactor")
    benefactor_id = str(benefactor["id"])
    acl = client.restGET(f"/entity/{benefactor_id}/acl")

    exposed_to: list[dict[str, Any]] = []
    submitter_permissions: set[str] = set()
    for entry in acl.get("resourceAccess", []):
        principal_id = str(entry.get("principalId"))
        access = set(entry.get("accessType", []))
        confidential_permissions = sorted(CONFIDENTIAL_ACCESS.intersection(access))
        if principal_id == submitter_id:
            submitter_permissions.update(access)
        if principal_id != submitter_id and confidential_permissions:
            exposed_to.append(
                {
                    "principal_id": principal_id,
                    "permissions": confidential_permissions,
                }
            )

    if exposed_to:
        details = ", ".join(
            f"{item['principal_id']} ({'/'.join(item['permissions'])})" for item in exposed_to
        )
        raise SubmissionPrivacyError(
            f"Refusing to upload beneath {entity_id}: ACL benefactor "
            f"{benefactor_id} exposes weights to principal(s) {details}. "
            "Use a private Synapse project/folder whose only READ/DOWNLOAD "
            "principal is the logged-in submitter."
        )

    missing = sorted(CONFIDENTIAL_ACCESS.difference(submitter_permissions))
    if missing:
        raise SubmissionPrivacyError(
            f"Refusing to use {entity_id}: logged-in principal {submitter_id} "
            f"does not have direct {'/'.join(missing)} permission on ACL "
            f"benefactor {benefactor_id}. Use a private project owned directly "
            "by the submitter, not access inherited through a team."
        )

    return PrivacyReport(
        entity_id=str(entity_id),
        benefactor_id=benefactor_id,
        submitter_id=submitter_id,
    )
