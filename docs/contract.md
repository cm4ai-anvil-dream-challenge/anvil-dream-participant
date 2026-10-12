# Participant submission contract

The interface is independent of the model framework. Your container owns the
model code, weight format, and prediction command. ProtiCelli is an example
adapter, not a required dependency. Other models must implement the same
three-image FASTA input and uint8 PNG prediction output contract. The current
execution policy uses one NVIDIA L4 on `g2-standard-8` for every participant.

Each submission is one deterministic, uncompressed TAR entity in the Synapse
Evaluation Queue. It contains canonical `manifest.json`, the WDL at
`submission/workflow.wdl`, the weights at `submission/weights`, and, when
requested, regular files below `other/` in lexicographic relative-path order.
The entity is stored in a submitter-owned private Synapse project that the CLI
creates on the first submission and reuses later.

The manifest is the authority for schema version, Synapse user ID, authenticated
AnVIL email and Rawls `userSubjectId`, every file's SHA-256 and byte size, the
digest-pinned public container references, and core/full fingerprints. It never
contains a Dockerfile, Terra workspace, GCS object, or guessed dependency.
The CLI also records the complete TAR's SHA-256 as a Synapse File annotation.
The organizer compares that annotation with the downloaded TAR before accepting
the submission and records the verified digest as `bundle_sha256` in its state Table.

## WDL interface

The submitted WDL must contain exactly one task named `Predict` and no workflow:

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
    manifest="~{write_json(inputs)}"
    python3 prepare.py --inputs-manifest "$manifest" --weights "~{weights}"
    python3 predict.py --weights "~{weights}" --output predictions
  >>>

  output {
    Array[File] predictions = glob("predictions/*")
  }

  runtime {
    docker: "quay.io/example/predict@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

Participants choose the digest-pinned public container and command because
they know how their model runs. The command body may contain multiple statements,
pipelines, loops, and conditionals. The organizer retains the submitted WDL
unchanged for audit. Before execution, it generates a copy that sends the entire
command body to `bash -e` under one organizer-owned timeout. The organizer extracts
runtime assets before starting that timeout. It supplies image lanes and weights,
and retains sample IDs, dataset indices, smoke-test logic, fan-out, validation,
and grading. Model-specific constants
such as batch size and seed belong in the participant command, container, or
weights. They are not part of the organizer-facing interface.

The only runtime attribute participants may set is `docker`. The participant
CLI and organizer intake reject every other runtime attribute in the submitted
WDL. The organizer retains that WDL unchanged for audit, then generates an
executable copy with resources from `[workflow_policy]`. The reference policy
sets 8 CPUs, 32 GB of memory, a 75 GB SSD, one NVIDIA L4, zero preemptible
attempts by default, zero task retries, `noAddress: false`, and
`predefinedMachineType: "g2-standard-8"`. The generated copy uses the Docker
digest from the submitted WDL.

The task interface is fixed. `Predict` must declare exactly the non-optional
`Array[PredictionInput] inputs` and `File weights` inputs, and only the
non-optional `Array[File] predictions` output. The `PredictionInput` struct must
contain exactly `String prediction_id`, `File microtubules_image`,
`File nucleus_image`, `File er_image`, and `File fasta_file`. Datasets with cell
masks also supply `File? mask_image`; declare this optional member to receive
the localized mask path. Older datasets can omit masks. No other additional
members are accepted. Task inputs and outputs remain non-optional.

The `inputs` array is the organizer's lane order. Every record contains an ID,
three aligned images, and an explicit FASTA sequence for that prediction. The
participant cannot see the private grading target or a cell-line label. Put
`write_json(inputs)` directly in the command placeholder. Cromwell then creates
the JSON manifest while it prepares the command, after mapping the `File`
values to task-local paths. A task-level `File inputs_manifest =
write_json(inputs)` declaration can instead retain the original `gs://` URIs.
Submission validation rejects that declaration and requires the command
placeholder.

The manifest is a JSON array:

```json
[
  {
    "prediction_id": "image-0001",
    "microtubules_image": "/cromwell_root/path/microtubules.png",
    "nucleus_image": "/cromwell_root/path/nucleus.png",
    "er_image": "/cromwell_root/path/er.png",
    "fasta_file": "/cromwell_root/path/sequence.fasta",
    "mask_image": "/cromwell_root/path/mask.png"
  }
]
```

Write each prediction as `<prediction_id>.png`. Do not infer sequence data
from filenames; the organizer supplies an explicit FASTA file. The organizer
reconstructs outputs by prediction ID because the WDL `glob()` result order is
not an identity guarantee. The organizer stores each standalone 2D target image
privately and exposes it only to the trusted grading workflow.

Source images use uint8 pixels, each 512 by 512. Prediction files must be
single-channel, 8-bit grayscale PNGs of the same shape, with values in 0–255.
Model-specific input normalization belongs in your container. The organizer
compares predictions and private targets in their native uint8 intensity scale,
using float64 arithmetic for metric calculation. Do not divide output pixels by
255 before writing the PNG. A mask is a separate binary image available to the
participant model; it does not change the scoring region. The ProtiCelli
reference adapter validates masks while retaining its three-channel model input.

The shell body can use multiple commands, pipelines, loops, and conditionals.
The organizer passes the whole body to one `bash -e` process, so one timeout
covers every participant command. Runtime assets are extracted before the timed
process starts. WDL placeholders may use only `write_json(inputs)` and `weights`,
and the command must contain exactly one `write_json(inputs)` placeholder. Put
other model-specific settings in command arguments or inside the container.
Participants cannot set the execution timeout. The organizer sets it on the
generated `Predict` task in `OrganizerEvaluation`.

The organizer stores the submitted WDL in the private Synapse TAR and stages it
in private organizer storage. It embeds the validated `Predict` task in the
organizer workflow and does not publish the submitted WDL or grant it public read
access. The container image must remain publicly pullable by Cromwell.

Supporting files are optional. Local `submit` uses `--other-files DIRECTORY`.
GCS `submit-anvil` uses `--files GS_PATH [GS_PATH ...]` for individual objects,
or adds `--recursive` to include files below each specified directory prefix.
Individual GCS objects use their filenames; recursive copies preserve paths
relative to each prefix. The CLI builds and uploads the submission TAR automatically.
It includes only the explicit WDL, weights, and selected supporting files.
It rejects symlinks, devices, traversal,
duplicate archive paths, malformed TAR members, and files over configured
limits. The organizer validates every header, digest, and fingerprint while
streaming extraction, then stores the original TAR under an immutable,
content-addressed organizer path. It also materializes the WDL and weights as
separate immutable objects in private organizer storage. Rawls receives the
generated `OrganizerEvaluation` workflow and one plan file as its workflow input.

The organizer creates one deterministic runtime-assets TAR that contains only
the optional files. It strips the submission-only `other/` prefix. The generated
`Predict` task extracts the TAR into its working directory before it starts the
participant command. The submitted participant WDL does not declare another
input.

For example, this directory:

```text
context/
  config.yaml
  vocab/tokens.json
```

produces `config.yaml` and `vocab/tokens.json` in the working directory. The
participant command can use `--config config.yaml` without knowing where
Cromwell localized the TAR.

The participant container must provide `tar`. Runtime assets use regular-file
mode `0644`, so invoke submitted scripts through their interpreter. The CLI
rejects the reserved top-level paths `predictions` and
`__dream_runtime_assets.tar`. Extraction also refuses to overwrite an existing
working-directory path.
