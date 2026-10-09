from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any

from dream_participant.integrations.container_registry import (
    ContainerRegistryError,
    verify_public_container_image,
)


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _image_reference(digest: str) -> str:
    return f"ghcr.io/example/predict@{digest}"


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content if content is not None else _json_bytes(payload)
        self.headers = dict(headers or {})
        self.closed = False

    def json(self) -> Any:
        return json.loads(self.content)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def _manifest_fixture(
    payload: dict[str, Any],
    *,
    include_digest_header: bool = True,
) -> tuple[FakeResponse, str]:
    response = FakeResponse(200, payload)
    digest = _digest(response.content)
    if include_digest_header:
        response.headers["Docker-Content-Digest"] = digest
    return response, _image_reference(digest)


def _index(*platforms: tuple[str, str]) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "manifests": [
            {
                "digest": f"sha256:{index:064x}",
                "platform": {"os": operating_system, "architecture": architecture},
            }
            for index, (operating_system, architecture) in enumerate(platforms, start=1)
        ],
    }


class ContainerRegistryTests(unittest.TestCase):
    def test_accepts_oci_index_that_contains_amd64_and_arm64(self) -> None:
        response, image_ref = _manifest_fixture(
            _index(("linux", "amd64"), ("linux", "arm64"))
        )
        session = FakeSession(response)

        image = verify_public_container_image(image_ref, session=session)

        self.assertEqual(image.platforms, ("linux/amd64", "linux/arm64"))
        self.assertTrue(response.closed)
        url, request = session.requests[0]
        self.assertEqual(
            url,
            f"https://ghcr.io/v2/example/predict/manifests/{_digest(response.content)}",
        )
        self.assertIn("application/vnd.oci.image.index.v1+json", request["headers"]["Accept"])
        self.assertEqual(request["auth"], ())
        self.assertTrue(request["allow_redirects"])

    def test_accepts_digest_pinned_manifest_without_digest_header(self) -> None:
        response, image_ref = _manifest_fixture(
            _index(("linux", "amd64")),
            include_digest_header=False,
        )
        session = FakeSession(response)

        image = verify_public_container_image(image_ref, session=session)

        self.assertEqual(image.platforms, ("linux/amd64",))
        self.assertNotIn("Docker-Content-Digest", response.headers)

    def test_accepts_docker_manifest_list_with_amd64(self) -> None:
        response, image_ref = _manifest_fixture(
            _index(("linux", "amd64"), ("linux", "arm64"))
        )
        response.headers["Content-Type"] = (
            "application/vnd.docker.distribution.manifest.list.v2+json"
        )
        session = FakeSession(response)

        image = verify_public_container_image(image_ref, session=session)

        self.assertIn("linux/amd64", image.platforms)
        self.assertIn(
            "application/vnd.docker.distribution.manifest.list.v2+json",
            session.requests[0][1]["headers"]["Accept"],
        )

    def test_rejects_arm_only_index(self) -> None:
        response, image_ref = _manifest_fixture(_index(("linux", "arm64")))
        session = FakeSession(response)

        with self.assertRaisesRegex(
            ContainerRegistryError,
            r"required linux/amd64 platform \(found: linux/arm64\)",
        ):
            verify_public_container_image(image_ref, session=session)

        self.assertTrue(response.closed)
        self.assertEqual(len(session.requests), 1)

    def test_reads_amd64_from_single_image_config_after_digest_check(self) -> None:
        config_response = FakeResponse(200, {"os": "linux", "architecture": "amd64"})
        config_digest = _digest(config_response.content)
        manifest_response, image_ref = _manifest_fixture(
            {
                "schemaVersion": 2,
                "config": {"digest": config_digest},
            }
        )
        session = FakeSession(manifest_response, config_response)

        image = verify_public_container_image(image_ref, session=session)

        self.assertEqual(image.platforms, ("linux/amd64",))
        self.assertTrue(manifest_response.closed)
        self.assertTrue(config_response.closed)
        self.assertEqual(
            session.requests[1][0],
            f"https://ghcr.io/v2/example/predict/blobs/{config_digest}",
        )

    def test_rejects_arm64_single_image_config_with_valid_digest(self) -> None:
        config_response = FakeResponse(200, {"os": "linux", "architecture": "arm64"})
        config_digest = _digest(config_response.content)
        manifest_response, image_ref = _manifest_fixture(
            {
                "schemaVersion": 2,
                "config": {"digest": config_digest},
            }
        )
        session = FakeSession(manifest_response, config_response)

        with self.assertRaisesRegex(ContainerRegistryError, "found: linux/arm64"):
            verify_public_container_image(image_ref, session=session)

    def test_rejects_config_bytes_that_do_not_match_descriptor_digest(self) -> None:
        expected_config = FakeResponse(200, {"os": "linux", "architecture": "amd64"})
        config_response = FakeResponse(200, {"os": "linux", "architecture": "arm64"})
        manifest_response, image_ref = _manifest_fixture(
            {
                "schemaVersion": 2,
                "config": {"digest": _digest(expected_config.content)},
            }
        )
        session = FakeSession(manifest_response, config_response)

        with self.assertRaisesRegex(ContainerRegistryError, "different digest"):
            verify_public_container_image(image_ref, session=session)

        self.assertTrue(config_response.closed)

    def test_uses_anonymous_bearer_token_for_public_registry(self) -> None:
        challenge = (
            'Bearer realm="https://auth.example/token",service="registry.example",'
            'scope="repository:example/predict:pull"'
        )
        unauthorized = FakeResponse(401, headers={"WWW-Authenticate": challenge})
        token = FakeResponse(200, {"token": "anonymous-token"})
        manifest, image_ref = _manifest_fixture(_index(("linux", "amd64")))
        session = FakeSession(unauthorized, token, manifest)

        image = verify_public_container_image(image_ref, session=session)

        self.assertEqual(image.platforms, ("linux/amd64",))
        self.assertTrue(unauthorized.closed)
        self.assertTrue(token.closed)
        self.assertTrue(manifest.closed)
        self.assertEqual(session.requests[1][0], "https://auth.example/token")
        self.assertEqual(
            session.requests[1][1]["params"],
            {
                "scope": "repository:example/predict:pull",
                "service": "registry.example",
            },
        )
        self.assertEqual(session.requests[1][1]["auth"], ())
        self.assertEqual(
            session.requests[2][1]["headers"]["Authorization"],
            "Bearer anonymous-token",
        )

    def test_rejects_private_registry_challenge_without_credentials(self) -> None:
        response = FakeResponse(
            401,
            headers={"WWW-Authenticate": 'Basic realm="https://auth.example/token"'},
        )
        session = FakeSession(response)

        with self.assertRaisesRegex(ContainerRegistryError, "non-anonymous authentication"):
            verify_public_container_image(
                _image_reference(f"sha256:{'0' * 64}"),
                session=session,
            )

        self.assertTrue(response.closed)
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(session.requests[0][1]["auth"], ())

    def test_rejects_inaccessible_registry(self) -> None:
        for status_code in (403, 404):
            with self.subTest(status_code=status_code):
                response = FakeResponse(status_code)
                session = FakeSession(response)

                with self.assertRaisesRegex(ContainerRegistryError, "not anonymously pullable"):
                    verify_public_container_image(
                        _image_reference(f"sha256:{'0' * 64}"),
                        session=session,
                    )

                self.assertTrue(response.closed)
                self.assertEqual(len(session.requests), 1)

    def test_rejects_forged_digest_header_when_manifest_bytes_do_not_match(self) -> None:
        expected_digest = _digest(_json_bytes(_index(("linux", "amd64"))))
        image_ref = _image_reference(expected_digest)
        forged_response = FakeResponse(
            200,
            _index(("linux", "arm64")),
            headers={"Docker-Content-Digest": expected_digest},
        )
        session = FakeSession(forged_response)

        with self.assertRaisesRegex(ContainerRegistryError, "invalid digest header"):
            verify_public_container_image(image_ref, session=session)

        self.assertTrue(forged_response.closed)
        self.assertEqual(len(session.requests), 1)

    def test_rejects_manifest_bytes_that_do_not_match_requested_digest_without_header(self) -> None:
        image_ref = _image_reference(_digest(_json_bytes(_index(("linux", "amd64")))))
        response = FakeResponse(200, _index(("linux", "arm64")))
        session = FakeSession(response)

        with self.assertRaisesRegex(ContainerRegistryError, "different digest"):
            verify_public_container_image(image_ref, session=session)

        self.assertTrue(response.closed)
        self.assertEqual(len(session.requests), 1)

    def test_rejects_digest_header_that_does_not_match_manifest_bytes(self) -> None:
        response, image_ref = _manifest_fixture(_index(("linux", "amd64")))
        response.headers["Docker-Content-Digest"] = f"sha256:{'c' * 64}"
        session = FakeSession(response)

        with self.assertRaisesRegex(ContainerRegistryError, "invalid digest header"):
            verify_public_container_image(image_ref, session=session)

        self.assertTrue(response.closed)
        self.assertEqual(len(session.requests), 1)

    def test_normalizes_official_docker_hub_image_name(self) -> None:
        manifest, reference = _manifest_fixture(_index(("linux", "amd64")))
        digest = reference.rsplit("@", 1)[1]
        manifest_content = manifest.content
        for registry in ("docker.io", "index.docker.io", "registry-1.docker.io"):
            with self.subTest(registry=registry):
                image_ref = f"{registry}/ubuntu@{digest}"
                response = FakeResponse(
                    200,
                    headers={"Docker-Content-Digest": digest},
                    content=manifest_content,
                )
                session = FakeSession(response)

                verified = verify_public_container_image(image_ref, session=session)

                self.assertEqual(verified.image, image_ref)
                self.assertEqual(
                    session.requests[0][0],
                    f"https://registry-1.docker.io/v2/library/ubuntu/manifests/{digest}",
                )


if __name__ == "__main__":
    unittest.main()
