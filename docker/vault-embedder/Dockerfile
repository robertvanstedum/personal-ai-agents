# The Vault's pinned embedder (#660): Ollama serving nomic-embed-text@0a109f422b47 (768 dims), CPU only, the model inside the image.
#
#   docker build -t <tag> docker/vault-embedder            (the context is this folder only)
#
# Every base is pinned by digest. The model is pulled here, at build, and the build FAILS unless its manifest digest is exactly the
# pinned one, so the running container needs no internet and can serve nothing else. Only the CPU back ends are kept: the upstream
# image is ~3.4 GB because of its GPU libraries; this one is ~350 MB. Clients still check the model's full digest and a known-answer
# vector before they embed anything (core/memory_index/embedder.py, embedder_probe.json).

# Ollama 0.40.17: the version the workstation's Ollama runs, so production vectors match the workstation index.
FROM ollama/ollama:0.40.17@sha256:ccc42bcd3d5fcf7ab0d532b300382154e1a4da56fc666bdb313b78ebdd912754 AS model
ARG PIN=0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f
ENV OLLAMA_MODELS=/models OLLAMA_HOST=127.0.0.1:11434
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN set -eu; \
    ollama serve >/tmp/serve.log 2>&1 & pid=$!; \
    for _ in $(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    ollama pull nomic-embed-text:latest; \
    kill "$pid"; wait "$pid" || true; \
    got=$(sha256sum /models/manifests/registry.ollama.ai/library/nomic-embed-text/latest | cut -d' ' -f1); \
    if [ "$got" != "$PIN" ]; then echo "nomic-embed-text digest $got is not the pinned $PIN" >&2; exit 1; fi; \
    mkdir -p /out/lib; \
    find /usr/lib/ollama -maxdepth 1 \( -type f -o -type l \) -exec cp -a {} /out/lib/ \; ; \
    ls /out/lib | grep -q '^libggml-cpu' ; \
    chmod -R a+rX,go-w /models

FROM ubuntu:noble-20261024.1@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN useradd --system --uid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin embedder
COPY --from=model /usr/bin/ollama /usr/bin/ollama
COPY --from=model /out/lib/ /usr/lib/ollama/
COPY --from=model /models /models
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
