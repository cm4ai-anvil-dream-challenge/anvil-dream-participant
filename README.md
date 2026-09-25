# CM4AI AnVIL DREAM participant

`cm4ai-anvil-dream-participant` provides the participant-only `dream` command and public
modules for WDL validation, Synapse readiness and private submission, Terra identity, bundles,
GCS staging, status records, container image checks, and progress logging. It does not include
organizer commands or code.

Install the package from this directory:

```sh
python -m pip install .
dream --help
dream participant submit --help
dream participant submit-anvil --help
dream participant submission-status --help
```

For example, validate a participant WDL with:

```python
from dream_participant.workflows.wdl import parse_participant_workflow

workflow = parse_participant_workflow("participant.wdl")
```
