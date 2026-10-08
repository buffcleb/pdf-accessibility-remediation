FROM python:3.12-slim-bookworm

ARG VERAPDF_URL=https://software.verapdf.org/releases/verapdf-installer.zip

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VERAPDF_PATH=/opt/verapdf/verapdf \
    DATA_DIR=/data

# Java for veraPDF, Tesseract for OCR of scanned pages, DejaVu as the embeddable text-layer font,
# Liberation as metric-compatible substitutes for unembedded Times/Arial/Helvetica/Courier, and Pango for
# WeasyPrint (the PDF report).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless tesseract-ocr tesseract-ocr-eng fonts-dejavu-core fonts-liberation2 curl unzip \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

COPY docker/verapdf-auto-install.xml /tmp/verapdf-auto-install.xml
RUN curl -fsSL -o /tmp/verapdf.zip "$VERAPDF_URL" \
    && unzip -q /tmp/verapdf.zip -d /tmp/verapdf \
    && java -jar "$(find /tmp/verapdf -name 'verapdf-izpack-installer-*.jar' | head -1)" /tmp/verapdf-auto-install.xml \
    && rm -rf /tmp/verapdf /tmp/verapdf.zip \
    && /opt/verapdf/verapdf --version

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN useradd --system --uid 10001 --home /srv appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
