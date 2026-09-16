FROM mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27
USER root
RUN apt-get update && apt-get install -y --no-install-recommends fluxbox x11vnc xdotool && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/recorder
COPY runtime/recorder/package.json runtime/recorder/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && node node_modules/esbuild/install.js
ADD --checksum=sha256:5066103959ef4e9b10f37e5a148627360dd8414e4cf8a7db92bdbd022e728aaa https://codeload.github.com/novnc/noVNC/tar.gz/refs/tags/v1.6.0 /tmp/novnc.tar.gz
RUN mkdir novnc && tar -xzf /tmp/novnc.tar.gz --strip-components=1 -C novnc && rm /tmp/novnc.tar.gz
COPY runtime/runner/egress.mjs /opt/runner/egress.mjs
COPY runtime/recorder/*.mjs runtime/recorder/*.js runtime/recorder/*.sh ./
COPY runtime/recorder/public/ ./public/
RUN node_modules/.bin/esbuild viewer.js --bundle --format=esm --outfile=public/viewer.js && chmod +x entrypoint.sh
ENV DISPLAY=:99 HOME=/tmp/home
USER pwuser
EXPOSE 6080
ENTRYPOINT ["/opt/recorder/entrypoint.sh"]
