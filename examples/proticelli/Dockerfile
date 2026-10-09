FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ARG TARGETARCH=amd64
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
COPY model-runtime.constraints.txt /tmp/model-runtime.constraints.txt
RUN test "${TARGETARCH}" = "amd64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates coreutils curl git libgomp1 tar \
    && rm -rf /var/lib/apt/lists/*

ARG BLAST_VERSION=2.17.0
RUN curl --fail --location --retry 3 \
        "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/${BLAST_VERSION}/ncbi-blast-${BLAST_VERSION}+-x64-linux.tar.gz" \
        --output /tmp/ncbi-blast.tar.gz \
    && echo "3888112d8207831aa47371d93583c601f058f88b5db22dc782438b039a3a411b  /tmp/ncbi-blast.tar.gz" | sha256sum --check - \
    && tar -xzf /tmp/ncbi-blast.tar.gz -C /opt \
    && ln -s "/opt/ncbi-blast-${BLAST_VERSION}+/bin/blastp" /usr/local/bin/blastp \
    && ln -s "/opt/ncbi-blast-${BLAST_VERSION}+/bin/blastdbcmd" /usr/local/bin/blastdbcmd \
    && rm /tmp/ncbi-blast.tar.gz \
    && blastp -version | head -n 1 | grep -F "${BLAST_VERSION}" \
    && blastdbcmd -version | head -n 1 | grep -F "${BLAST_VERSION}"

RUN python3 -m venv ${VIRTUAL_ENV} \
    && python3 -m pip install --no-cache-dir --constraint /tmp/model-runtime.constraints.txt \
        torch==2.14.1+cu126 torchvision==0.29.1+cu126 \
        --index-url https://download.pytorch.org/whl/cu126 \
    && python3 -c "import torch, torchvision; assert torch.__version__ == '2.14.1+cu126'; assert torchvision.__version__ == '0.29.1+cu126'"

ARG PROTICELLI_COMMIT=f894e78d2fbc0530c3637d5f7c457ead0738a5d0
ARG PROTICELLI_SOURCE_SHA256=006b60ebed38d12382ef57075a68fe1369d9028a1cc5e299dc4d7710e3f5c86b
RUN curl --fail --location --retry 5 --retry-all-errors \
        --connect-timeout 30 --max-time 300 \
        "https://codeload.github.com/CellProfiling/ProtiCelli/tar.gz/${PROTICELLI_COMMIT}" \
        --output /tmp/proticelli-src.tar.gz \
    && echo "${PROTICELLI_SOURCE_SHA256}  /tmp/proticelli-src.tar.gz" | sha256sum --check - \
    && mkdir -p /opt/proticelli-src \
    && tar -xzf /tmp/proticelli-src.tar.gz --strip-components=1 -C /opt/proticelli-src \
        "ProtiCelli-${PROTICELLI_COMMIT}/pyproject.toml" \
        "ProtiCelli-${PROTICELLI_COMMIT}/README.md" \
        "ProtiCelli-${PROTICELLI_COMMIT}/proticelli" \
    && rm /tmp/proticelli-src.tar.gz \
    && cd /opt/proticelli-src \
    && python3 -m pip install --no-cache-dir --constraint /tmp/model-runtime.constraints.txt . \
        tifffile==2026.9.9 \
    && cd / \
    && rm -rf /opt/proticelli-src \
    && python3 -m pip check \
    && python3 -c "import proticelli, torch, torchvision; from proticelli import Model; assert proticelli.__file__.startswith('/opt/venv/'); assert torch.__version__ == '2.14.1+cu126'; assert torchvision.__version__ == '0.29.1+cu126'"

COPY predict.py proticelli_model.py proticelli_images.py /opt/proticelli/
RUN python3 -m py_compile /opt/proticelli/predict.py /opt/proticelli/proticelli_model.py /opt/proticelli/proticelli_images.py \
    && python3 /opt/proticelli/predict.py --help
