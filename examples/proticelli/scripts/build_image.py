"""Build with a persistent, checksum-verified host cache for ProtiCelli source."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="proticelli-nucleus-scaled")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text()
    commit = re.search(r"^ARG PROTICELLI_COMMIT=(\w+)$", dockerfile, re.M).group(1)
    digest = re.search(r"^ARG PROTICELLI_SOURCE_SHA256=(\w+)$", dockerfile, re.M).group(1)
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "proticelli-build"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"{digest}.tar.gz"

    def verified(path: Path) -> bool:
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest

    if verified(archive):
        print(f"Using verified cached source: {archive}", flush=True)
    else:
        print(f"Downloading pinned ProtiCelli source to {cache}", flush=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            download = Path(temporary) / "source.tar.gz"
            subprocess.run([
                "curl", "--fail", "--location", "--retry", "2", "--retry-all-errors",
                "--connect-timeout", "15", "--max-time", "90",
                "--speed-limit", "1024", "--speed-time", "20",
                f"https://codeload.github.com/CellProfiling/ProtiCelli/tar.gz/{commit}",
                "--output", str(download),
            ], check=True, timeout=300)
            if not verified(download):
                raise SystemExit("Downloaded source checksum does not match the pinned Dockerfile.")
            download.replace(archive)

    if args.prepare_only:
        return
    docker = ["docker"]
    if subprocess.run(docker + ["info"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        docker = ["sudo", "-n", "docker"]
    start = dockerfile.index("RUN curl --fail --location --retry 5")
    end = dockerfile.index('    && echo "${PROTICELLI_SOURCE_SHA256}', start)
    cached = (dockerfile[:start]
              + "COPY proticelli-src.tar.gz /tmp/proticelli-src.tar.gz\nRUN "
              + dockerfile[end:].replace("    && echo", "echo", 1))
    with tempfile.TemporaryDirectory(prefix="proticelli-build-") as temporary:
        context = Path(temporary)
        for name in ("model-runtime.constraints.txt", "predict.py",
                     "proticelli_model.py", "proticelli_images.py"):
            shutil.copy2(root / name, context / name)
        shutil.copy2(archive, context / "proticelli-src.tar.gz")
        (context / "Dockerfile").write_text(cached)
        subprocess.run(docker + ["build", "--platform", "linux/amd64",
                                 "-t", args.tag, str(context)], check=True)


if __name__ == "__main__":
    main()
