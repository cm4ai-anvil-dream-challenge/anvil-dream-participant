#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: bash $0 MODEL_DIRECTORY OUTPUT_ZIP UNIPROT_GZ_OR_IMMUTABLE_HTTPS_URL" >&2
    exit 1
fi

MODEL_DIRECTORY=$(cd "$1" && pwd)
OUTPUT_DIRECTORY=$(dirname "$2")
mkdir -p "$OUTPUT_DIRECTORY"
OUTPUT_DIRECTORY=$(cd "$OUTPUT_DIRECTORY" && pwd)
OUTPUT_NAME=$(basename "$2")
SCRIPT_DIRECTORY=$(cd "$(dirname "$0")" && pwd)
REFERENCE_SOURCE=$3
SOURCE_MOUNT=()
if [[ -f "$REFERENCE_SOURCE" ]]; then
    REFERENCE_DIRECTORY=$(cd "$(dirname "$REFERENCE_SOURCE")" && pwd)
    REFERENCE_NAME=$(basename "$REFERENCE_SOURCE")
    SOURCE_MOUNT=(--mount "type=bind,source=$REFERENCE_DIRECTORY/$REFERENCE_NAME,target=/reference/$REFERENCE_NAME,readonly")
    REFERENCE_SOURCE="/reference/$REFERENCE_NAME"
fi
IMAGE=${PROTICELLI_IMAGE:-quay.io/anvil_dream/blast-proticelli-example@sha256:1aa99ed21859b8c0b7f9868c53ac97ae50e6d417aca963cbbd483de5f3a8bbec}

docker run --rm --platform linux/amd64 --entrypoint python3 \
    --mount "type=bind,source=$MODEL_DIRECTORY,target=/model,readonly" \
    --mount "type=bind,source=$OUTPUT_DIRECTORY,target=/output" \
    --mount "type=bind,source=$SCRIPT_DIRECTORY,target=/builder,readonly" \
    "${SOURCE_MOUNT[@]}" \
    "$IMAGE" /builder/build_weights.py \
    --model-dir /model --output "/output/$OUTPUT_NAME" \
    --reference-source "$REFERENCE_SOURCE"
