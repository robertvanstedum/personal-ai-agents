# Spec — Metrics Stack: Prometheus + Grafana on EC2
*Created: 2026-06-22 — Claude.ai*
*Status: spec_ready — build after operations monitoring Part 1 (CoS health checks)*
*Extracted from: spec_monitoring_stack_2026-06-18.md (Sentry absorbed into #100)*

---

## Context

EC2 health monitoring via CoS (spec_operations_monitoring_v2) gives
alerts when something breaks. Prometheus + Grafana gives visibility
into how the system is performing over time — request rates, response
times, analysis job durations, cron run times.

These are complementary. CoS says "something is wrong right now."
Grafana shows trends, patterns, and whether things are getting
better or worse over time.

Both run as Docker containers on the same EC2 instance alongside
the existing stack. No new infrastructure needed.

---

## What this adds

**Prometheus** scrapes a `/metrics` endpoint from each Flask app
every 15 seconds and stores the time-series data.

**Grafana** reads from Prometheus and displays dashboards.
Claude Code builds the dashboards. Robert opens
`grafana.minimoi.ai` to read them.

---

## Part 1 — Prometheus

### Flask instrumentation

One library, one line per app:

```bash
pip install prometheus-flask-exporter
```

```python
from prometheus_flask_exporter import PrometheusMetrics
metrics = PrometheusMetrics(app)
```

This automatically adds `/metrics` to each Flask app with:
- Request counts by endpoint and status code
- Response time histograms
- Exception counts

### Custom metrics — what to instrument

**Mein Deutsch:**
```python
from prometheus_client import Counter, Histogram, Gauge

analysis_jobs_total = Counter(
    'gesprache_analysis_jobs_total',
    'Analysis jobs submitted',
    ['model', 'status']
)

analysis_duration = Histogram(
    'gesprache_analysis_duration_seconds',
    'Analysis job duration',
    ['model'],
    buckets=[5, 10, 15, 20, 30, 60]
)

websocket_connections = Gauge(
    'gesprache_websocket_connections_active',
    'Active WebSocket connections'
)
```

**Curator:**
```python
curator_loop_duration = Histogram(
    'curator_loop_duration_seconds',
    'Curator scoring loop duration'
)

curator_articles_scored = Counter(
    'curator_articles_scored_total',
    'Articles scored',
    ['status']
)
```

### Prometheus container

Add to `docker-compose.prod.yml`:

```yaml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  ports:
    - "127.0.0.1:9090:9090"
  restart: unless-stopped

volumes:
  prometheus_data:
```

Note: bound to `127.0.0.1` only — not exposed to internet.

`prometheus.yml` at repo root:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'minimoi-portal'
    static_configs:
      - targets: ['portal:5001']

  - job_name: 'minimoi-curator'
    static_configs:
      - targets: ['curator:8766']

  - job_name: 'minimoi-german'
    static_configs:
      - targets: ['german:8767']
```

Uses Docker service names — works on the internal network.

---

## Part 2 — Grafana

### Grafana container

Add to `docker-compose.prod.yml`:

```yaml
grafana:
  image: grafana/grafana:latest
  ports:
    - "127.0.0.1:3000:3000"
  volumes:
    - grafana_data:/var/lib/grafana
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    - GF_SERVER_ROOT_URL=https://grafana.minimoi.ai
  restart: unless-stopped

volumes:
  grafana_data:
```

Add `GRAFANA_PASSWORD` to SSM:
`/minimoi/production/grafana_password`

### Subdomain

Add `grafana.minimoi.ai` to Cloudflare DNS → EC2 Elastic IP.

Nginx config addition:

```nginx
server {
    server_name grafana.minimoi.ai;
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/minimoi.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/minimoi.ai/privkey.pem;
}
```

Run Certbot to add `grafana.minimoi.ai` to the SSL cert:
```bash
sudo certbot --nginx -d grafana.minimoi.ai
```

### Dashboards (Claude Code builds these)

**Dashboard 1 — Overview**
What Robert sees first when opening Grafana:
- All three apps: request rate, error rate, p95 response time
- Curator: last run time, articles scored
- German: active WebSocket connections, analysis jobs today
- Any active alerts

**Dashboard 2 — Gespräche Analysis**
- Analysis jobs per hour (time series)
- Success vs failure rate
- Duration percentiles by model (p50, p95, p99)
- Active WebSocket connections over time

**Dashboard 3 — Curator**
- Scoring loop duration over time
- Articles scored per run
- Error rate by source

**Dashboard 4 — Infrastructure**
- EC2 CPU and memory (from CoS health check metrics or Node Exporter)
- Disk usage over time
- Request volume across all apps

### Alert rules in Grafana

Six rules covering the most important thresholds:

```
Analysis success rate < 90% over 1 hour
Analysis p95 duration > 45 seconds
EC2 CPU > 80% for 10 minutes
Any app returning 0 successful requests for 5 minutes
Disk > 80%
WebSocket connections = 0 during active hours
```

Notification channel: email initially. Add Telegram webhook
(minimoi_system_bot) as a second channel once confirmed working.

---

## Commit sequence

```
1. Add prometheus-flask-exporter to all three requirements.txt
2. Add PrometheusMetrics init to portal, curator, german apps
3. Add custom metrics to Gespräche analysis pipeline and Curator loop
4. Add prometheus.yml to repo root
5. Add prometheus + grafana services to docker-compose.prod.yml
6. Add GRAFANA_PASSWORD to SSM
7. Add grafana.minimoi.ai to Cloudflare DNS + Nginx + Certbot
8. Build four Grafana dashboards
9. Configure six alert rules
```

---

## Definition of Done

- [ ] /metrics endpoint responding on all three Flask apps
- [ ] Prometheus scraping all three apps successfully
  (verify: `curl localhost:9090/api/v1/targets`)
- [ ] Grafana accessible at https://grafana.minimoi.ai
- [ ] Four dashboards rendering with real data
- [ ] Six alert rules configured
- [ ] Test alert: trigger a threshold, confirm notification
- [ ] GRAFANA_PASSWORD in SSM, not hardcoded
- [ ] grafana_data and prometheus_data volumes persisting
  (verify: restart containers, data survives)
- [ ] No regression on existing app performance

## Commit message

`Add Prometheus + Grafana metrics stack — four dashboards,
six alert rules, /metrics on all three Flask apps.`

---

*Spec · 2026-06-22 · Claude.ai*
*Extracted from: spec_monitoring_stack_2026-06-18.md*
*Sentry: absorbed into design log #100*
*Build after: spec_operations_monitoring_v2_2026-06-22.md Part 1*
*Register in design_log as: spec_ready*
