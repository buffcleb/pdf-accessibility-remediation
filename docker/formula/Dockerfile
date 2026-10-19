# The formula reading service (#403): reads a picture of a formula into LaTeX with RapidLaTeXOCR (Apache-2.0;
# pix2tex's model, MIT, as ONNX), and a small loop that takes pictures from a Redis list and puts the LaTeX back
# (serve.py). It gives the engine a second reading of each formula, independent of the AI's, to check it against.
# Only needed where FORMULA_READER is on; on a cluster it runs only while there are formulas to read.
# Base images are pinned to a digest (the tag says which, the digest makes it that build and no other); the
# digests are those of 2026-10-13, and moving to a newer build is a deliberate change of this line.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

# The model, downloaded once here (not at each start, from GitHub) and checked against its SHA-256
ARG MODEL_URL=https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0
ARG MODEL_FILES="\
encoder.onnx 01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d \
decoder.onnx bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072 \
image_resizer.onnx e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d \
tokenizer.json 1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# OpenCV's libraries
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*
COPY docker/formula/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && models="$(python -c 'import os, rapid_latex_ocr; print(os.path.join(os.path.dirname(rapid_latex_ocr.__file__), "models"))')" \
 && mkdir -p "$models" && cd "$models" \
 && set -- $MODEL_FILES \
 && while [ $# -ge 2 ]; do \
      curl -fsSL -o "$1" "$MODEL_URL/$1" && echo "$2  $1" | sha256sum -c - || exit 1; \
      shift 2; \
    done \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 reader
USER reader
WORKDIR /home/reader
COPY docker/formula/serve.py /opt/serve.py
CMD ["python3", "/opt/serve.py"]
