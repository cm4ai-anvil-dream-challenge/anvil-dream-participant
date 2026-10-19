[How to participate](docs/how-to-participate.md) · [Quickstart](docs/quickstart.md) · [All documentation](docs/README.md)

[![CM4AI AnVIL Cell Map Prediction DREAM Challenge](https://raw.githubusercontent.com/cm4ai-anvil-dream-challenge/anvil-dream-participant/main/docs/assets/challenge-banner.png)](https://www.synapse.org/Synapse:syn74338981/wiki/639954)

Predict protein localization from reference microscopy images and a protein sequence.
Use the participant CLI to validate your workflow, package your model, and submit it
to the CM4AI/AnVIL Cell Map Prediction DREAM Challenge.

## Install the CLI

Use Python 3.12 or newer:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade anvil-dream-participant
anvil-dream-participant --help
```

Follow the [quickstart](docs/quickstart.md) to configure authentication, prepare your
WDL, container image, and weights, and submit your model. The CLI supports files
on your computer and in an AnVIL workspace bucket.

## Prepare your submission

| Guide | What it covers |
| --- | --- |
| [How to participate](docs/how-to-participate.md) | Registration, teams, submissions, and results. |
| [Quickstart](docs/quickstart.md) | Authentication, preparing your model, and making a submission. |
| [Submission format](docs/contract.md) | The WDL interface, inputs, and prediction outputs. |
| [Container images](docs/container-images.md) | Building, publishing, and pinning your model image. |
| [Data](docs/data.md) | Available datasets, image channels, and file formats. |
| [Evaluation](docs/evaluation.md) | What the challenge measures and how models are evaluated. |
| [ProtiCelli example](docs/proticelli.md) | A reference model adapter for the submission interface. |
| [Command line tool](docs/cli.md) | CLI commands, WDL validation, and the local benchmark. |

The [documentation index](docs/README.md) also includes challenge rules, background,
and the timeline. You can read the [challenge site on Synapse](https://www.synapse.org/Synapse:syn74338981/wiki/639954)
without GitHub repository access.

## Check your submission

Save the submission ID returned by the CLI, then check its status:

```sh
anvil-dream-participant submission-status --submission-id SUBMISSION_ID
```

See the [quickstart troubleshooting guide](docs/quickstart.md#troubleshooting) for
help with authentication, containers, workflows, or submissions.

## Run the public benchmark

If you have an `image-results.jsonl` file, run the included benchmark:

```sh
python -m anvil_dream_participant.benchmarking.evaluation \
  --image-results image-results.jsonl \
  --comparison-report comparison-report.json \
  --aggregate evaluation-aggregate.json \
  --output score-result.json
```

The [benchmark guide](docs/benchmark.md) describes the input format and score outputs.
This command reads per-image metrics; it does not generate predictions or require
access to the hidden evaluation dataset.
