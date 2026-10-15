# The music reading service: Audiveris (optical music recognition, AGPL-3.0, https://github.com/Audiveris/audiveris)
# and a small loop that takes pictures of music from a Redis list and puts MusicXML back (serve.py).
# Only needed where OMR_ENABLED is on; on a cluster it runs only while there is music to read.
#
# Audiveris ships for x86-64 only: build with --platform linux/amd64.
# Base images are pinned to a digest (the tag says which, the digest makes it that build and no other); the
# digests are those of 2026-10-13, and moving to a newer build is a deliberate change of this line.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG AUDIVERIS_VERSION=5.11.0
ARG TESSDATA_VERSION=4.1.0
ENV DEBIAN_FRONTEND=noninteractive
# libgtk-3-0: Audiveris asks GTK for the screen scale at start, even with no screen
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl python3 python3-redis \
      libgtk-3-0 libfreetype6 fontconfig \
 && curl -fsSL -o /tmp/audiveris.deb \
      "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/Audiveris-${AUDIVERIS_VERSION}-ubuntu24.04-x86_64.deb" \
 # The package's own setup adds a desktop menu entry, which a container has nowhere to put: unpack it instead
 && dpkg-deb -x /tmp/audiveris.deb / \
 && test -x /opt/audiveris/bin/Audiveris \
 # Audiveris reads words with Tesseract's older engine, which needs the full language file (distributions ship
 # the newer engine's only)
 && mkdir -p /opt/tessdata \
 && curl -fsSL -o /opt/tessdata/eng.traineddata \
      "https://github.com/tesseract-ocr/tessdata/raw/${TESSDATA_VERSION}/eng.traineddata" \
 && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /tmp/audiveris.deb /var/lib/apt/lists/*

# Where Audiveris looks for the words in a score (lyrics, tempo, chord names)
ENV TESSDATA_PREFIX=/opt/tessdata
RUN useradd --create-home --uid 10001 omr
USER omr
WORKDIR /home/omr
COPY docker/audiveris/serve.py /opt/serve.py
CMD ["python3", "/opt/serve.py"]
