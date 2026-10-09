"""Resolve prediction inputs, packaged weights, and supported ProtiCelli proteins."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_PREDICTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

_PROTEIN_RESIDUES = re.compile(r"[A-Z*]+\Z")

_BLAST_DATABASE_BASENAME = "human_reviewed_swissprot"

_GENE_MAP_BASENAME = "human_gene_map.tsv"

_BLAST_DATABASE_EXTENSIONS = frozenset({".phr", ".pin", ".psq", ".pdb", ".pal"})

_DATABASE_SEQUENCE_COUNT = re.compile(r"(?im)^\s*([\d,]+)\s+sequences?\b")

_DATABASE_COUNT_LABEL = re.compile(r"(?im)^\s*(?:number of )?sequences\s*:\s*([\d,]+)\s*$")


@dataclass(frozen=True)
class PredictionInput:
    prediction_id: str
    microtubules_image: Path
    nucleus_image: Path
    er_image: Path
    fasta_file: Path
    mask_image: Path | None = None


@dataclass(frozen=True)
class ProteinSelection:
    protein_name: str
    accession: str
    hit_rank: int
    bitscore: float
    evalue: float
    percent_identity: float


class UnsupportedProteinError(ValueError):
    """No BLAST neighbor maps to the model's protein vocabulary."""


@dataclass(frozen=True)
class BlastHit:
    accession: str
    bitscore: float
    evalue: float
    percent_identity: float
    order: int


@dataclass(frozen=True)
class WeightsLocation:
    model_root: Path
    search_roots: tuple[Path, ...]


def _label(value: object, field: str, position: int) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\x00" in value:
        raise ValueError(f"input manifest item {position} has an invalid {field}")
    return value


def read_inputs_manifest(path: str | Path) -> tuple[PredictionInput, ...]:
    """Parse the localized `write_json(inputs)` record shape at the task boundary."""
    manifest = Path(path).expanduser().resolve()
    if not manifest.is_file():
        raise ValueError(f"input manifest not found: {manifest}")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read input manifest: {error}") from error
    if not isinstance(value, list) or not value:
        raise ValueError("input manifest must be a non-empty JSON array")

    expected = {
        "prediction_id",
        "microtubules_image",
        "nucleus_image",
        "er_image",
        "fasta_file",
    }
    inputs: list[PredictionInput] = []
    seen: set[str] = set()
    for position, item in enumerate(value):
        if not isinstance(item, dict) or set(item) - {"mask_image"} != expected:
            raise ValueError(
                f"input manifest item {position} must contain exactly "
                "prediction_id, microtubules_image, nucleus_image, er_image, and fasta_file"
            )
        prediction_id = _label(item["prediction_id"], "prediction_id", position)
        if not _PREDICTION_ID.fullmatch(prediction_id):
            raise ValueError(f"input manifest item {position} has an invalid prediction_id")
        key = prediction_id.casefold()
        if key in seen:
            raise ValueError(f"input manifest contains duplicate prediction_id {prediction_id!r}")
        seen.add(key)
        paths = {
            name: Path(_label(item[name], name, position)).expanduser().resolve()
            for name in expected - {"prediction_id"}
        }
        inputs.append(
            PredictionInput(
                prediction_id,
                paths["microtubules_image"],
                paths["nucleus_image"],
                paths["er_image"],
                paths["fasta_file"],
                Path(_label(item["mask_image"], "mask_image", position)).expanduser().resolve()
                if item.get("mask_image") is not None else None,
            )
        )
    return tuple(inputs)


def validate_inputs(inputs: tuple[PredictionInput, ...]) -> None:
    """Require three PNG/TIFF images, an explicit FASTA, and any supplied mask."""
    for item in inputs:
        images = (
            ("microtubules_image", item.microtubules_image),
            ("nucleus_image", item.nucleus_image),
            ("er_image", item.er_image),
        )
        if item.mask_image is not None:
            images += (("mask_image", item.mask_image),)
        for field, path in images:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{field} not found for {item.prediction_id}: {path}")
            if path.suffix.casefold() not in {".png", ".tif", ".tiff"}:
                raise ValueError(f"{field} must be a PNG or TIFF for {item.prediction_id}")
        if item.fasta_file.is_symlink() or not item.fasta_file.is_file():
            raise ValueError(f"FASTA not found for {item.prediction_id}: {item.fasta_file}")


def read_fasta_sequences(path: Path) -> tuple[str, ...]:
    """Read every named protein sequence without inferring content from filenames."""
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"could not read FASTA {path.name}: {error}") from error
    if not lines or not lines[0].startswith(">") or not lines[0][1:].strip():
        raise ValueError(f"FASTA {path.name} must contain named sequences")
    records: list[list[str]] = []
    for line in lines:
        if line.startswith(">"):
            if not line[1:].strip():
                raise ValueError(f"FASTA {path.name} contains an unnamed sequence")
            records.append([])
        else:
            records[-1].append(line.strip())
    sequences = tuple("".join(record).upper() for record in records)
    if any(not sequence or not _PROTEIN_RESIDUES.fullmatch(sequence) for sequence in sequences):
        raise ValueError(f"FASTA {path.name} contains an empty or invalid protein sequence")
    return sequences


