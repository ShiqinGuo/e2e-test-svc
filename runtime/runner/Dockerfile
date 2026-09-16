FROM mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27
USER root
WORKDIR /opt/runner
RUN npm init -y && npm pkg set type=module && npm install --save-exact @playwright/test@1.63.0 fflate@0.8.3 && npm cache clean --force
COPY runtime/runner/ /opt/runner/
RUN chmod -R a+rX /opt/runner
USER pwuser
WORKDIR /work
ENTRYPOINT ["node", "/opt/runner/entry.mjs"]
