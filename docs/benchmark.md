# Public evaluation benchmark

This package benchmarks an already merged `image-results.jsonl` file. The organizer grades
individual prediction images and gathers their results into that file. The benchmark reads the
per-image results, computes evaluation-wide metrics and a score, and writes three JSON files.
It does not need prediction TIFFs, target TIFFs, shard receipts, or GCS access.

The implementation is [`evaluation.py`](../benchmarking/evaluation.py). The organizer calls the same module from
[`benchmark.wdl`](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-anvil-dream-tools/blob/feature/cleanup/organizer/workflows/templates/benchmark.wdl). The participant package
works on its own; running the Python benchmark does not require the organizer package.

## Input file

The input is UTF-8 JSON Lines (JSONL), with one JSON object and one newline per image. The
organizer's gather task writes the rows in dataset order. Keep one row per image and pass the
complete file to the benchmark; do not average rows before benchmarking.

Each object must have exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `sample_id` | string | Nonempty, trimmed image ID. The benchmark requires it for the input contract, though the score uses only `metrics`. |
| `metrics` | object | The nine image-level values defined below. |

`metrics` contains exactly these fields. Here `p` and `t` are corresponding prediction and
target pixel values for one `512 × 512` image, and each sum runs over its 262,144 pixels.

| Field | Definition |
|---|---|
| `absolute_error_sum` | Sum of `abs(p - t)`. |
| `squared_error_sum` | Sum of `(p - t)²`. |
| `dot_product_sum` | Sum of `p × t`. |
| `prediction_square_sum` | Sum of `p²`. |
| `target_square_sum` | Sum of `t²`. |
| `maximum_absolute_error` | Largest `abs(p - t)` in the image. |
| `mean_absolute_error` | `absolute_error_sum / 262144`. |
| `root_mean_squared_error` | Square root of `squared_error_sum / 262144`. |
| `cosine_similarity` | `dot_product_sum / (sqrt(prediction_square_sum) × sqrt(target_square_sum))`, clamped to `[-1, 1]`, or `null` if either norm is zero. |

The grader computes these values from prediction and target TIFFs using float64 arithmetic.
Numbers must be finite; the three derived metrics must agree with the sums. The benchmark
adds the sums across images, takes the largest image-level maximum error, and recalculates
evaluation-wide MAE, RMSE, and cosine similarity. The fixed image size supplies the pixel count.
The command takes the dataset ID once, so rows do not repeat it.

The organizer's gather task checks `sample_id` and row order against the prediction receipts.
Filenames, prediction hashes, and shard numbers stay in organizer records rather than in each
JSONL row. The current target TIFFs are private. Participants can benchmark a supplied JSONL
file, but cannot recreate the official rows from predictions alone without the targets.

This is one valid JSONL row. It describes an identical all-zero prediction and target, so
cosine similarity is `null`:

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
  --dataset-id YOUR_DATASET_ID \
  --comparison-report comparison-report.json \
  --aggregate evaluation-aggregate.json \
  --output score-result.json
```

Replace `YOUR_DATASET_ID` with the dataset ID for the input. The flag is optional for a local
check and defaults to `local`; the organizer WDL passes the actual ID.

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

To reproduce an official benchmark result, use the same JSONL bytes and dataset ID. The
organizer's [`BenchmarkEvaluation`](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-anvil-dream-tools/blob/feature/cleanup/organizer/workflows/templates/benchmark.wdl) WDL task
invokes this Python module. The participant package includes this benchmark and its input
contract. Raw grading and gathering remain organizer tasks.
