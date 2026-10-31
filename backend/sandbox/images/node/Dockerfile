FROM node:20-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git config --system --unset-all safe.directory 2>/dev/null || true \
    && git config --system --add safe.directory /workspace \
    && git config --system --add safe.directory /tmp/reporunner-repository-clone

RUN git --version && node --version && npm --version
