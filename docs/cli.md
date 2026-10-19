# AnVIL DREAM participant

Use `anvil-dream-participant` to validate and package your model workflow, submit
it to the challenge, and check its status. Install the CLI from PyPI with Python
3.12 or newer.

## Participant documentation

- [Quickstart](quickstart.md) covers registration, setup, submission, and status.
- [Submission contract](contract.md) defines the WDL interface and prediction outputs.
- [Container images](container-images.md) covers publishing and digest pinning.
- [Benchmark guide](benchmark.md) defines metrics and local benchmarking.
- [ProtiCelli example](proticelli.md) explains the reference model adapter.

## Install a published release

Install the participant package in Python 3.12 or newer:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install anvil-dream-participant
anvil-dream-participant --help
anvil-dream-participant submit --help
anvil-dream-participant submit-anvil --help
anvil-dream-participant submission-status --help
```

## Use the WDL validator

You can validate a participant WDL before submitting:

```python
from anvil_dream_participant.workflows.wdl import parse_participant_workflow

workflow = parse_participant_workflow("participant.wdl")
```

The participant CLI uses this miniwdl-based contract validation when you submit. Java is not
required. For an optional independent check with Cromwell WOMtool, see the
[participant quickstart](quickstart.md#1-install-the-cli).

## Run the benchmark

The package includes the benchmark used to combine per-image metrics into a score.
Its Python entrypoint is [`anvil_dream_participant.benchmarking.evaluation`](../benchmarking/evaluation.py).
The [benchmark guide](benchmark.md) defines the complete input contract, command, score
formula, outputs, and current limits.

| Output | Contents |
|---|---|
| `comparison-report.json` | Evaluation-wide MAE, RMSE, cosine similarity, maximum error, and compared image/pixel counts. |
| `evaluation-aggregate.json` | Image and pixel counts plus one `metrics` object with additive sums and derived evaluation-wide metrics. |
| `score-result.json` | Numeric 0–1 score and its MAE, RMSE, maximum-error, and cosine components. |

Run it locally with an already merged `image-results.jsonl` file:

```sh
python -m pip install anvil-dream-participant
python -m anvil_dream_participant.benchmarking.evaluation \
  --image-results image-results.jsonl \
  --comparison-report comparison-report.json \
  --aggregate evaluation-aggregate.json \
  --output score-result.json
```

The benchmark reads an already merged metrics file. Prediction images alone are
not sufficient to run it. See the [benchmark guide](benchmark.md) for the JSONL
input format and scoring formula.
