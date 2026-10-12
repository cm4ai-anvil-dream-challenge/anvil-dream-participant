# AnVIL DREAM participant

`anvil-dream-participant` is a standalone Python distribution for submitting
participant-authored WDL workflows. It installs the `anvil-dream-participant` command and the public
`anvil_dream_participant` modules, including the runnable benchmark implementation. It does not
include, import, or install the DREAM organizer package, and it does not read runtime assets
from the parent repository.

[Online participant documentation](https://www.synapse.org/Synapse:syn74338981/wiki/643841)
is available without GitHub repository access.

## Participant documentation

- [Quickstart](docs/quickstart.md) covers registration, setup, submission, and status.
- [Submission contract](docs/contract.md) defines the WDL interface and prediction outputs.
- [Container images](docs/container-images.md) covers publishing and digest pinning.
- [Benchmark guide](benchmarking/README.md) defines metrics and local benchmarking.
- [ProtiCelli example](docs/proticelli.md) explains the reference model adapter.
- [Participant releases](docs/participant-releases.md) covers maintainer setup and PyPI publishing.

These guides ship with the participant package. Organizer and maintainer documentation
is maintained separately in the [organizer repository](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-anvil-dream-tools/tree/main/organizer/docs).

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
python -m pip install anvil-dream-participant
anvil-dream-participant --help
anvil-dream-participant submit --help
anvil-dream-participant submit-anvil --help
anvil-dream-participant submission-status --help
```

The package includes the participant commands, WDL contract checks, private bundle format,
Synapse client integration, Terra identity lookup, GCS staging, image validation, and status
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

The participant CLI uses this miniwdl-based contract validation when you submit. Java is not
required. For an optional independent check with Cromwell WOMtool, see the
[participant quickstart](docs/quickstart.md#4-install-the-participant-cli).

## Run the benchmark

The standalone participant package includes the exact benchmark implementation used by the
organizer. Its Python entrypoint is [`anvil_dream_participant.benchmarking.evaluation`](benchmarking/evaluation.py);
the corresponding organizer task is `BenchmarkEvaluation` in
`organizer/workflows/templates/benchmark.wdl` in the [source repository](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-anvil-dream-tools).
The [benchmark guide](benchmarking/README.md) defines the complete input contract, command, score
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

Raw grading and shard gathering remain trusted organizer tasks. Current target TIFFs are private,
so participants can run the benchmark only when they have the already merged JSONL input; WDL
source visibility alone does not make raw grading runnable. A future self-run route needs a
separately published participant-runnable raw-grading WDL and approved target inputs, followed by
the benchmark task. The guide documents the input rows and exact standalone command.
