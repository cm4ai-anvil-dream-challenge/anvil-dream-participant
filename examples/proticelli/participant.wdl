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
    docker: "quay.io/anvil_dream/blast-proticelli-example@sha256:9e03517cc45ac0ad72386620d34bf24a54a12a297f65ab79a2c2ac9a9debfa18"
  }

  output {
    Array[File] predictions = glob("predictions/*.png")
  }
}
