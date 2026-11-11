# AnVIL DREAM participant

`anvil-dream-participant` is a standalone Python distribution for submitting
participant-authored WDL workflows. It installs the `anvil-dream-participant` command and the public
`anvil_dream_participant` modules, including the HPA challenge profile.
It does not include, import, or install the DREAM organizer package, and it does not read runtime assets
from the parent repository.

[Online participant documentation](https://www.synapse.org/Synapse:syn77349038/wiki/643948)
is available without GitHub repository access.

## Participant documentation

- [Quickstart](quickstart.md) covers registration, setup, submission, and status.
- [Submission contract](contract.md) defines the WDL interface and prediction outputs.
- [Container images](container-images.md) covers publishing and digest pinning.
- [Local evaluation guide](benchmark.md) points to the separate evaluation package.
- [ProtiCelli example](proticelli.md) explains the reference model adapter.

These guides ship with the participant package. Organizer and maintainer documentation
is maintained separately under `organizer/docs/` in the source repository.

## Develop and test this package

This directory is a standalone project. It contains its own docs, tests, and
[ProtiCelli example](proticelli.md). From this directory:

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

The `submit`, `submit-anvil`, and `submission-status` commands use the public HPA
profile by default. Set the root `--challenge MODULE:ATTRIBUTE` option before a command to load a
different `ChallengeProfile`. The selected profile supplies the evaluation, queue, workflow
validator. `--evaluation-id` overrides the profile's evaluation for
submission commands.

Install the participant package in Python 3.12 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install anvil-dream-participant
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
[participant quickstart](quickstart.md#1-install-the-cli).

## Evaluate HPA predictions

Local evaluation is provided by the separate
[cm4ai-evaluate package](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-evaluate).
It compares predicted protein images with your own ground-truth images and reports
raw diagnostic metrics. See the [local evaluation guide](benchmark.md).

The participant package submits models and checks submission status. Official challenge
evaluation runs on held-out data in the organizer environment.

## Supporting files

Use `submit --other-files DIRECTORY` for local supporting files or
`submit-anvil --files gs://bucket/object ...` for GCS sources. These assets appear
in the prediction task working directory. See [supporting files versus container contents](contract.md#supporting-files-versus-container-contents)
for path mapping, bundled scripts, and examples using image code with external configuration.
