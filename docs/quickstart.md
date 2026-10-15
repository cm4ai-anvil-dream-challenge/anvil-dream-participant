This guide walks you through using the [AnVIL DREAM participant CLI tool](https://github.com/cm4ai-anvil-dream-challenge/anvil-dream-participant) to build, package, and submit your model. Once submitted, your pipeline will run against our hidden evaluation dataset.

> **Before you start:** you must be registered for the challenge and a Certified User. See [How to participate](how-to-participate.md) for more details.

Follow the steps below to set up the CLI, prepare your items, and submit.

---

## 1. Install the CLI

> **One-time setup.** If you've already installed the CLI, skip to [Step 2](#2-configure-authentication).

You need Python 3.12 or newer. Install the participant release from PyPI into its own virtual environment. This guide uses `venv`, but `uv`, `conda`, and others also work (the activate command will differ). These commands work from any directory:

```sh
python3.12 --version
python3.12 -m venv "$HOME/dream-participant-venv"
. "$HOME/dream-participant-venv/bin/activate"
PIP_USER=false python -m pip install anvil-dream-participant==0.1.2
anvil-dream-participant --help
anvil-dream-participant submit --help
anvil-dream-participant submit-anvil --help
```

Each time you open a new terminal, reactivate the environment:

```sh
. "$HOME/dream-participant-venv/bin/activate"
```

The participant release includes PNG and optional-mask support, and no repository access is needed for installation. To install from source (like for package development or contribution), see the [participant CLI guide](cli.md).

<details>
<summary>Working in AnVIL?</summary>

In the AnVIL RStudio Bioconductor VM, use **Tools > Terminal > New Terminal**, not the R console. Set `PIP_USER=false` to stop pip from forcing a user-site install.

If `python3.12` is unavailable, use another installed Python 3.12+ executable, or ask the organizers for a supported VM setup.

</details>

<details>
<summary>Extra WDL validation?</summary>

The CLI checks your WDL with miniwdl; Java is not required. For an independent Cromwell parser check, follow the [WOMtool validation instructions](https://github.com/broadinstitute/cromwell/blob/develop/docs/WOMtool.md). Parser validation does not replace running your model on development inputs.

</details>

## 2. Configure authentication

> **One-time setup.** If you've already logged in, skip to [Step 3](#3-prepare-your-model-and-wdl).

The CLI is dependent on authentication to both Synapse and Google Cloud.

### Synapse

1. Generate a [Synapse Personal Access Token (PAT)](https://www.synapse.org/#!PersonalAccessTokens:) with all permissions enabled: **View**, **Download**, and **Modify**.
2. Run the following command, and follow the prompts to enter your Synapse username and PAT:

   ```sh
   synapse config
   synapse login
   ```

   This will create a `.synapseConfig` file in your home directory.

<mark>Note: keep all tokens out of the commands, the WDL file, containers, and submitted files.</mark>

### Google Cloud

How you login will depend on where your files to be submitted are located:

**Files on your computer**

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
2. Configure Application Default Credentials (ADC) with the Google account registered in Terra:

   ```sh
   gcloud auth application-default login
   ```

Local submission requires Terra registration, but does not require organizer workspace or bucket access.

**Files in an AnVIL bucket**

No login required. The CLI will derive your registered email from Terra Sam - do **not** run a separate ADC login in the VM.


## 3. Prepare your model and WDL

Your model can use any framework that implements the interface. ProtiCelli is only an example, not a requirement. For full context, read the [submission contract](contract.md), which defines the full WDL interface and prediction outputs.

### Environment Constraints

One NVIDIA L4 GPU (`g2-standard-8`) with 8 CPUs, 32 GB memory, and 75 GB SSD; with no task retries and one timeout set by the organizers. See the [submission contract](contract.md) for more details.

### Model input files

Your script should read in a JSON array created by `~{write_json(inputs)}` (command placeholder in your WDL file).  Each record will contain a prediction ID, three aligned reference images, and an explicit protein FASTA file. The optional `mask_image` will receive a cell mask when available.

### Expected model output files

For each input record, write one single-channel `512 x 512` uint8 PNG named exactly `<prediction_id>.png`. Put these files in `predictions/` and return them through the WDL output.

Note that reference images use uint8 pixels. Apply the input normalization required by your model inside the container, and encode predictions on the native 0–255 intensity scale. Private target images are available only to organizer grading.

### Weights file

Prepare one weights file in the format your model expects. An archive is acceptable if your code can read or unpack it. The CLI does not convert model checkpoints.

For model-specific starting points, including how the ProtiCelli weights bundle is assembled, see the [ProtiCelli example](proticelli.md) and [ProtiCelliDream](https://github.com/cm4ai-anvil-dream-challenge/ProtiCelliDream).

### Wrap it in a WDL file

Create a `workflow.wdl` that defines your execution pipeline. To ensure compatibility with the evaluation harness, your WDL must adhere to the following interface rules:

* Exactly one task named `Predict`, with no outer `workflow` block
* Exactly two inputs: `Array[PredictionInput] inputs` and `File weights`
* Exactly one output: `Array[File] predictions`
* Only the `docker` attribute declared in the `runtime` block
* Do not declare a task-level manifest file. Keep `write_json(inputs)` in the `command` block so file paths are generated correctly


**Container Implementation**

All model code, dependencies, and execution scripts must be baked directly into your container image. The entrypoint script (`predict.py`) and command-line flags shown in the example below are customizable templates for your container. Adjust script names, flags, and model hyperparameters (such as batch size and random seeds) inside the WDL `command` block or model code as needed.

<details>
<summary>Example WDL template</summary>

This template uses the current interface.

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

> **Important Notes**
> * **Do not include:** credentials, symlinks, or the reserved paths `predictions` and `__dream_runtime_assets.tar`
> * Run supporting scripts with their interpreter (for example `python script.py`), because executable bits are not preserved

</details>


## 4. Build and publish the Docker image

The image must target the `linux/amd64` platform, so build it using Docker Buildx. Arm-only images will not be accepted. See the [container images guide](container-images.md) for more details.

1. Make sure your Dockerfile installs `bash` and `tar` , which are used for execution and optional file extraction.

2. Build and push the image, replacing `ORG`, `IMAGE`, and the build directory:

   ```sh
   docker buildx build --platform linux/amd64 --push \
     --tag ghcr.io/ORG/IMAGE:release ./container
   docker buildx imagetools inspect ghcr.io/ORG/IMAGE:release
   ```

3. Make the image public, so that anyone can pull it without credentials
4. Copy its digest into your WDL file as `ghcr.io/ORG/IMAGE@sha256:...`. Mutable tags (like `:release`) are not accepted.
5. **Test the new image!** The CLI can check your image's public access and `linux/amd64` support, but it cannot prove your model will run. Before submitting, we recommend running your prediction command on development inputs. Check that the weights load and that the uint8 PNG outputs are correctly named.


## 5. Submit

Activate the Python environment (see Step 1), then set the queue ID for the submission command:

```sh
export DREAM_EVALUATION_ID=9620108
```

Run the command that matches where your files are, replacing the example paths with your own. Both commands will generate the required tarfile and manifest for you, as well as create or reuse your private Synapse submission project.


### Files on your computer: `submit`

```sh
anvil-dream-participant submit \
  --wdl ./submission/workflow.wdl \
  --weights ./submission/weights.pt \
  --other-files ./submission/context \
  --anvil-email you@example.org \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

### Files in an AnVIL bucket: `submit-anvil`

Check that the VM sets `WORKSPACE_BUCKET`, then run:

```sh
export SUBMISSION_BUCKET="$WORKSPACE_BUCKET"
anvil-dream-participant submit-anvil \
  --wdl "$SUBMISSION_BUCKET/path/to/workflow.wdl" \
  --weights "$SUBMISSION_BUCKET/path/to/weights.pt" \
  --files "$SUBMISSION_BUCKET/path/to/config.yaml" "$SUBMISSION_BUCKET/path/to/tokens.json" \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

To use a different bucket, set `SUBMISSION_BUCKET` to its `gs://` URL and make sure the VM can access it. Allow temporary disk space for the downloaded files and the submission tarfile.


### Command options

| Command | Option | Description |
|---|---|---|
| Both | `--wdl`, `--weights` | Filepath to your `workflow.wdl` and weights file |
| `submit`| `--other-files` | Supporting files, passed as a folder. Omit if you have none |
| `submit-anvil` | `--files` | Supporting files, passed as space-separated `gs://` bucket/object URLs. Omit if you have none. |
| `submit` | `--anvil-email` | The email of the Google account registered in Terra |
| Both | `--evaluation-id` | The queue ID to submit the model pipeline to |
| Both | `--name` | A label for this submission. **Use a new name each time you submit.** |
| Both | `--team` | Optional. Pass your Synapse team ID to submit as a team |

<details>
<summary>Include a whole folder with `--recursive`</summary>

```sh
anvil-dream-participant submit-anvil \
  --wdl "$SUBMISSION_BUCKET/path/to/workflow.wdl" \
  --weights "$SUBMISSION_BUCKET/path/to/weights.pt" \
  --files "$SUBMISSION_BUCKET/path/to/context/" \
  --recursive \
  --evaluation-id "$DREAM_EVALUATION_ID" \
  --name my-model-v1
```

* `--recursive` requires `--files`. Each `--files` path is treated as a directory prefix (a trailing slash is optional), and paths below it are preserved. For example, `context/vocab/tokens.json` becomes `vocab/tokens.json`
* You can give multiple directories, including ones in different buckets. Duplicate destination paths and file/directory conflicts are rejected before download.

</details>

### Supporting files

Supporting files are optional, and they land in the prediction task's working directory. The example folder below produces `config.yaml` and `vocab/tokens.json`, not `context/config.yaml`:

```text
submission/
  workflow.wdl
  weights.pt
  context/
    config.yaml
    vocab/tokens.json
```


## 6. Check submission status

A successful submission will output `submission_id`, `entity_id`, `status`, fingerprints, and authenticated identities. **Save the `submission_id`** then use it to check its status:

```sh
anvil-dream-participant submission-status --submission-id SUBMISSION_ID
```

A status of `RECEIVED` means Synapse has received your submission into the queue. Prediction and grading may still be pending, so rerun this command later for updates.

> #### **Optional: run the public benchmark**
> The package includes the benchmark organizers use. It runs on an already merged `image-results.jsonl` file, not prediction PNGs, and cannot recreate official metrics on its own. See the [benchmark guide](benchmark.md) for the command and scoring formula.

---

## Troubleshooting

| Symptom | Next step |
|---|---|
| `anvil-dream-participant` is missing, or expected flags are missing | Activate the participant environment and rerun installation and help commands. |
| Synapse authentication fails | Configure a current token with View, Download, and Modify, then log in again. |
| Certification or registration is incomplete | Complete the requirements reported by the CLI. |
| `queue_submit_denied` or `participant_team_queue_access_denied` | Give organizers the queue ID and ask them to check participant access. |
| `submission_team_ineligible` | Check competing-team registration, eligibility, and quota for the selected queue. |
| Terra identity or local ADC error | Complete Terra registration and refresh ADC with the account matching `--anvil-email`. |
| GCS staging fails | Check object paths, VM list/read access, and temporary disk space. |
| Container validation fails | Publish an anonymously pullable `linux/amd64` image and pin its digest. |
| WDL validation fails | Use the exact task interface, one command-level `write_json(inputs)` placeholder, and only `docker` in runtime. |
| Evaluation fails after `RECEIVED` | Read `submission-status`, check the error and output contract, and share the submission ID with organizers. |

Still stuck? Post your question to the [discussion board](https://www.synapse.org/Synapse:syn77349038/discussion/default)!
When asking for help, include your command with secrets removed, submission ID if returned, and the error log. Never share access tokens.
