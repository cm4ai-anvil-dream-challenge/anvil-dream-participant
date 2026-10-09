# Participant container images

Build and publish the container before submitting. The WDL is the only
container reference submitted; the Dockerfile is local build input and is not
uploaded or executed by the organizer.

Use an immutable public OCI reference in every task's runtime block:

```wdl
runtime {
  docker: "ghcr.io/example/predict@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

The submit command checks that each digest-pinned image can be pulled
anonymously and supports `linux/amd64` before uploading the TAR. Mutable
tags, private registries, missing manifests, and ARM-only images are rejected.
Install `tar` in the image. The organizer uses it to extract optional runtime
files into the task working directory before the participant command starts.

The bundle's `manifest.json` records each image reference from the submitted
WDL. The organizer checks that the WDL still names those references, then
fetches each registry manifest by digest and hashes the returned bytes before
launch. It also checks the registry's `Docker-Content-Digest` header when the
registry sends one. For a single-image manifest, the organizer hashes the
referenced config bytes before reading the operating system and architecture.
The organizer does not download every image layer during this check. Terra
pulls the same digest-pinned image when it runs the workflow.

For example, build and publish locally, then put the resulting digest in the
WDL:

```bash
docker buildx build --platform linux/amd64 --push \
  --tag ghcr.io/ORG/IMAGE:release ./container
docker buildx imagetools inspect ghcr.io/ORG/IMAGE:release
```

Do not add a Dockerfile, registry credential, or GCS URL to the submission.
