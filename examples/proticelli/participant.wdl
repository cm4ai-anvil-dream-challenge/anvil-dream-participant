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

  # The batch size, inference steps, and seed below are ProtiCelli settings.
  # They are not part of the organizer-facing participant contract.
  # The organizer adds the execution timeout; participants do not set it here.
  # Files supplied with --other-files are available here at the same relative
  # paths they had inside that directory.
  command <<<
    python3 /opt/proticelli/predict.py \
      --inputs-manifest "~{write_json(inputs)}" \
      --weights "~{weights}" \
      --batch-size 4 \
      --inference-steps 50 \
      --seed 0 \
      --output predictions
  >>>

  runtime {
    docker: "quay.io/anvil_dream/blast-proticelli-example@sha256:59c9a92a5b0b73f1298bf4eac1b9a02d57d505d349c9cd71f43c99f5314d8bf8"
  }

  output {
    Array[File] predictions = glob("predictions/*.tiff")
  }
}
