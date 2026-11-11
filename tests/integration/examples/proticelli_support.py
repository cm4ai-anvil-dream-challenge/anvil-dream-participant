from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = ROOT / "examples/proticelli"


def load_participant_module(filename: str, module_name: str) -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    adapter_path = ADAPTER_DIR / filename
    if str(ADAPTER_DIR) not in sys.path:
        sys.path.insert(0, str(ADAPTER_DIR))
    spec = importlib.util.spec_from_file_location(module_name, adapter_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def reference_zip(*files: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files:
            archive.writestr(name, value)
    return buffer.getvalue()


def write_weights_zip(path: Path) -> None:
    gene_map = (
        "accession\thgnc_symbol\tensembl_gene_ids\n"
        "P00001\t\n"
        "P00002\tNOT_IN_VOCAB\t\n"
        "P00003\tTUBB\tENSG000001"
    ).encode()
    blast_db = reference_zip(
        ("human_reviewed_swissprot.phr", b"phr"),
        ("human_reviewed_swissprot.pin", b"pin"),
        ("human_reviewed_swissprot.psq", b"psq"),
    )
    gene_map_zip = reference_zip(("human_gene_map.tsv", gene_map))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("checkpoint/unet/config.json", "{}")
        archive.writestr("checkpoint/unet/weights.safetensors", "weights")
        archive.writestr("vae/config.json", "{}")
        archive.writestr("vae/weights.safetensors", "weights")
        archive.writestr("reference/human_blast_db.zip", blast_db)
        archive.writestr("reference/human_gene_map.zip", gene_map_zip)
