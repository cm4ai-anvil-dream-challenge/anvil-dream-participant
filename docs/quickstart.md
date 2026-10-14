# Participant quickstart

Submit your model through the DREAM participant CLI. You provide a task-only WDL,
a public container image, one weights file, and optional supporting files. The CLI
validates and packages these files into a private Synapse submission. Organizers
run prediction and grading on hidden evaluation data.

Use `submit` for files on your computer, or `submit-anvil` for files in Google Cloud
Storage from an AnVIL RStudio VM. Both use the standalone participant package.

## 1. Register for the challenge

1. Create or sign in to your [Synapse account](https://www.synapse.org/).
2. Complete [Synapse Certified User status](https://www.synapse.org/#!Profile:certified).
3. Open the [challenge home page](overview.md)
   and use **Register**. Complete the requirements shown on the site and review the
   [challenge rules](challenge-rules.md).
   A pending registration request must be approved before submission.
4. Sign in to [Terra](https://app.terra.bio/) with the Google account you will use
   for submission. Complete registration and any profile or terms prompts.
5. Ensure your registered participant account has **Can Submit** access to the
   challenge Evaluation Queue, `9620030`.

Use Evaluation Queue **`9620030`** for submissions. The commands below select this
queue with `--evaluation-id`.

To submit individually, omit `--team`. To submit on behalf of your competing team,
register that team with the challenge and add `--team TEAM_ID` to your submission
command. Replace `TEAM_ID` with your team's Synapse ID. The CLI checks that the
team is eligible to submit.

## 2. Prepare your model and WDL

Read the [submission format](contract.md)
and the [current participant contract](contract.md).
Your model can use any framework that implements the interface. ProtiCelli is an
example, not a required dependency.

Create `workflow.wdl` with exactly one task named `Predict` and no `workflow` block.
The only task inputs are `Array[PredictionInput] inputs` and `File weights`, and
its only output is `Array[File] predictions`. Only `docker` is allowed in runtime.

This template uses the current interface. Replace the container reference and
prediction command with your own implementation before submitting:

```wdl
version 1.0

struct PredictionInput {
  String prediction_id
  File microtubules_image
  File nucleus_image
  File er_image
  File fasta_file
  File? mask_image
}

task Predict {
  input {
    Array[PredictionInput] inputs
    File weights
  }

  command <<<
    mkdir -p predictions
    python3 /opt/model/predict.py \
      --inputs-manifest "~{write_json(inputs)}" \
      --weights "~{weights}" \
      --output predictions
  >>>

  output {
    Array[File] predictions = glob("predictions/*.png")
  }

  runtime {
    docker: "ghcr.io/ORG/IMAGE@sha256:REPLACE_WITH_YOUR_IMAGE_DIGEST"
  }
}
```

The script and its flags are examples you implement in your container. Include
model code and dependencies in the image. Model settings such as batch size and
seed belong in the command or model code. Organizers set resources and timeout.

Your script reads the JSON array created by `~{write_json(inputs)}`. Each record
contains a prediction ID, three aligned reference images, and an explicit protein
FASTA file. The optional `mask_image` receives a cell mask when available. Keep
`write_json(inputs)` directly in the command placeholder so the JSON contains
localized paths. Do not declare a task-level manifest file or infer sequence from
a filename.

Write one single-channel `512 x 512` uint8 PNG per input record, named
exactly `<prediction_id>.png`. Put these files in `predictions/` and return them
through the WDL output. Reference images use uint8 pixels. Apply the input
normalization required by your model inside the container, and encode predictions
on the native 0–255 intensity scale. Private target images are available only to
organizer grading.

Prepare one weights file in the format your model expects. An archive is acceptable
if your code can read or unpack it. The CLI does not convert model checkpoints.
See the [ProtiCelli example](proticelli.md)
and [ProtiCelliDream](https://github.com/cm4ai-anvil-dream-challenge/ProtiCelliDream)
for model-specific starting points.

## 3. Build and publish the container

Use a machine with Docker Buildx and access to your registry. Replace `ORG`, `IMAGE`,
and the container build directory:

```sh
docker buildx build --platform linux/amd64 --push \
  --tag ghcr.io/ORG/IMAGE:release ./container
docker buildx imagetools inspect ghcr.io/ORG/IMAGE:release
```

Make the image publicly pullable without credentials. Copy its published digest
into the WDL as `ghcr.io/ORG/IMAGE@sha256:...`. Mutable tags and ARM-only images are
not accepted. Include `bash` and `tar` for execution and optional file extraction.

Run your prediction command against development inputs before submitting. Verify
weights loading and correctly named uint8 PNG outputs. The CLI checks public image
access and `linux/amd64` support, but cannot prove your model will execute.
See the [container guide](container-images.md).

## 4. Install the participant CLI

Use Python 3.12 or newer. Install the participant release from PyPI in its own
virtual environment. These commands work from any directory:

```sh
python3.12 --version
python3.12 -m venv "$HOME/dream-participant-venv"
. "$HOME/dream-participant-venv/bin/activate"
PIP_USER=false python -m pip install anvil-dream-participant==0.2.0
anvil-dream-participant --help
anvil-dream-participant submit --help
anvil-dream-participant submit-anvil --help
```

The release includes PNG and optional-mask support. Repository access is not
required for installation. See the [participant CLI guide](cli.md) for
installing a source checkout when developing the package.

In AnVIL, use the RStudio Bioconductor VM's **Tools > Terminal > New Terminal**,
not the R console. `PIP_USER=false` prevents pip from forcing a user-site install.
If `python3.12` is unavailable, use another installed Python 3.12 or newer executable
or ask organizers for a supported VM setup.

Activate this environment again in each new terminal:

```sh
. "$HOME/dream-participant-venv/bin/activate"
```

The CLI checks WDL with miniwdl; Java is not required. For an optional independent
Cromwell parser check, follow the
[WOMtool validation instructions](https://github.com/broadinstitute/cromwell/blob/develop/docs/WOMtool.md).
Parser validation does not replace running your model on development inputs.

## 5. Configure authentication

In Synapse, open **Account Settings > Personal Access Tokens** and create a token
with **View**, **Download**, and **Modify**. Configure the client in your environment:

```sh
synapse config
synapse login
```

Enter the token through the client's prompts. Keep tokens out of commands, WDL,
containers, and submitted files. Synapse and Google authentication are separate.

For local `submit`, install the Google Cloud CLI and configure Application Default
Credentials with the Google account registered in Terra:

```sh
gcloud auth application-default login
```

Use that account's email for `--anvil-email`. Local submission needs Terra
registration, but does not require organizer workspace or bucket access.

For `submit-anvil`, use the RStudio VM's existing Google credentials. Do not run a
separate ADC login in that VM. The CLI derives your registered email from Terra Sam,
so this route has no `--anvil-email` flag. The VM identity needs list and read access
to the bucket holding your files.

## 6. Submit through one route

Set the challenge Evaluation Queue ID:

```sh
export DREAM_EVALUATION_ID=9620030
```

### Local files

An example directory is:

```text
submission/
  workflow.wdl
  weights.pt
  context/
    config.yaml
    vocab/tokens.json
```

Replace paths and email with your own:

```sh
anvil-dream-participant submit \
  --wdl ./submission/workflow.wdl \
  --weights ./submission/weights.pt \
  --other-files ./submission/context \
  --anvil-email you@example.org \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

Omit `--other-files` if no supporting files are needed. Add `--team TEAM_ID` for a
team submission.

### Files in an AnVIL bucket

Check that the VM sets `WORKSPACE_BUCKET`. Replace the example object paths:

```sh
export SUBMISSION_BUCKET="$WORKSPACE_BUCKET"
anvil-dream-participant submit-anvil \
  --wdl "$SUBMISSION_BUCKET/path/to/workflow.wdl" \
  --weights "$SUBMISSION_BUCKET/path/to/weights.pt" \
  --files "$SUBMISSION_BUCKET/path/to/config.yaml" "$SUBMISSION_BUCKET/path/to/tokens.json" \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

Use `--files` followed by space-separated `gs://bucket/object` URLs for supporting
files. Each file becomes available by its filename, such as `config.yaml`, in the
prediction task's working directory. Omit `--files` when no supporting files are needed.

To include an entire GCS directory, add `--recursive`:

```sh
anvil-dream-participant submit-anvil \
  --wdl "$SUBMISSION_BUCKET/path/to/workflow.wdl" \
  --weights "$SUBMISSION_BUCKET/path/to/weights.pt" \
  --files "$SUBMISSION_BUCKET/path/to/context/" --recursive \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

Recursive mode treats every `--files` path as a directory prefix. A trailing slash
is optional. Paths below each prefix are preserved: `context/vocab/tokens.json`
becomes `vocab/tokens.json`. You can provide multiple directories, including ones
in different buckets. Duplicate destination paths and file/directory conflicts
are rejected before download. `--recursive` requires `--files`.

For another bucket, set `SUBMISSION_BUCKET` to its `gs://` URL and verify VM access.
Allow temporary disk space for both the downloaded files and the submission TAR.

The CLI creates or reuses your private Synapse submission project and builds the
TAR and manifest automatically. Do not submit a Dockerfile or build the manifest
by hand. Supporting files become relative to the task's working directory. The
example supplies `config.yaml` and `vocab/tokens.json`, not `context/config.yaml`.
Do not include credentials, symlinks, or reserved paths `predictions` and
`__dream_runtime_assets.tar`. Invoke supporting scripts with their interpreter;
executable bits are not preserved.

## 7. Save the submission ID and check status

Success prints JSON containing `submission_id`, `entity_id`, `status`, fingerprints,
and authenticated identities. Save the returned `submission_id`. Initial status
`RECEIVED` means Synapse accepted the submission; prediction and grading may still
be pending.

`ACCEPTED` means organizer validation passed. `SCORED` means evaluation and
grading completed successfully.

```sh
anvil-dream-participant submission-status --submission-id SUBMISSION_ID
```

Replace `SUBMISSION_ID` with the returned ID. The command reports `status`,
`dream_state`, `comparison_status`, `comparison_shard_count`, and `error` when
available. Organizer annotations may initially be null. Rerun later for updates.
Organizers operate evaluation and grading.

For a revised model, update your files and rerun with a distinct `--name`. Identical
bundles may reuse the uploaded TAR while creating another queue submission. Follow
challenge rules and queue quotas before resubmitting.

## Optional: run the public benchmark

The participant package includes the benchmark used by organizers. It reads an
already merged `image-results.jsonl` file of per-image metrics, not prediction PNGs.
If you have a compatible results file, run:

```sh
python -m anvil_dream_participant.benchmarking.evaluation \
  --image-results image-results.jsonl \
  --comparison-report comparison-report.json \
  --aggregate evaluation-aggregate.json \
  --output score-result.json
```

See the [benchmark guide](benchmark.md)
for the exact JSONL schema and scoring formula. Official targets are private.
Prediction PNGs alone cannot recreate official metrics. Running this benchmark
does not require the organizer package.

## Troubleshooting

| Symptom | Next step |
|---|---|
| `anvil-dream-participant` is missing, or expected flags are missing | Activate the participant environment and rerun installation and help commands. |
| Synapse authentication fails | Configure a current token with View, Download, and Modify, then log in again. |
| Certification or registration is incomplete | Complete the requirements reported by the CLI. Pending requests need organizer approval. |
| `queue_submit_denied` or `participant_team_queue_access_denied` | Give organizers the queue ID and ask them to check participant Can Submit access. |
| `submission_team_ineligible` | Check competing-team registration, eligibility, and quota for the selected queue. |
| Terra identity or local ADC error | Complete Terra registration and refresh ADC with the account matching `--anvil-email`. |
| GCS staging fails | Check object paths, VM list/read access, and temporary disk space. |
| Container validation fails | Publish an anonymously pullable `linux/amd64` image and pin its digest. |
| WDL validation fails | Use the exact task interface, one command-level `write_json(inputs)` placeholder, and only `docker` in runtime. |
| Evaluation fails after `RECEIVED` | Read `submission-status`, check the error and output contract, and share the submission ID with organizers. |

When asking for help, include your command with secrets removed, queue ID, submission
ID if returned, and error text. Never share access tokens.
