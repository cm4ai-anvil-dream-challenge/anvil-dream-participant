"""Load three channel PNG/TIFF batches and write ID-matched prediction images."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from proticelli_model import PredictionInput, ProteinSelection, read_fasta_sequences


_INPUT_IMAGE_SHAPE = (512, 512)
_PREDICTION_SHAPE = (512, 512)
_MAX_IMAGES_PER_LOAD = 1000


@dataclass(frozen=True)
class ImageBatch:
    images: Any
    inputs: tuple[PredictionInput, ...]
    proteins: tuple[ProteinSelection, ...]


@lru_cache(maxsize=64)
def _read_image(path: Path) -> Any:
    import numpy as np
    import tifffile

    try:
        if path.suffix.casefold() == ".png":
            from PIL import Image

            with Image.open(path) as source:
                if source.format != "PNG" or source.mode != "L":
                    raise ValueError(f"PNG must be 8-bit grayscale: {path.name}")
                image = np.asarray(source, dtype=np.uint8)
        else:
            image = tifffile.imread(path)
    except Exception as error:
        raise ValueError(f"could not read image {path}: {error}") from error
    if tuple(image.shape) != _INPUT_IMAGE_SHAPE:
        raise ValueError(f"image {path.name} must have shape [512, 512]")
    if image.dtype != np.dtype("uint8"):
        raise ValueError(f"image {path.name} must use uint8")
    if not np.isfinite(image).all():
        raise ValueError(f"image {path.name} must contain only finite values")
    return image


def _fasta_cache(inputs: tuple[PredictionInput, ...]) -> dict[Path, tuple[str, ...]]:
    cache: dict[Path, tuple[str, ...]] = {}
    for item in inputs:
        if item.fasta_file not in cache:
            cache[item.fasta_file] = read_fasta_sequences(item.fasta_file)
    return cache


def iter_image_batches(
    inputs: tuple[PredictionInput, ...],
    sequences_by_path: Mapping[Path, str],
    selections: Mapping[str, ProteinSelection],
    *,
    max_images_per_load: int = _MAX_IMAGES_PER_LOAD,
) -> Iterator[ImageBatch]:
    """Stack three separate grayscale inputs in ProtiCelli channel order."""
    if max_images_per_load <= 0:
        raise ValueError("max_images_per_load must be positive")
    import numpy as np

    for start in range(0, len(inputs), max_images_per_load):
        batch_inputs = inputs[start : start + max_images_per_load]
        images = []
        proteins = []
        for item in batch_inputs:
            if item.mask_image is not None:
                _read_image(item.mask_image)
            images.append(
                np.stack(
                    (
                        _read_image(item.microtubules_image),
                        _read_image(item.nucleus_image),
                        _read_image(item.er_image),
                    ),
                    axis=-1,
                )
            )
            proteins.append(selections[sequences_by_path[item.fasta_file]])
        yield ImageBatch(np.stack(images, axis=0), batch_inputs, tuple(proteins))


def _write_prediction(
    destination: Path,
    prediction_id: str,
    image: Any,
    selection: ProteinSelection,
    sequence: str,
) -> Path:
    import numpy as np
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    prediction = np.asarray(image, dtype=np.float32)
    if tuple(prediction.shape) != _PREDICTION_SHAPE or not np.isfinite(prediction).all():
        raise ValueError(f"ProtiCelli returned an invalid prediction for {prediction_id}")
    sequence_digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    provenance = {
        "prediction_id": prediction_id,
        "sequence_sha256": sequence_digest,
        "protein_name": selection.protein_name,
        "uniprot_accession": selection.accession,
        "blast_hit_rank": selection.hit_rank,
        "bitscore": selection.bitscore,
        "evalue": selection.evalue,
        "percent_identity": selection.percent_identity,
    }
    output = destination / f"{prediction_id}.png"
    encoded = np.clip(prediction / np.float32(1.3) * np.float32(255), 0, 255).astype(np.uint8)
    metadata = PngInfo()
    metadata.add_text("provenance", json.dumps(provenance, sort_keys=True))
    Image.fromarray(encoded).save(output, pnginfo=metadata)
    return output
