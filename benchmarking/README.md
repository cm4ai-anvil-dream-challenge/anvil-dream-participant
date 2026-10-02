# Public evaluation benchmark

This package benchmarks an already merged `image-results.jsonl` file. It does not run a model,
compare TIFF pixels, gather shard files, access GCS, or validate organizer receipts. Those are
trusted organizer tasks. The public benchmark reads one per-image record at a time, combines its
primitive statistics, computes evaluation-wide metrics and the score, and writes three artifacts.

The implementation is [`evaluation.py`](evaluation.py). The organizer calls the same module from
`organizer/workflows/templates/benchmark.wdl` in the [source repository](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-anvil-dream-tools); the WDL itself stays in the organizer package.

## Input file

Use UTF-8 JSON Lines: one JSON object for each image. This is the
merged file produced by the organizer's trusted gather task from per-image grader records. Keep
the rows in dataset order and retain every field. Do not average or otherwise combine their
statistics before running the benchmark.

Each object must have exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `sample_id` | string | Prediction ID for this image. |
| `metrics` | object | Six primitive sums plus per-image MAE, RMSE, and cosine similarity computed by the grader. |

`metrics` has exactly `absolute_error_sum`, `squared_error_sum`, `dot_product_sum`,
`prediction_square_sum`, `target_square_sum`, `maximum_absolute_error`,
`mean_absolute_error`, `root_mean_squared_error`, and `cosine_similarity`; cosine is `null` when
either image has zero norm. Numeric values must be finite. These values come from comparing one
prediction TIFF with its target in float64. The benchmark combines the primitive sums across
images, then derives evaluation-wide MAE, RMSE, cosine similarity, and maximum absolute error.
Every image has 262,144 pixels under the fixed `512 × 512` challenge contract, so the row does
not repeat that count. The dataset ID is supplied once to the benchmark instead of on every row.

The grader computes and records the per-image metrics; the benchmark uses the primitive statistics
for evaluation-wide calculations. Gather checks each row's `sample_id` against the ordered
prediction receipts. Its position supplies the global index, which is recorded in the separate
prediction index. Filenames, prediction digests, and shard numbers stay in
those organizer-owned receipts and indexes instead of being duplicated in every JSONL row. The
participant wheel does not contain those trusted organizer tasks. Currently target TIFFs are
organizer-held, so seeing the WDL source does not let a participant regenerate this input from
predictions alone. A self-run path will require a separately published, participant-runnable
raw-grading WDL and approved target inputs, followed by this benchmark.

This synthetic one-row file is a complete valid example. The zero statistics describe an identical
all-zero prediction and target; cosine is undefined because both vectors have zero norm:

```jsonl
{"metrics":{"absolute_error_sum":0.0,"cosine_similarity":null,"dot_product_sum":0.0,"maximum_absolute_error":0.0,"mean_absolute_error":0.0,"prediction_square_sum":0.0,"root_mean_squared_error":0.0,"squared_error_sum":0.0,"target_square_sum":0.0},"sample_id":"image-000001"}
```

The organizer limits the merged file to 128 MiB by default (configurable). The benchmark streams
the file instead of loading all rows into memory.

## Run it

From a source checkout, install the participant project and pass the merged file:

```sh
python -m pip install -e ./participant
python -m dream_participant.benchmarking.evaluation \
  --image-results image-results.jsonl \
  --comparison-report comparison-report.json \
  --aggregate evaluation-aggregate.json \
  --output score-result.json
```

`--dataset-id` is optional for local runs and defaults to `local`. Supply the real dataset ID
when you need output files with the same dataset label and hashes as the organizer run; the
organizer WDL passes it automatically.

The score equally averages four components. For evaluation-wide MAE, RMSE, and maximum absolute
error, each named component (`mae_score`, `rmse_score`, and `maximum_error_score`) is
`1 / (1 + error / target_RMS)`, where `target_RMS = sqrt(target_square_sum / element_count)` from
the aggregate. If `target_RMS` is zero, an error of zero scores 1 and a nonzero error scores 0.
The `cosine_similarity_score` is `(1 + cosine_similarity) / 2`; when cosine is undefined because
of a zero norm, it scores 1 only when both prediction and target norms are zero, otherwise 0. The
overall score is the arithmetic mean of those four values, ranges from 0 to 1, and higher is
better. The score result includes all four component values; the comparison report retains raw
evaluation-wide metrics.

The outputs are:

| File | Contents |
|---|---|
| `comparison-report.json` | Evaluation-wide MAE, RMSE, cosine similarity, maximum error, and image/pixel counts. |
| `evaluation-aggregate.json` | Image count, pixel count, and one `metrics` object containing the additive sums and derived evaluation-wide metrics. |
| `score-result.json` | Numeric score and its four named components. |

To reproduce a benchmark result, keep the input file unchanged. For a local run inside the
organizer's WDL container, use the `BenchmarkEvaluation` task in
`organizer/workflows/templates/benchmark.wdl` in the source repository; it invokes this exact Python
module. The standalone participant wheel contains the benchmark and its image-results input
contract, but no raw grader, gather task, or organizer CLI. Participant submission and staging
tools remain part of the package; organizer GCS and state-management code do not.
