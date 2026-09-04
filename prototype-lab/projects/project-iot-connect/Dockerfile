# IoT Connect standalone beta — one image for the application and the
# synthetic integration service. The Compose file selects the command.
#
# Base image pinned by digest so the Mac and AWS builds use the same software.
# Human tag: python:3.12-slim (linux/arm64 + linux/amd64 manifest list), resolved
# by BuildKit on 2026-09-03 to the digest below.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/iot-connect

# requirements.lock = exact versions that passed verification (installed here);
# requirements.txt = human-edited ranges (checked against the lock by tests).
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock && pip check

COPY . .

RUN useradd --system --uid 10001 --create-home iotconnect \
    && chown -R iotconnect:iotconnect /srv/iot-connect
USER iotconnect

EXPOSE 8095 8096

# Default command: bootstrap the database, then serve IoT Connect.
CMD ["sh", "scripts/start-app.sh"]
