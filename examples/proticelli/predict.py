"""Run the ProtiCelli prediction lane and expose its command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import proticelli_images
import proticelli_model


def predict_shard(
    inputs_manifest: str | Path,
    weights: str | Path,
    *,
    batch_size: int = 4,
    inference_steps: int = 50,
    seed: int = 0,
    output: str | Path,
) -> tuple[Path, ...]:
    """Map distinct FASTA sequences once and write ID-matched prediction PNGs."""
    if batch_size <= 0 or inference_steps <= 0:
        raise ValueError("batch_size and inference_steps must be positive")
    try:
        import numpy as np
        from proticelli import Model
    except ImportError as error:
        raise RuntimeError("the ProtiCelli runtime needs numpy, tifffile, and Model") from error

    inputs = proticelli_model.read_inputs_manifest(inputs_manifest)
    proticelli_model.validate_inputs(inputs)
    candidates_by_path = proticelli_images._fasta_cache(inputs)
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="proticelli-weights-") as temporary:
        weight_location = proticelli_model.unpack_weights(weights, Path(temporary))
        checkpoint_dir, vae_dir = proticelli_model._model_directories(weight_location.model_root)
        database = proticelli_model._blast_database(weight_location.search_roots)
        gene_map = proticelli_model.load_gene_map(
            proticelli_model._gene_map_path(weight_location.search_roots)
        )
        model = Model(
            checkpoint_dir=checkpoint_dir,
            vae_dir=vae_dir,
            device="cuda",
            dtype="float32",
        )
        protein_vocabulary = model.protein_map
        vocabulary_index = proticelli_model.build_vocabulary_index(protein_vocabulary)
        selections: dict[str, proticelli_model.ProteinSelection] = {}
        for sequence in dict.fromkeys(sequence for candidates in candidates_by_path.values()
                                      for sequence in candidates):
            try:
                selection = proticelli_model.select_supported_protein(
                    sequence,
                    database=database,
                    gene_map=gene_map,
                    protein_vocabulary=protein_vocabulary,
                    vocabulary_index=vocabulary_index,
                )
            except proticelli_model.UnsupportedProteinError:
                continue
            selections[sequence] = selection
            print(
                json.dumps(
                    {
                        "stage": "protein_selection",
                        "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                        "protein_name": selection.protein_name,
                        "uniprot_accession": selection.accession,
                        "blast_hit_rank": selection.hit_rank,
                        "bitscore": selection.bitscore,
                        "evalue": selection.evalue,
                        "percent_identity": selection.percent_identity,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        sequences_by_path: dict[Path, str] = {}
        for path, candidates in candidates_by_path.items():
            supported = [sequence for sequence in candidates if sequence in selections]
            if not supported:
                raise proticelli_model.UnsupportedProteinError(
                    f"no sequence in FASTA {path.name} has a supported BLAST match"
                )
            sequences_by_path[path] = min(
                supported, key=lambda sequence: (-selections[sequence].bitscore,
                                                  selections[sequence].evalue)
            )

        for batch in proticelli_images.iter_image_batches(inputs, sequences_by_path, selections):
            normalized, nucleus_scales = proticelli_images.normalize_inputs(batch.images)
            result = model.predict(
                normalized,
                protein_names=tuple(item.protein_name for item in batch.proteins),
                batch_size=batch_size,
                num_inference_steps=inference_steps,
                seed=seed,
                show_progress=False,
            )
            if isinstance(result, dict):
                result = result.get("images")
            elif hasattr(result, "images"):
                result = result.images
            if result is None:
                raise ValueError("ProtiCelli returned no images")
            predictions = np.asarray(result, dtype=np.float32)
            expected_shape = (len(batch.inputs), 512, 512)
            if predictions.shape != expected_shape:
                raise ValueError(f"ProtiCelli predictions must have shape {list(expected_shape)}")
            for item, selection, prediction, nucleus_scale in zip(
                batch.inputs, batch.proteins, predictions, nucleus_scales, strict=True
            ):
                sequence = sequences_by_path[item.fasta_file]
                output_paths.append(
                    proticelli_images._write_prediction(
                        destination,
                        item.prediction_id,
                        prediction,
                        selection,
                        sequence,
                        nucleus_scale,
                    )
                )
    return tuple(output_paths)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run ProtiCelli from microtubules_image, nucleus_image, er_image, and fasta_file "
            "inputs; write each result as <prediction_id>.png."
        )
    )
    parser.add_argument("--inputs-manifest", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--inference-steps", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    predict_shard(
        args.inputs_manifest,
        args.weights,
        batch_size=args.batch_size,
        inference_steps=args.inference_steps,
        seed=args.seed,
        output=args.output,
    )


if __name__ == "__main__":
    main()
