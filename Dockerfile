# Base images are pinned to a digest (the tag says which, the digest makes it that build and no other); the
# digests are those of 2026-10-13, and moving to a newer build is a deliberate change of this line.
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
# WeasyPrint (the PDF report).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless tesseract-ocr tesseract-ocr-eng fonts-dejavu-core fonts-liberation2 curl unzip gnupg \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
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

COPY app ./app
RUN useradd --system --uid 10001 --home /srv appuser && mkdir -p /data && chown appuser /data
# Fontconfig (used when pages are rendered) keeps a cache: give it somewhere it can write, or every render logs
# "No writable cache directories"
ENV XDG_CACHE_HOME=/tmp/.cache
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
