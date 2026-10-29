# Base images are pinned to a digest (the tag says which, the digest makes it that build and no other); the
# digests are those of 2026-10-13, and moving to a newer build is a deliberate change of this line.

# MathCAT (MIT): formulas' speech by rule and their Nemeth and UEB braille (#403), as one small program built from
# docker/mathcat with its crates locked (Cargo.lock) and its rules compiled in. Built on the machine's own platform
# and cross-compiled for the image's (no C in it, so a linker is all it needs): building for the cluster's amd64 on an
# arm64 Mac then takes a minute or two instead of the half hour emulation would.
FROM --platform=$BUILDPLATFORM rust:1.98.1-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730 AS mathcat
ARG TARGETARCH
ARG BUILDARCH
WORKDIR /build
RUN case "${TARGETARCH:-amd64}" in \
      amd64) echo x86_64-unknown-linux-gnu > /target; echo x86-64 > /gcc ;; \
      arm64) echo aarch64-unknown-linux-gnu > /target; echo aarch64 > /gcc ;; \
      *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && if [ "${TARGETARCH:-amd64}" != "${BUILDARCH:-amd64}" ]; then \
         apt-get update && apt-get install -y --no-install-recommends "gcc-$(cat /gcc)-linux-gnu" \
           "libc6-dev-${TARGETARCH}-cross" \
         && rm -rf /var/lib/apt/lists/*; fi \
    && rustup target add "$(cat /target)"
COPY docker/mathcat/ ./
RUN t="$(cat /target)" \
    && if [ "${TARGETARCH:-amd64}" != "${BUILDARCH:-amd64}" ]; then \
         export "CARGO_TARGET_$(echo "$t" | tr 'a-z-' 'A-Z_')_LINKER=$(tr - _ < /gcc)-linux-gnu-gcc"; fi \
    && cargo build --release --locked --target "$t" && cp "target/$t/release/mathcat-cli" /mathcat-cli

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

# veraPDF: a named release, and the download is checked against the project's release signature (the key's
# fingerprint is the one https://docs.verapdf.org/install/ prints) before anything from it runs
ARG VERAPDF_VERSION=1.30.3
ARG VERAPDF_URL=https://software.verapdf.org/releases/1.30/verapdf-greenfield-${VERAPDF_VERSION}-installer.zip
ARG VERAPDF_SIGNING_FINGERPRINT=13DD102B4DD69354D12DE5A83184863278B17FE7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VERAPDF_PATH=/opt/verapdf/verapdf \
    DATA_DIR=/data

# Java for veraPDF, Tesseract for OCR of scanned pages, DejaVu as the embeddable text-layer font,
# Liberation as metric-compatible substitutes for unembedded Times/Arial/Helvetica/Courier, and Pango for
# WeasyPrint (the PDF report). Then packages of the base image with a fixed vulnerability newer than its digest
# (scripts/build.sh's Trivy scan): OpenSSL (CVE-2026-84782, fixed in 3.0.22-1~deb12u2). Drop a package from the
# upgrade once the pinned base image has the fix.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless tesseract-ocr tesseract-ocr-eng fonts-dejavu-core fonts-liberation2 curl unzip gnupg \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    && apt-get install -y --no-install-recommends --only-upgrade openssl libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/verapdf-auto-install.xml /tmp/verapdf-auto-install.xml
RUN curl -fsSL -o /tmp/verapdf.zip "$VERAPDF_URL" && curl -fsSL -o /tmp/verapdf.zip.asc "$VERAPDF_URL.asc" \
    && export GNUPGHOME=/tmp/gnupg && mkdir -m 700 "$GNUPGHOME" \
    && curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x$VERAPDF_SIGNING_FINGERPRINT" | gpg --batch --import \
    && gpg --batch --with-colons --fingerprint | grep -q "^fpr:::::::::$VERAPDF_SIGNING_FINGERPRINT:" \
    && gpg --batch --verify /tmp/verapdf.zip.asc /tmp/verapdf.zip \
    && rm -rf "$GNUPGHOME" /tmp/verapdf.zip.asc \
    && unzip -q /tmp/verapdf.zip -d /tmp/verapdf \
    && java -jar "$(find /tmp/verapdf -name 'verapdf-izpack-installer-*.jar' | head -1)" /tmp/verapdf-auto-install.xml \
    && rm -rf /tmp/verapdf /tmp/verapdf.zip \
    && /opt/verapdf/verapdf --version | grep -F "$VERAPDF_VERSION"

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=mathcat /mathcat-cli /usr/local/bin/mathcat-cli
COPY app ./app
# The image's tag (scripts/build.sh), recorded with every result
ARG ENGINE_VERSION=""
ENV ENGINE_VERSION=$ENGINE_VERSION
RUN useradd --system --uid 10001 --home /srv appuser && mkdir -p /data && chown appuser /data
# Fontconfig (used when pages are rendered) keeps a cache: give it somewhere it can write, or every render logs
# "No writable cache directories"
ENV XDG_CACHE_HOME=/tmp/.cache
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
