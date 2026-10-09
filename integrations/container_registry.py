"""Anonymous OCI registry checks for participant container images."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

import requests
from requests.utils import parse_dict_header


_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_CONTAINER_GUIDE = (
    "https://github.com/cm4ai-anvil-dream-challenge/"
    "cm4ai-anvil-dream-tools/blob/main/participant/docs/container-images.md"
)


class ContainerRegistryError(ValueError):
    """A participant image cannot be pulled anonymously by a Terra worker."""


@dataclass(frozen=True)
class PublicContainerImage:
    image: str
    registry: str
    repository: str
    digest: str
    platforms: tuple[str, ...] = ()


def _with_guide(message: str) -> str:
    return f"{message}. See {_CONTAINER_GUIDE}"


def _parse_image(image: str) -> tuple[PublicContainerImage, str]:
    try:
        name, digest = image.rsplit("@", 1)
        first, remainder = name.split("/", 1)
    except ValueError as error:
        raise ContainerRegistryError(
            _with_guide(f"container image must include a registry and digest: {image}")
        ) from error
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ContainerRegistryError(
            _with_guide(f"container image must use a SHA-256 digest: {image}")
        )
    if first in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        registry = "docker.io"
        endpoint = "registry-1.docker.io"
        if "/" not in remainder:
            remainder = f"library/{remainder}"
    elif "." in first or ":" in first or first == "localhost":
        registry = first
        endpoint = first
    else:
        registry = "docker.io"
        endpoint = "registry-1.docker.io"
        remainder = name
    return PublicContainerImage(image, registry, remainder, digest), endpoint


def _manifest_request(session: Any, url: str, headers: dict[str, str], timeout: float):
    return session.get(
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
        auth=(),
    )


def _anonymous_bearer_token(
    session: Any,
    challenge: str,
    repository: str,
    timeout: float,
) -> str:
    scheme, separator, raw_parameters = challenge.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        raise ContainerRegistryError("container registry requires non-anonymous authentication")
    parameters = parse_dict_header(raw_parameters)
    realm = parameters.get("realm")
    if not realm or not realm.startswith("https://"):
        raise ContainerRegistryError(
            "container registry returned an invalid authentication service"
        )
    query = {
        "scope": parameters.get("scope") or f"repository:{repository}:pull",
    }
    if service := parameters.get("service"):
        query["service"] = service
    response = session.get(realm, params=query, timeout=timeout, auth=())
    try:
        if response.status_code != 200:
            raise ContainerRegistryError("container image is not anonymously pullable")
        payload = response.json()
    finally:
        response.close()
    token = (
        payload.get("token") or payload.get("access_token") if isinstance(payload, dict) else None
    )
    if not isinstance(token, str) or not token:
        raise ContainerRegistryError("container registry did not return an anonymous pull token")
    return token


def _verify_content_digest(
    content: bytes,
    expected_digest: str,
    *,
    description: str,
    declared_digest: str | None = None,
) -> None:
    actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if declared_digest and declared_digest.casefold() != actual_digest.casefold():
        raise ContainerRegistryError(
            _with_guide(f"container registry returned an invalid digest header for {description}")
        )
    if actual_digest.casefold() != expected_digest.casefold():
        raise ContainerRegistryError(
            _with_guide(f"container registry returned a different digest for {description}")
        )


def _decode_json_object(content: bytes, *, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ContainerRegistryError(
            _with_guide(f"container registry returned an invalid {description}")
        ) from error
    if not isinstance(payload, dict):
        raise ContainerRegistryError(
            _with_guide(f"container registry returned an invalid {description}")
        )
    return payload


def _get_json(
    session: Any,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    expected_digest: str,
    description: str,
) -> dict[str, Any]:
    response = _manifest_request(session, url, headers, timeout)
    try:
        if response.status_code != 200:
            raise ContainerRegistryError(
                _with_guide(f"container registry returned HTTP {response.status_code} for {url}")
            )
        content = response.content
        _verify_content_digest(
            content,
            expected_digest,
            description=description,
            declared_digest=response.headers.get("Docker-Content-Digest"),
        )
    finally:
        response.close()
    return _decode_json_object(content, description=description)


def _format_platform(platform: Any) -> str | None:
    if not isinstance(platform, dict):
        return None
    operating_system = platform.get("os")
    architecture = platform.get("architecture")
    if not isinstance(operating_system, str) or not isinstance(architecture, str):
        return None
    if operating_system.casefold() == "unknown" or architecture.casefold() == "unknown":
        return None
    value = f"{operating_system}/{architecture}"
    variant = platform.get("variant")
    if isinstance(variant, str) and variant:
        value = f"{value}/{variant}"
    return value.casefold()


def _read_platforms(
    session: Any,
    manifest_url: str,
    *,
    manifest: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> tuple[str, ...]:
    entries = manifest.get("manifests")
    if isinstance(entries, list):
        platforms = {
            formatted
            for entry in entries
            if isinstance(entry, dict)
            if (formatted := _format_platform(entry.get("platform"))) is not None
        }
        return tuple(sorted(platforms))

    config = manifest.get("config")
    config_digest = config.get("digest") if isinstance(config, dict) else None
    if not isinstance(config_digest, str) or not config_digest.startswith("sha256:"):
        return ()
    repository_url, _, _ = manifest_url.rpartition("/manifests/")
    image_config = _get_json(
        session,
        f"{repository_url}/blobs/{quote(config_digest, safe=':')}",
        headers=headers,
        timeout=timeout,
        expected_digest=config_digest,
        description="image config",
    )
    platform = _format_platform(image_config)
    return (platform,) if platform else ()


def verify_public_container_image(
    image: str,
    *,
    required_platform: str = "linux/amd64",
    session: Any | None = None,
    timeout: float = 20,
) -> PublicContainerImage:
    """Require an anonymously readable image with the configured execution platform."""
    parsed, endpoint = _parse_image(image)
    url = (
        f"https://{endpoint}/v2/{quote(parsed.repository, safe='/')}/manifests/"
        f"{quote(parsed.digest, safe=':')}"
    )
    headers = {"Accept": _MANIFEST_ACCEPT}
    request_headers = headers
    own_session = session is None
    client = session or requests.Session()
    try:
        try:
            response = _manifest_request(client, url, headers, timeout)
            if response.status_code == 401:
                challenge = response.headers.get("WWW-Authenticate", "")
                response.close()
                token = _anonymous_bearer_token(client, challenge, parsed.repository, timeout)
                request_headers = {**headers, "Authorization": f"Bearer {token}"}
                response = _manifest_request(
                    client,
                    url,
                    request_headers,
                    timeout,
                )
        except requests.RequestException as error:
            raise ContainerRegistryError(
                f"could not contact container registry {parsed.registry}: {error}"
            ) from error
        try:
            if response.status_code in {401, 403, 404}:
                raise ContainerRegistryError(
                    _with_guide(
                        "container image is not anonymously pullable: "
                        f"{parsed.image}. Confirm that it was pushed and set the Quay "
                        "repository visibility to public"
                    )
                )
            if response.status_code != 200:
                raise ContainerRegistryError(
                    f"container registry returned HTTP {response.status_code} for {parsed.image}"
                )
            content = response.content
            _verify_content_digest(
                content,
                parsed.digest,
                description=parsed.image,
                declared_digest=response.headers.get("Docker-Content-Digest"),
            )
            manifest = _decode_json_object(content, description="image manifest")
        finally:
            response.close()
        try:
            platforms = _read_platforms(
                client,
                url,
                manifest=manifest,
                headers=request_headers,
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise ContainerRegistryError(
                _with_guide(
                    f"could not inspect container image platforms at {parsed.registry}: {error}"
                )
            ) from error
        required_platform = required_platform.casefold()
        if required_platform not in platforms:
            available = ", ".join(platforms) if platforms else "unknown"
            raise ContainerRegistryError(
                _with_guide(
                    f"container image does not provide the required {required_platform} "
                    f"platform (found: {available}); build and push it with "
                    f"docker buildx build --platform {required_platform} --push"
                )
            )
        return replace(parsed, platforms=platforms)
    finally:
        if own_session:
            client.close()
