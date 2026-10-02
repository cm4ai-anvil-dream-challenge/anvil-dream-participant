"""Participant Google ADC and Terra AnVIL identity adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import google.auth
import requests
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.auth.transport.requests import Request


class _TerraApiConfig(Protocol):
    sam_url: str


class TerraApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AnvilIdentity:
    """The verified Google identity that Terra associates with a user."""

    email: str
    subject_id: str

    def __post_init__(self) -> None:
        email = str(self.email or "").strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise TerraApiError("Terra identity email is not valid")
        subject_id = str(self.subject_id or "").strip()
        if not subject_id:
            raise TerraApiError("Terra identity did not include a stable subject ID")
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "subject_id", subject_id)


def _google_adc_error() -> TerraApiError:
    return TerraApiError(
        "Google Application Default Credentials are missing or invalid. "
        "Run `gcloud auth application-default login`, then verify with "
        "`gcloud auth application-default print-access-token >/dev/null`. "
        "Google ADC is separate from Synapse authentication."
    )


def _google_adc_transport_error() -> TerraApiError:
    return TerraApiError(
        "Could not contact Google's authentication service to refresh Application "
        "Default Credentials. Check the network connection and try again. If it "
        "continues, run `gcloud auth application-default login`."
    )


class ParticipantTerraClient:
    """Authenticate to Terra and verify the caller's registered AnVIL identity."""

    def __init__(self, config: _TerraApiConfig):
        self.config = config
        try:
            self.credentials, self.google_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except DefaultCredentialsError as error:
            raise _google_adc_error() from error
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        # Refresh lazily so long-running `watch` processes do not reuse an
        # expired OAuth access token.
        if not self.credentials.valid:
            try:
                self.credentials.refresh(Request())
            except TransportError as error:
                raise _google_adc_transport_error() from error
            except (DefaultCredentialsError, RefreshError) as error:
                raise _google_adc_error() from error
        return {
            "Authorization": f"Bearer {self.credentials.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request_to(
            method, path, base_url=self.config.sam_url, service="Sam", **kwargs
        )

    def _request_to(
        self, method: str, path: str, *, base_url: str, service: str, **kwargs: Any
    ) -> Any:
        response = self.session.request(
            method,
            f"{base_url}{path}",
            headers=self._headers(),
            timeout=90,
            **kwargs,
        )
        if not response.ok:
            if response.status_code == 401:
                if service != "Sam":
                    raise TerraApiError(
                        f"{service} rejected the Google Application Default Credentials "
                        f"with HTTP 401 while calling {method} {path}.",
                        status_code=401,
                    )
                raise TerraApiError(
                    "Terra Sam rejected the Google Application Default Credentials "
                    f"with HTTP 401 while calling {method} {path}. ADC exists, but "
                    "Terra did not accept that identity. Sign into app.terra.bio once "
                    "with the same Google account and complete registration or terms prompts. Then "
                    "reset ADC with `gcloud auth application-default revoke` followed "
                    "by `gcloud auth application-default login`. This authentication "
                    "failure is unrelated to the directory from which the script runs.",
                    status_code=401,
                )
            detail = response.text[:2000]
            raise TerraApiError(
                f"{service} {method} {path} returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            return response.text

    def authenticated_anvil_identity(self, claimed_email: str) -> AnvilIdentity:
        """Verify the claimed email, OIDC subject, and Terra account registration."""
        claimed = str(claimed_email or "").strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", claimed):
            raise TerraApiError("--anvil-email must be a valid email address")
        response = self.session.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers=self._headers(),
            timeout=30,
        )
        if not response.ok:
            raise TerraApiError(
                "Could not verify the authenticated Google account. Refresh Google "
                "Application Default Credentials with `gcloud auth application-default login`.",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except requests.exceptions.JSONDecodeError as error:
            raise TerraApiError("Google identity response was not valid JSON") from error
        email = str(payload.get("email") or "").strip().casefold()
        subject_id = str(payload.get("sub") or "").strip()
        if payload.get("email_verified") is not True or not email or not subject_id:
            raise TerraApiError(
                "Google identity response did not contain a verified email and stable subject"
            )
        if email != claimed:
            raise TerraApiError(
                f"Claimed AnVIL email {claimed!r} does not match the authenticated Google "
                f"account {email!r}."
            )
        return self._registered_anvil_identity(email)

    def authenticated_anvil_identity_from_sam(self) -> AnvilIdentity:
        """Resolve the VM credential to its registered Terra user through Sam."""
        return self._registered_anvil_identity()

    def _registered_anvil_identity(self, expected_email: str | None = None) -> AnvilIdentity:
        try:
            registration = self._request(
                "GET",
                "/register/user/v2/self/info",
            )
        except TerraApiError as error:
            if error.status_code == 401:
                raise TerraApiError(
                    "Terra Sam rejected the Google credentials used by this command. "
                    "Check that this VM belongs to your registered Terra account, then retry.",
                    status_code=401,
                ) from error
            if error.status_code == 404:
                raise TerraApiError(
                    "Terra has no registered account for the authenticated Google identity. "
                    "Open app.terra.bio, complete Terra registration with this Google "
                    "account, then retry.",
                    status_code=404,
                ) from error
            raise
        if not isinstance(registration, dict):
            raise TerraApiError("Terra self-info response was not an object")
        user = registration.get("user", {})
        nested_email = user.get("email") if isinstance(user, dict) else None
        registered_email = (
            str(registration.get("userEmail") or registration.get("email") or nested_email or "")
            .strip()
            .casefold()
        )
        nested_subject = user.get("userSubjectId") if isinstance(user, dict) else None
        sam_subject_id = str(registration.get("userSubjectId") or nested_subject or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", registered_email) or not sam_subject_id:
            raise TerraApiError(
                "Terra self-info did not include the registered email and stable userSubjectId; "
                "complete Terra registration, then retry."
            )
        if expected_email is not None and registered_email != expected_email:
            raise TerraApiError("Terra self-info identity does not match the authenticated account")
        return AnvilIdentity(registered_email, sam_subject_id)


__all__ = ["AnvilIdentity", "ParticipantTerraClient", "TerraApiError"]
