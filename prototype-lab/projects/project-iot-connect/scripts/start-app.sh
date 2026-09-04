#!/bin/sh
# Container entrypoint for the IoT Connect application service.
# 1. wait for PostgreSQL, apply the idempotent schema, seed an empty database;
# 2. serve the application.
set -eu
python scripts/bootstrap_database.py
exec uvicorn app.main:app --host 0.0.0.0 --port "${IOTCONNECT_APP_PORT_INTERNAL:-8095}"
