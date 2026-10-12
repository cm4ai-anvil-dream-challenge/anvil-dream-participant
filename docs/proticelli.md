# ProtiCelli participant example

This example receives one lane of independently pinned, pre-aligned images and
protein FASTA files. Its `PredictionInput` record contains:

- `prediction_id`: stable identity used to name the prediction
- `microtubules_image`, `nucleus_image`, and `er_image`: separate 512 x 512 uint8
  TIFFs or 8-bit grayscale PNGs in ProtiCelli's channel order
- `fasta_file`: an explicit FASTA containing one or more protein sequences
- `mask_image`: an optional localized cell mask, validated by the adapter

The public task receives no grading target, target-protein label, or cell-line
label. The organizer keeps the 2D target image in private storage and supplies it
only to the trusted grading workflow. The images are assumed to be spatially
aligned already. The adapter accepts uint8 image pixels in the 0–255 range and applies
the model normalization before inference.

The command placeholder `~{write_json(inputs)}` creates the JSON array of
localized file paths. Keep it in the command. The prediction script reads each
FASTA once, resolves each unique sequence once, and chooses the strongest
supported BLAST match across all sequences in each file. Higher bitscore wins,
then lower e-value; ties keep FASTA order. It stacks image planes in
microtubules/nucleus/ER order, and writes `<prediction_id>.png`. It runs local
BLAST against the reference database in the weights ZIP, walks hits by score
until it finds a gene in the model vocabulary, and fails the task explicitly if
no hit is supported. The selected accession, hit rank, sequence digest, and
score are stored in each output PNG's `provenance` text field for provenance.

## Image normalization and output encoding

The adapter explicitly applies upstream `ImageNormalizer(bit_depth=8)` to each
cell. It places the reference channels in the upstream four-channel layout,
leaves the unavailable protein channel at zero, and then extracts the normalized
microtubule, nucleus, and ER channels for inference. It retains the clipped
nucleus maximum as the per-cell intensity scale, with a reference-channel
fallback when the nucleus scale is zero.

To write a prediction, it multiplies the model output by that retained scale,
clips to 0–255, rounds to the nearest integer, and casts to uint8. This matches
the adapter's normalization scale; it does not use the older fixed
`prediction / 1.3 * 255` conversion. Model inference remains floating point.
PNG provenance records the protein match, sequence digest, and nucleus scale.

The expected outer weights ZIP layout is:

```text
checkpoint/unet/
checkpoint/unet_ema/
vae/
reference/human_blast_db.zip
reference/human_gene_map.zip
```

## Assemble the weights bundle

The weights ZIP used in the recorded TEST run is 3,878,681,126 bytes with SHA-256
`ad05433c000837be1d2b83d9ddd4e29d813d49ea0b42cb463dc79c27b87762cd`. The organizer
retrieves it with the run handoff manifest, verifies
the outer ZIP and all six checkpoint files, and transfers it to the participant through the
approved private handoff. A participant can then submit the exact ZIP directly; they do not
need the organizer's Cloud Storage access, your machine's Downloads directory, or the source
UniProt archive. The original checkpoint download release was not recorded, so the manifest
identifies the accepted model by six per-file hashes rather than claiming a release ID.

To build a new bundle, supply a model directory with `checkpoint/unet`, `checkpoint/unet_ema`,
and `vae`; each contains `config.json` and `diffusion_pytorch_model.safetensors`. One way to
obtain those exact six files is to extract them from the verified handoff ZIP. Obtain the
recorded `uniprot_sprot_human.dat.gz` from the organizer or an immutable archive. Its required
SHA-256 is `1af63498762d426240e30c9eb40e0124cd6af1b9eb35f440d1b4e89a097999bc`.
The builder refuses a mismatched file or a mutable `current_release` URL. It derives the
reviewed human records from the same source, then keeps only accessions whose `GN Name` resolves
through the installed `proticelli.data/antibody_map.pkl`. Matching follows the prediction code's
case-insensitive exact and comma-token vocabulary rules. Each new reference ZIP records the total
reviewed human count, selected count, and vocabulary resource SHA-256 before building the BLAST
2.17.0 database. This describes newly rebuilt bundles; the recorded handoff manifest remains the
historical artifact. Docker must be running. From the participant project directory,
choose your own paths:

```bash
mkdir -p "$PWD/model-assets"
unzip -q "$PWD/weights-handoff.zip" \
  'checkpoint/unet/*' 'checkpoint/unet_ema/*' 'vae/*' -d "$PWD/model-assets"
bash examples/proticelli/scripts/build_weights.sh \
  "$PWD/model-assets" \
  "$PWD/rebuilt-weights.zip" \
  "$PWD/uniprot_sprot_human.dat.gz"
```

The model directory and UniProt file are read-only inputs. The builder packages only the
six model files and two reference ZIPs, excluding training state, and refuses to overwrite an
existing output. ZIP headers and generated reference metadata are normalized so identical
inputs give identical rebuilt bytes across source-file timestamps. A newly rebuilt ZIP need
not have the same hash as the historical ZIP, whose archive metadata was not normalized.
Allow space for the bundle and temporary references.

The script defaults to the image digest pinned in this example. Set
`PROTICELLI_IMAGE` to use another compatible image. The output includes reference
provenance, verifies ZIP integrity, and prints its SHA-256 digest. Submit the verified handoff
or rebuilt file with `--weights PATH_TO_ZIP`.

## Runtime assets

The runner securely unpacks these ZIPs into task-local storage. It also accepts
a directory or TAR for development, provided the model files and BLAST/gene-map
files are present. BLAST+ 2.17.0 is installed in the example container. The
gene map is a TSV with `accession`, `hgnc_symbol`, and optionally
`ensembl_gene_ids` columns.

The checked-in WDL pins the published `quay.io/anvil_dream/blast-proticelli-example`
image at digest `sha256:9e03517cc45ac0ad72386620d34bf24a54a12a297f65ab79a2c2ac9a9debfa18`.
The Dockerfile pins ProtiCelli source commit `f894e78d2fbc0530c3637d5f7c457ead0738a5d0`,
BLAST+ 2.17.0 with a tarball checksum, and a Python 3.12 base image digest. Its
[runtime constraints](../examples/proticelli/model-runtime.constraints.txt) were read from the published image;
they pin PyTorch, torchvision, and the model's Python dependency stack for rebuilds. The
recorded run retains its original image digest. The current image adds PNG,
mask, and multi-sequence FASTA support. When building
a replacement image, use `linux/amd64`, run the prediction smoke check, publish it, and
replace `runtime.docker` with the new public registry manifest digest. Keep the WDL task
inputs, outputs, and command interface compatible with the participant contract. Verify with
`python3 /opt/proticelli/predict.py --help` and check the WDL contract before submission.

On a build VM, use the example directory as the Docker build context, then smoke-test the
local image before publishing a replacement digest:

```bash
docker build --platform linux/amd64 -t proticelli-rebuild-check \
  examples/proticelli
docker run --rm --platform linux/amd64 --entrypoint python3 \
  proticelli-rebuild-check /opt/proticelli/predict.py --help
```

The example uses batch size 4, 50 inference steps, and seed 0. These are
model-specific settings, not organizer inputs. The organizer supplies the Terra resource and
timeout policy; the submission still needs a public, immutable `runtime.docker` image digest.
