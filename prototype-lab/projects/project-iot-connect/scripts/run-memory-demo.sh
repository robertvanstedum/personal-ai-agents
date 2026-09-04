#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "${script_dir}/.." && pwd)"

cd "${project_dir}"

if [[ ! -x ".venv/bin/uvicorn" ]]; then
  echo "IoT Connect virtual environment is missing."
  echo "Create it and install requirements before starting the demo."
  exit 1
fi

export IOTCONNECT_STORE=memory

echo "Starting IoT Connect in portable memory mode (no Docker, no persistence)."
echo "For the complete persistent stack use: make up"
echo "Admin UI: http://127.0.0.1:8095/admin"
echo "Swagger:  http://127.0.0.1:8095/docs"
echo "Stop with Control-C."

exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8095
