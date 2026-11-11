# AnVIL DREAM participant

`anvil-dream-participant` is a standalone Python distribution for submitting
participant-authored WDL workflows. It installs the `anvil-dream-participant` command and the public
`anvil_dream_participant` modules, including the HPA challenge profile.
It does not include, import, or install the DREAM organizer package, and it does not read runtime assets
from the parent repository.

[Online participant documentation](https://www.synapse.org/Synapse:syn74338981/wiki/643841)
is available without GitHub repository access.

## Evaluate your model locally

Use the official [cm4ai-evaluate repository](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-evaluate)
to evaluate your model's predicted images against your own ground-truth images.
It provides the local evaluation tools and usage instructions. See also the
[local evaluation guide](docs/benchmark.md). This participant package handles
model submission and submission status.

## Participant documentation

[Documentation index](docs/README.md) includes the challenge and participant pages
imported from Synapse, with their original page IDs and source links.

- [Quickstart](docs/quickstart.md) covers registration, setup, submission, and status.
- [Submission contract](docs/contract.md) defines the WDL interface and prediction outputs.
- [Container images](docs/container-images.md) covers publishing and digest pinning.
- [Local evaluation guide](docs/benchmark.md) points to the separate evaluation package.
- [ProtiCelli example](docs/proticelli.md) explains the reference model adapter.
- [Participant releases](docs/participant-releases.md) covers maintainer setup and PyPI publishing.

These guides ship with the participant package. Organizer and maintainer documentation
is maintained separately in the organizer repository.

## Develop and test this package

This directory is a standalone project. It contains its own docs, tests, and
[ProtiCelli example](docs/proticelli.md). From this directory:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -B -m unittest discover -s tests -t . -v
python -m build
```

The participant test suite and release build need no organizer checkout or package.
The source distribution includes the tests. The wheel includes the public guides
and runnable example assets and leaves development tests out.

## Install a published release

Install the participant package in Python 3.12 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade anvil-dream-participant
anvil-dream-participant --help
anvil-dream-participant submit --help
anvil-dream-participant submit-anvil --help
anvil-dream-participant submission-status --help
```

The package includes the participant commands, WDL contract checks, private bundle format,
Synapse client integration, Terra identity lookup, GCS staging and status
reporting. It does not need the organizer project to install or run.

## Install from this source checkout

To develop the participant package from a source checkout, run these commands from this
directory:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
anvil-dream-participant --help
```

Install only this project for participant use. The parent repository's organizer package is
not a dependency.

## Use the WDL validator

The public module can validate a participant WDL without importing organizer code:

```python
from anvil_dream_participant.workflows.wdl import parse_participant_workflow

workflow = parse_participant_workflow("participant.wdl")
```

The participant CLI loads the selected profile and uses its workflow validator when you submit.
The default profile is HPA. Set the root `--challenge MODULE:ATTRIBUTE` option to select another
published `ChallengeProfile`. An explicit `--evaluation-id` takes precedence over that profile's
evaluation. Java is not required. For an optional independent check with Cromwell WOMtool, see the
[participant quickstart](docs/quickstart.md).