def load_gene_map(path: str | Path) -> dict[str, str]:
    """Load every accession in the packaged map, including rows without an HGNC symbol.

    Older full-reference bundles can include unmapped accessions. They still
    consume BLAST's ``max_target_seqs`` result budget.
    """
    gene_map: dict[str, str] = {}
    try:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fields = set(reader.fieldnames or ())
            if not {"accession", "hgnc_symbol"}.issubset(fields):
                raise ValueError("gene map must contain accession and hgnc_symbol TSV columns")
            for position, row in enumerate(reader, start=2):
                accession = (row.get("accession") or "").strip()
                symbol = (row.get("hgnc_symbol") or "").strip()
                if not accession:
                    continue
                previous = gene_map.setdefault(accession, symbol)
                if previous != symbol:
                    raise ValueError(
                        f"gene map has conflicting symbols for {accession} at row {position}"
                    )
    except (OSError, csv.Error) as error:
        raise ValueError(f"could not read gene map: {error}") from error
    if not gene_map or not any(gene_map.values()):
        raise ValueError("gene map contains no accession rows with HGNC symbols")
    return gene_map


def _database_accession(value: str) -> str:
    identifier = value.split()[0]
    if identifier.startswith(("sp|", "tr|")):
        parts = identifier.split("|")
        if len(parts) > 1:
            identifier = parts[1]
    return identifier


