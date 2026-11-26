# The Vault's pinned embedder (#660): Ollama serving nomic-embed-text@0a109f422b47 (768 dims), CPU only, the model inside the image.
#
#   docker build -t <tag> docker/vault-embedder            (the context is this folder only)
#
# Pinned three ways: the base image by digest, Ollama's release tarball by its published sha256, and the model by its manifest
# (committed here, nomic-embed-text.manifest.json) and each blob's digest. The build FAILS if any of them differs, so the running container needs no internet and can serve nothing else. Only the
# CPU back ends are unpacked (the tarball's CUDA and Vulkan libraries are 2.3 GB of its 2.4 GB); the image is ~400 MB, and the
# build needs ~1.5 GB of temporary disk for the tarball. Clients still check the model's full digest and a known-answer vector
# before they embed anything (core/memory_index/embedder.py, embedder_probe.json).

FROM ubuntu:noble-20261024.1@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 AS fetch
# Ollama 0.40.17: the version the workstation's Ollama runs, so these vectors match the workstation index.
ARG OLLAMA_VERSION=0.40.17
ARG OLLAMA_SHA256=1df9d6228bac5a22e420b242708c0d10e5b886db97bab99d446a41760f4e79af
ARG PIN=0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl zstd && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    curl -fsSL -o /tmp/ollama.tar.zst "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"; \
    echo "${OLLAMA_SHA256}  /tmp/ollama.tar.zst" | sha256sum -c -; \
    mkdir -p /opt/ollama; \
    zstd -dc /tmp/ollama.tar.zst | tar -x -C /opt/ollama --exclude='lib/ollama/cuda_*' --exclude='lib/ollama/vulkan' \
        --exclude='lib/ollama/rocm*' --exclude='lib/ollama/mlx*'; \
    rm -f /tmp/ollama.tar.zst; \
    ls /opt/ollama/lib/ollama | grep -q '^libggml-cpu'; \
    if ls -d /opt/ollama/lib/ollama/*/ >/dev/null 2>&1; then echo "unexpected library folder kept" >&2; exit 1; fi
# The model by content, not by tag. The pinned manifest is committed beside this file (its bytes hash to the pin; the registry
# serves manifests by tag only, and `ollama pull` would re-serialize it so /api/tags reported another digest for the same
# weights). Every blob it names is fetched by its own digest and checked. Ollama 0.40 also wants its newer manifest folders to
# exist; they stay empty, and the model is served from the legacy manifest, as on the workstation.
COPY nomic-embed-text.manifest.json /tmp/manifest.json
RUN set -eu; \
    reg=https://registry.ollama.ai/v2/library/nomic-embed-text; \
    m=/models/manifests/registry.ollama.ai/library/nomic-embed-text; mkdir -p "$m" /models/blobs; \
    echo "$PIN  /tmp/manifest.json" | sha256sum -c -; \
    cp /tmp/manifest.json "$m/latest"; \
    for d in $(grep -o 'sha256:[0-9a-f]\{64\}' "$m/latest" | sort -u); do \
      h=${d#sha256:}; curl -fsSL -o "/models/blobs/sha256-$h" "$reg/blobs/$d"; echo "$h  /models/blobs/sha256-$h" | sha256sum -c -; \
    done; \
    mkdir -p /models/manifests-v2 /models/metadata; \
    chmod -R a+rX,go-w /models

FROM ubuntu:noble-20261024.1@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN useradd --system --uid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin embedder
COPY --from=fetch /opt/ollama/bin/ollama /usr/bin/ollama
COPY --from=fetch /opt/ollama/lib/ollama/ /usr/lib/ollama/
COPY --from=fetch /models /models
ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/models \
    HOME=/tmp/home \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_CONTEXT_LENGTH=2048 \
    OLLAMA_KEEP_ALIVE=15m \
    OLLAMA_NOPRUNE=1
USER 10001
# Smoke test as the runtime user on the model store it will serve read-only: start, embed one sentence, 768 numbers back.
RUN set -eu; \
    OLLAMA_HOST=127.0.0.1:11434 ollama serve >/tmp/serve.log 2>&1 & pid=$!; \
    for _ in $(seq 1 60); do OLLAMA_HOST=127.0.0.1:11434 ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    OLLAMA_HOST=127.0.0.1:11434 ollama list | grep -Eq '^nomic-embed-text:latest[[:space:]]+0a109f422b47'; \
    body='{"model":"nomic-embed-text:latest","input":["search_document: vault embedder smoke test"],"keep_alive":0}'; \
    exec 3<>/dev/tcp/127.0.0.1/11434; \
    printf 'POST /api/embed HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s' "${#body}" "$body" >&3; \
    reply=$(cat <&3); exec 3>&-; \
    commas=$(printf '%s' "$reply" | sed -n 's/.*"embeddings":\[\[\([^]]*\)\]\].*/\1/p' | tr -cd ',' | wc -c); \
    if [ "$commas" -ne 767 ]; then echo "embedding is not 768 wide ($commas commas)" >&2; cat /tmp/serve.log >&2; exit 1; fi; \
    kill "$pid"; wait "$pid" || true; rm -rf /tmp/home /tmp/serve.log
EXPOSE 11434
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
