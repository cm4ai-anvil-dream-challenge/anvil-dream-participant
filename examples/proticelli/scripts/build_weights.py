"""Build submission assets using BLAST already installed in the participant image."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
from http.client import HTTPException
from importlib import resources
import json
import pickle
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile
import zlib

from tqdm import tqdm


MODEL_FOLDERS = ("checkpoint/unet", "checkpoint/unet_ema", "vae")
BLAST_BIN = Path("/opt/ncbi-blast-2.17.0+/bin")
REFERENCE_FILENAME = "uniprot_sprot_human.dat.gz"
REFERENCE_SHA256 = "1af63498762d426240e30c9eb40e0124cd6af1b9eb35f440d1b4e89a097999bc"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
BLAST_TITLE = "Supported reviewed human UniProt Swiss-Prot reference"
VOCABULARY_RESOURCE = "proticelli.data/antibody_map.pkl"


def _source_url(source: str) -> str | None:
    parsed = urlsplit(source)
    if not parsed.scheme and not parsed.netloc:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Reference URLs must use HTTPS")
    path_parts = {part.casefold() for part in parsed.path.split("/")}
    if path_parts.intersection({"current_release", "latest"}):
        raise ValueError("Reference URL is mutable; provide a versioned HTTPS URL or a local file")
    has_release = any(
        re.fullmatch(r"(?:release-)?20\d{2}_\d{2}", part, re.IGNORECASE) for part in path_parts
    )
    has_content_hash = REFERENCE_SHA256 in source.casefold()
    if not (has_release or has_content_hash):
        raise ValueError(
            "Reference URL must include a release identifier or the pinned SHA-256; "
            "use a local file for other sources"
        )
    return source


def _copy_remote_reference(source_url: str, destination: Path) -> None:
    for attempt in range(3):
        try:
            with urlopen(source_url, timeout=60) as response:
                expected_size = response.headers.get("Content-Length")
                received = 0
                print("Receiving pinned Swiss-Prot reference", flush=True)
                with (
                    destination.open("wb") as output,
                    tqdm(
                        total=int(expected_size) if expected_size else None,
                        desc="Downloading Swiss-Prot",
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                    ) as bar,
                ):
                    while chunk := response.read1(64 * 1024):
                        output.write(chunk)
                        received += len(chunk)
                        bar.update(len(chunk))
                if expected_size and received != int(expected_size):
                    raise EOFError("Reference size does not match Content-Length")
            return
        except (OSError, EOFError, HTTPException) as error:
            destination.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError(
                    "UniProt reference download failed after 3 attempts; "
                    "no weights bundle was created."
                ) from error
            print(f"Download failed ({error}); retrying ({attempt + 2}/3)", flush=True)
            time.sleep(2**attempt)


def download_reference(
    directory: Path,
    source: str,
    expected_sha256: str | None = None,
) -> tuple[Path, dict[str, object]]:
    """Copy an explicit local source or download a versioned HTTPS source."""
    path = directory / REFERENCE_FILENAME
    if expected_sha256 is None:
        expected_sha256 = REFERENCE_SHA256
    source_url = _source_url(source)
    if source_url is None:
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            raise ValueError(f"Reference source is not a file: {source_path}")
        with source_path.open("rb") as input_file, path.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        identity: dict[str, object] = {"kind": "local_file"}
    else:
        print(f"Downloading pinned Swiss-Prot reference from {source_url}", flush=True)
        _copy_remote_reference(source_url, path)
        identity = {"kind": "https_url", "url": source_url}

    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha256:
        path.unlink(missing_ok=True)
        raise ValueError(
            f"UniProt reference SHA-256 mismatch: expected {expected_sha256}, received {digest}"
        )

    try:
        with gzip.open(path, "rb") as compressed:
            while compressed.read(1024 * 1024):
                pass
    except (OSError, EOFError, zlib.error) as error:
        path.unlink(missing_ok=True)
        raise ValueError("Pinned UniProt reference is not a valid gzip file") from error

    identity["sha256"] = digest
    return path, identity


def supported_gene_names(protein_vocabulary: dict[str, int]) -> set[str]:
    return {
        token.strip().casefold()
        for name in protein_vocabulary
        for token in (name, *name.split(","))
        if token.strip()
    }


def load_supported_vocabulary() -> tuple[set[str], dict[str, object]]:
    vocabulary_bytes = resources.files("proticelli.data").joinpath("antibody_map.pkl").read_bytes()
    protein_vocabulary = pickle.loads(vocabulary_bytes)
    if (
        not isinstance(protein_vocabulary, dict)
        or not protein_vocabulary
        or any(
            not isinstance(name, str) or not name.strip() or type(index) is not int
            for name, index in protein_vocabulary.items()
        )
    ):
        raise ValueError(f"Invalid ProtiCelli protein vocabulary in {VOCABULARY_RESOURCE}")
    identity: dict[str, object] = {
        "resource": VOCABULARY_RESOURCE,
        "sha256": hashlib.sha256(vocabulary_bytes).hexdigest(),
        "protein_count": len(protein_vocabulary),
    }
    return supported_gene_names(protein_vocabulary), identity


def extract_human_references(
    source: Path,
    fasta: Path,
    gene_map: Path,
    supported_vocabulary: set[str],
) -> tuple[int, int]:
    accessions = set()
    selected_count = 0
    record = []
    with (
        gzip.open(source, "rt") as data,
        fasta.open("w") as sequences,
        gene_map.open("w", newline="") as mapping,
    ):
        writer = csv.writer(mapping, delimiter="\t", lineterminator="\n")
        writer.writerow(("accession", "hgnc_symbol", "ensembl_gene_ids"))
        for line in data:
            if not line.startswith("//"):
                record.append(line)
                continue
            text = "".join(record)
            record = []
            identifier = re.search(r"^ID   (\S+)\s+Reviewed;", text, re.MULTILINE)
            taxonomy = re.search(r"^OX   NCBI_TaxID=(\d+)", text, re.MULTILINE)
            if not identifier or not taxonomy or taxonomy[1] != "9606":
                continue
            accession = re.search(r"^AC   ([^;]+);", text, re.MULTILINE)
            sequence_block = re.search(
                r"^SQ   SEQUENCE\s+(\d+) AA;[^\n]*\n(.*)",
                text,
                re.MULTILINE | re.DOTALL,
            )
            if not accession or not sequence_block:
                raise ValueError("Reviewed human record is missing accession or sequence")
            sequence = "".join(sequence_block[2].split())
            if not re.fullmatch(r"[A-Z]+", sequence) or len(sequence) != int(sequence_block[1]):
                raise ValueError(f"Invalid sequence for {accession[1]}")
            if accession[1] in accessions:
                raise ValueError(f"Duplicate accession: {accession[1]}")
            accessions.add(accession[1])
            genes = " ".join(
                line[5:].strip() for line in text.splitlines() if line.startswith("GN   ")
            )
            names = re.findall(r"(?:^|[ ;])Name=([^;{]+)", genes)
            symbol = " ".join(name.strip() for name in names)
            if symbol.casefold() not in supported_vocabulary:
                continue
            ensembl = " ".join(
                line for line in text.splitlines() if line.startswith("DR   Ensembl;")
            )
            gene_ids = ";".join(sorted(set(re.findall(r"ENSG\d+", ensembl))))
            sequences.write(f">sp|{accession[1]}|{identifier[1]}\n{sequence}\n")
            writer.writerow((accession[1], symbol, gene_ids))
            selected_count += 1
        if record:
            raise ValueError("Truncated Swiss-Prot record")
    return len(accessions), selected_count


def normalize_blast_metadata(database: Path) -> None:
    """Remove BLAST's wall-clock creation date from its version-5 JSON metadata."""
    metadata_path = database.with_suffix(".pjs")
    metadata = json.loads(metadata_path.read_text())
    created_at = metadata.pop("last-updated", None)
    if not isinstance(created_at, str):
        raise ValueError(f"BLAST metadata has no last-updated field: {metadata_path}")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def _zip_info(name: str, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def write_deterministic_zip(
    destination: Path,
    members: Iterable[tuple[str, Path | bytes]],
    compression: int,
) -> None:
    """Write files with stable ordering metadata and permissions."""
    with zipfile.ZipFile(destination, "w", compression=compression, allowZip64=True) as archive:
        archive.comment = b""
        for name, source in members:
            info = _zip_info(name, compression)
            if isinstance(source, Path):
                with (
                    source.open("rb") as input_file,
                    archive.open(info, "w", force_zip64=True) as output_file,
                ):
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            else:
                archive.writestr(info, source)


def _provenance_bytes(provenance: dict[str, object]) -> bytes:
    return (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_references(directory: Path, reference_source: str) -> tuple[Path, Path]:
    supported_vocabulary, vocabulary_identity = load_supported_vocabulary()
    source, source_provenance = download_reference(directory, reference_source)
    fasta = directory / "human_reviewed.fasta"
    gene_map = directory / "human_gene_map.tsv"
    reviewed_count, selected_count = extract_human_references(
        source, fasta, gene_map, supported_vocabulary
    )
    if reviewed_count < 1000:
        raise ValueError(f"Expected a human proteome, found only {reviewed_count} reviewed records")
    if selected_count == 0:
        raise ValueError(
            "No reviewed human UniProt records match the ProtiCelli protein vocabulary"
        )
    print(
        f"Selected {selected_count:,} supported sequences from "
        f"{reviewed_count:,} reviewed human records",
        flush=True,
    )

    database = directory / "human_reviewed_swissprot"
    subprocess.run(
        [
            str(BLAST_BIN / "makeblastdb"),
            "-in",
            str(fasta),
            "-dbtype",
            "prot",
            "-blastdb_version",
            "5",
            "-title",
            BLAST_TITLE,
            "-out",
            str(database),
        ],
        check=True,
    )
    normalize_blast_metadata(database)
    subprocess.run([str(BLAST_BIN / "blastdbcmd"), "-db", str(database), "-info"], check=True)
    version = subprocess.check_output([str(BLAST_BIN / "blastp"), "-version"], text=True)
    provenance = {
        "source": source_provenance,
        "selection": (
            "reviewed; NCBI_TaxID=9606; GN Name resolves through the exact and comma-token "
            "ProtiCelli vocabulary index"
        ),
        "total_reviewed_human_records": reviewed_count,
        "selected_accession_count": selected_count,
        "vocabulary": vocabulary_identity,
        "blast_version": version.strip(),
    }

    database_files = sorted(directory.glob("human_reviewed_swissprot.*"))
    blast_zip = directory / "human_blast_db.zip"
    write_deterministic_zip(
        blast_zip,
        [
            *((path.name, path) for path in database_files),
            ("PROVENANCE.json", _provenance_bytes(provenance)),
        ],
        zipfile.ZIP_DEFLATED,
    )
    map_zip = directory / "human_gene_map.zip"
    write_deterministic_zip(
        map_zip,
        [
            (gene_map.name, gene_map),
            ("PROVENANCE.json", _provenance_bytes(provenance)),
        ],
        zipfile.ZIP_DEFLATED,
    )
    return blast_zip, map_zip


def assemble(model: Path, references: tuple[Path, Path], output: Path) -> None:
    members: list[tuple[str, Path]] = []
    for folder in MODEL_FOLDERS:
        for name in ("config.json", "diffusion_pytorch_model.safetensors"):
            members.append((f"{folder}/{name}", model / folder / name))
    members.extend(("reference/" + path.name, path) for path in references)
    write_deterministic_zip(output, members, zipfile.ZIP_STORED)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--reference-source",
        required=True,
        help="local UniProt gzip file or immutable versioned HTTPS URL",
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    for folder in MODEL_FOLDERS:
        for filename in ("config.json", "diffusion_pytorch_model.safetensors"):
            if not (args.model_dir / folder / filename).is_file():
                parser.error(f"Missing model file: {folder}/{filename}")
        if (args.model_dir / folder).is_symlink() or any(
            path.is_symlink() for path in (args.model_dir / folder).rglob("*")
        ):
            parser.error(f"Symlinks are not allowed in {folder}")
    for tool in ("makeblastdb", "blastp", "blastdbcmd"):
        if not (BLAST_BIN / tool).is_file():
            parser.error(f"Missing {tool}; run build_weights.sh with the participant image")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".weights-build-", dir=args.output.parent) as temporary:
        directory = Path(temporary)
        references = build_references(directory, args.reference_source)
        staged = directory / "weights.zip"
        assemble(args.model_dir, references, staged)
        with zipfile.ZipFile(staged) as archive:
            invalid = archive.testzip()
            if invalid:
                raise ValueError(f"ZIP validation failed: {invalid}")
        with staged.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        args.output.hardlink_to(staged)
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes)", flush=True)
    print(f"SHA-256: {digest}", flush=True)


if __name__ == "__main__":
    main()
