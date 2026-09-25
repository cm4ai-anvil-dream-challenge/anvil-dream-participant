# CM4AI AnVIL DREAM participant

`cm4ai-anvil-dream-participant` provides public modules for participant WDL validation,
submission bundles, GCS staging, status records, container image checks, and progress logging.

Install the package from this directory:

```sh
python -m pip install .
```

For example, validate a participant WDL with:

```python
from dream_participant.workflows.wdl import parse_participant_workflow

workflow = parse_participant_workflow("participant.wdl")
```
