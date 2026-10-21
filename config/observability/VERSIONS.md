# Observability component versions (spec 161 §10.1)

Pinned stable releases, each at least two weeks old when chosen (20 Oct 2026, verified via the
GitHub releases API and PyPI). Record the image digests at install time with
`docker buildx imagetools inspect <image> --format '{{json .Manifest.Digest}}'` and fill them in.

| Component | Version | Released | Image / package | Digest |
|---|---|---|---|---|
| OpenTelemetry Collector (contrib) | 0.162.0 | 2026-09-29 | `otel/opentelemetry-collector-contrib:0.162.0` | _record at install_ |
| Prometheus | 3.15.0 | 2026-09-25 | `prom/prometheus:v3.15.0` | _record at install_ |
| Alertmanager | 0.34.1 | 2026-09-17 | `prom/alertmanager:v0.34.1` | _record at install_ |
| OTel Python SDK + OTLP/HTTP exporter | 1.45.1 | 2026-10-06 | `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` (requirements.txt) | n/a |

Upgrade procedure: spec 161 §12.3.