@lru_cache(maxsize=32)
def _blast_database_target_count(database: str, blastdbcmd: str = "blastdbcmd") -> int:
    """Read the real BLAST database sequence count once per database path."""
    try:
        completed = subprocess.run(
            [blastdbcmd, "-db", database, "-info"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("could not read the packaged BLAST database sequence count") from error
    output = f"{completed.stdout}\n{completed.stderr}"
    counts = [int(value.replace(",", "")) for value in _DATABASE_SEQUENCE_COUNT.findall(output)]
    if not counts:
        counts = [int(value.replace(",", "")) for value in _DATABASE_COUNT_LABEL.findall(output)]
    if not counts or sum(counts) <= 0:
        raise ValueError("blastdbcmd -info returned no positive database sequence count")
    return sum(counts)


def _ranked_blast_hits(
    sequence: str,
    database: str | Path,
    *,
    target_count: int,
    blastp: str = "blastp",
) -> tuple[BlastHit, ...]:
    """Return ranked distinct UniProt targets with one HSP per target."""
    if target_count <= 0:
        raise ValueError("BLAST target count must be positive")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".faa", encoding="ascii") as query:
        query.write(f">query\n{sequence}\n")
        query.flush()
        completed = subprocess.run(
            [
                blastp,
                "-query",
                query.name,
                "-db",
                str(database),
                "-outfmt",
                "6 sseqid bitscore evalue pident",
                "-max_hsps",
                "1",
                "-max_target_seqs",
                str(target_count),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    hits: list[BlastHit] = []
    for order, line in enumerate(completed.stdout.splitlines()):
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError("BLAST returned an invalid tabular result")
        accession, bitscore, evalue, identity = fields
        hits.append(
            BlastHit(
                _database_accession(accession),
                float(bitscore),
                float(evalue),
                float(identity),
                order,
            )
        )
    hits.sort(key=lambda hit: (-hit.bitscore, hit.evalue, hit.order))
    return tuple(hits)


def build_vocabulary_index(protein_vocabulary: Mapping[str, int]) -> dict[str, str]:
    """Index exact and comma-token gene names once per prediction lane."""
    exact = {name.casefold(): name for name in protein_vocabulary}
    candidates: dict[str, tuple[int, str]] = {}
    for name in protein_vocabulary:
        specificity = len(name.split(","))
        for token in name.split(","):
            key = token.strip().casefold()
            candidate = (specificity, name)
            if key not in candidates or (specificity, name.casefold()) < (
                candidates[key][0],
                candidates[key][1].casefold(),
            ):
                candidates[key] = candidate
    result = {key: candidate[1] for key, candidate in candidates.items()}
    result.update(exact)
    return result


def select_supported_protein(
    sequence: str,
    *,
    database: str | Path,
    gene_map: Mapping[str, str],
    protein_vocabulary: Mapping[str, int],
    vocabulary_index: Mapping[str, str] | None = None,
    blastp: str = "blastp",
) -> ProteinSelection:
    """Walk BLAST neighbors in rank order to the first mapped vocabulary protein."""
    if not protein_vocabulary:
        raise ValueError("ProtiCelli protein vocabulary is empty")
    supported_labels = vocabulary_index or build_vocabulary_index(protein_vocabulary)
    hits = _ranked_blast_hits(
        sequence,
        database,
        target_count=_blast_database_target_count(str(database)),
        blastp=blastp,
    )
    for rank, hit in enumerate(hits, start=1):
        symbol = gene_map.get(hit.accession)
        if symbol is None and "-" in hit.accession:
            symbol = gene_map.get(hit.accession.split("-", 1)[0])
        if not symbol:
            continue
        label = supported_labels.get(symbol.casefold())
        if label is not None:
            return ProteinSelection(
                label,
                hit.accession,
                rank,
                hit.bitscore,
                hit.evalue,
                hit.percent_identity,
            )
    sequence_digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    raise UnsupportedProteinError(
        "BLAST returned no hit supported by the ProtiCelli protein vocabulary "
        f"for sequence sha256={sequence_digest} ({len(hits)} ranked hits checked)"
    )


def _model_directories(root: Path) -> tuple[Path, Path]:
    checkpoint_candidates = sorted(
        {
            path.parent.parent
            for path in root.rglob("unet/config.json")
            if path.is_file()
            and any(
                child.is_file() for child in path.parent.iterdir() if child.name != "config.json"
            )
        }
    )
    vae_candidates = sorted(
        {
            path.parent
            for path in root.rglob("config.json")
            if path.parent.name.casefold() in {"vae", "variational_autoencoder"}
            and any(
                child.is_file() for child in path.parent.iterdir() if child.name != "config.json"
            )
        }
    )
    if len(checkpoint_candidates) != 1 or len(vae_candidates) != 1:
        raise ValueError("weights must contain exactly one ProtiCelli checkpoint and VAE")
    return checkpoint_candidates[0], vae_candidates[0]


def _blast_database(roots: tuple[Path, ...]) -> Path:
    candidates = {
        path.with_suffix("")
        for root in roots
        for path in root.rglob(f"{_BLAST_DATABASE_BASENAME}.*")
        if path.is_file() and path.suffix.casefold() in _BLAST_DATABASE_EXTENSIONS
    }
    if len(candidates) != 1:
        raise ValueError(f"weights must contain one {_BLAST_DATABASE_BASENAME} BLAST database")
    return candidates.pop()


def _gene_map_path(roots: tuple[Path, ...]) -> Path:
    candidates = {
        path for root in roots for path in root.rglob(_GENE_MAP_BASENAME) if path.is_file()
    }
    if len(candidates) != 1:
        raise ValueError(f"weights must contain one {_GENE_MAP_BASENAME}")
    return next(iter(candidates))


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    """Extract regular ZIP files only, rejecting traversal and symlink entries."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    seen: set[Path] = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            name = member.filename
            if not name or "\\" in name:
                raise ValueError("weights ZIP contains an invalid path")
            relative = Path(name)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError("weights ZIP contains an unsafe path")
            target = (root / relative).resolve()
            if target != root and root not in target.parents:
                raise ValueError("weights ZIP contains an unsafe path")
            if target in seen:
                raise ValueError("weights ZIP contains duplicate paths")
            seen.add(target)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("weights ZIP may not contain symlinks")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise ValueError("weights ZIP may contain only files and directories")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def _extract_nested_reference_zips(root: Path, destination: Path) -> tuple[Path, ...]:
    """Extract the two reference archives used by the published ProtiCelli ZIP."""
    references = root / "reference"
    extracted: list[Path] = []
    for basename in ("human_blast_db", "human_gene_map"):
        archive_path = references / f"{basename}.zip"
        if archive_path.is_file():
            reference_root = destination / basename
            _safe_extract_zip(archive_path, reference_root)
            extracted.append(reference_root)
    return (destination,) if extracted else ()


def unpack_weights(weights: str | Path, destination: Path) -> WeightsLocation:
    """Load a directory, ZIP bundle, or TAR and localize nested reference ZIPs."""
    source = Path(weights).expanduser().resolve()
    if source.is_dir():
        root = source
        extracted_references = _extract_nested_reference_zips(root, destination / "reference-data")
    elif source.is_file() and zipfile.is_zipfile(source):
        root = destination / "weights"
        _safe_extract_zip(source, root)
        extracted_references = _extract_nested_reference_zips(root, root / "reference-data")
    elif source.is_file() and tarfile.is_tarfile(source):
        root = destination / "weights"
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:*") as archive:
            members = archive.getmembers()
            for member in members:
                if not (member.isfile() or member.isdir()):
                    raise ValueError("weights archive may contain only files and directories")
                target = (root / member.name).resolve()
                if target != root and root not in target.parents:
                    raise ValueError("weights archive contains an unsafe path")
            archive.extractall(root, members=members)
        extracted_references = _extract_nested_reference_zips(root, root / "reference-data")
    else:
        raise ValueError("weights must be a directory, ZIP archive, or TAR archive")
    return WeightsLocation(root, (root, *extracted_references))
