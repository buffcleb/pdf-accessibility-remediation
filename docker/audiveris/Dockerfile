# The music reading service: Audiveris (optical music recognition, AGPL-3.0, https://github.com/Audiveris/audiveris)
# and a small loop that takes pictures of music from a Redis list and puts MusicXML back (serve.py).
# Only needed where OMR_ENABLED is on; on a cluster it runs only while there is music to read.
#
# Audiveris ships for x86-64 only: build with --platform linux/amd64.
# Base images are pinned to a digest (the tag says which, the digest makes it that build and no other); the
# digests are those of 2026-10-13, and moving to a newer build is a deliberate change of this line.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ARG AUDIVERIS_VERSION=5.11.0
ARG TESSDATA_VERSION=4.1.0
# Libraries Audiveris bundles at versions with fixed vulnerabilities (scripts/build.sh's Trivy scan), replaced by the
# fixed release: Maven Central path and SHA-256, and the bundled jar it replaces. Revisit when Audiveris updates them.
ARG JAR_SWAPS="\
com/itextpdf/itextpdf/5.5.13.6/itextpdf-5.5.13.6.jar c2b290659fd4381e5512cc9e69c67b409150b84921dc367b1ec983c376ca18f6 itextpdf-5.5.13.2.jar \
com/fasterxml/jackson/core/jackson-core/2.21.7/jackson-core-2.21.7.jar 8c5623b98f32d5f7e1287ff61ed50e08ab7b11381e991fff89327faabba42261 jackson-core-2.20.0.jar \
com/fasterxml/jackson/core/jackson-databind/2.21.7/jackson-databind-2.21.7.jar 1290c2795e93e8a6861a6c4d9ff0d844d32f5ea178362cb2721edf5561e828b1 jackson-databind-2.20.0.jar \
com/fasterxml/jackson/core/jackson-annotations/2.21/jackson-annotations-2.21.jar 53ca085f4a150f703f49e1aabd935bd03b43e1ea3d55d135438292af22cef56b jackson-annotations-2.20.jar"
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
 # The fixed libraries in place of the bundled ones, each checked against its SHA-256, and the launcher's class path
 # (lib/app/Audiveris.cfg) pointed at them. commons-io 2.4 goes: commons-io 2.16.1, which Audiveris also bundles, comes
 # first on the class path and is the one loaded.
 && cd /opt/audiveris/lib/app \
 && set -- $JAR_SWAPS \
 && while [ $# -ge 3 ]; do \
      new="$(basename "$1")"; \
      curl -fsSL -o "$new" "https://repo1.maven.org/maven2/$1" \
      && echo "$2  $new" | sha256sum -c - \
      && test -f "$3" && rm "$3" \
      && sed -i "s|\$APPDIR/$3\$|\$APPDIR/$new|" Audiveris.cfg \
      && grep -q "\$APPDIR/$new\$" Audiveris.cfg || exit 1; \
      shift 3; \
    done \
 && rm org.apache.commons.io-2.4.jar && sed -i '/org\.apache\.commons\.io-2\.4\.jar/d' Audiveris.cfg \
 && grep -q 'commons-io-2.16.1.jar' Audiveris.cfg \
 && cd / \
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
