#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <service-dir> <module-path>"
  exit 1
fi

service_dir="$1"
module_path="$2"
service_host="${SERVICE_HOST:-0.0.0.0}"
service_port="${SERVICE_PORT:-8000}"

export PYTHONPATH="packages/shared-kernel/src:packages/contracts/src:packages/observability/src:packages/security/src:services/${service_dir}/src"
uvicorn "${module_path}:app" --host "${service_host}" --port "${service_port}" --reload
