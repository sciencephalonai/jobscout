#!/usr/bin/env bash
# health.sh — quick health check of the JobScout backend (:8000) and
# frontend (:5173). Prints an HTTP code + OK/DOWN line for each endpoint.
# Exits non-zero if the backend is down.
set -uo pipefail

API_OPENAPI="http://127.0.0.1:8000/openapi.json"
API_STATS="http://127.0.0.1:8000/api/stats"
FE_URL="http://127.0.0.1:5173/"

code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo "000"
}

api_openapi_code="$(code "${API_OPENAPI}")"
api_stats_code="$(code "${API_STATS}")"
fe_code="$(code "${FE_URL}")"

status() { [[ "$1" == "200" ]] && echo "OK" || echo "DOWN"; }

echo "Backend /openapi.json : ${api_openapi_code} $(status "${api_openapi_code}")"
echo "Backend /api/stats    : ${api_stats_code} $(status "${api_stats_code}")"
echo "Frontend (5173)       : ${fe_code} $(status "${fe_code}")"

if [[ "${api_openapi_code}" == "200" ]]; then
  # Print a one-line stats summary when the backend is up.
  curl -s --max-time 5 "${API_STATS}" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"  -> total_jobs={d.get('total_jobs')} by_source={d.get('by_source')}\")" 2>/dev/null || true
  echo "Backend: OK"
  exit 0
else
  echo "Backend: DOWN" >&2
  exit 1
fi
