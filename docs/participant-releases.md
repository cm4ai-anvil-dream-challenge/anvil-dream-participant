# Publish the participant package

The public distribution is `anvil-dream-participant`. It installs the
`anvil-dream-participant` command and `anvil_dream_participant` modules. Only
participant artifacts are uploaded.

## Configure PyPI once

Sign in to PyPI as `matthew_peterkort` and add or update the publisher in
[account publishing](https://pypi.org/manage/account/publishing/):

| Field | Value |
|---|---|
| PyPI project name | `anvil-dream-participant` |
| Owner | `cm4ai-anvil-dream-challenge` |
| Repository name | `anvil-dream-participant` |
| Workflow filename | `publish-participant.yml` |
| Environment name | `pypi-participant` |

The publisher must name the standalone `anvil-dream-participant` repository.
A publisher configured for `cm4ai-anvil-dream-tools` does not authorize this
repository. Configure the new repository in PyPI before the first tagged
release. The repository uses a GitHub environment named `pypi-participant`,
limited to `participant-v*` tags. The publish job obtains a short-lived token
through Trusted Publishing. No PyPI API token is stored in the repository.
See [PyPI's pending publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Cut a release

Commit all package changes and release tooling first. From this repository's
root, with Python 3.12 or newer and Git configured:

```sh
make release-participant-dry-run VERSION=0.2.0
make release-participant VERSION=0.2.0
```

For a later release, choose a new version, for example:

```sh
make release-participant VERSION=0.2.1
```

If the version changes, the command updates `pyproject.toml` and commits that
file. It creates an annotated `participant-vVERSION` tag and pushes the tag to
`origin`. The helper requires a clean working tree. Set `PYTHON=python3` or
`RELEASE_REMOTE=REMOTE` if needed.

The tag starts
[the publishing workflow](../.github/workflows/publish-participant.yml). It
tests the package, builds the wheel and source archive, checks their public
contents and license, and exercises the installed wheel before uploading the
validated artifacts to PyPI. Pull requests use the separate package CI workflow;
manually running the publishing workflow validates the package without
uploading it. Only a release tag push publishes.

## Verify or recover a release

Watch the run in GitHub Actions:

```sh
gh run list --workflow publish-participant.yml
gh run watch RUN_ID --exit-status
```

A failed upload keeps its validated build artifacts in the workflow run. Fix
PyPI publisher setup and rerun the failed job with
`gh run rerun RUN_ID --failed`. If the release source needs a correction, use a
new version and release tag. PyPI versions cannot be overwritten.

After a successful release:

```sh
python3.12 -m venv /tmp/anvil-dream-release-check
/tmp/anvil-dream-release-check/bin/python -m pip install anvil-dream-participant==0.2.0
/tmp/anvil-dream-release-check/bin/anvil-dream-participant --help
```

Update the organizer's participant dependency after the new version is
available on PyPI.
