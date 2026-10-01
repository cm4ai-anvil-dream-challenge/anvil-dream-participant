# CM4AI AnVIL DREAM participant

`cm4ai-anvil-dream-participant` is a standalone Python distribution for submitting
participant-authored WDL workflows. It installs the `dream` command and the public
`dream_participant` modules. It does not include, import, or install the DREAM organizer
package, and it does not read runtime assets from the parent repository.

## Install a published release

After the package is published to PyPI, install it in Python 3.12 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install cm4ai-anvil-dream-participant
dream participant --help
dream participant submit --help
dream participant submit-anvil --help
dream participant submission-status --help
```

The package includes the participant commands, WDL contract checks, private bundle format,
Synapse client integration, Terra identity lookup, GCS staging, image validation, and status
reporting. It does not need the organizer project to install or run.

## Install from this source checkout

To work on the participant package before a PyPI release, run these commands from this
directory:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
dream participant --help
```

Install only this project for participant use. The parent repository's organizer package is
not a dependency.

## Use the WDL validator

The public module can validate a participant WDL without importing organizer code:

```python
from dream_participant.workflows.wdl import parse_participant_workflow

workflow = parse_participant_workflow("participant.wdl")
```
