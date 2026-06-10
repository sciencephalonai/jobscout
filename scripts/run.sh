#!/usr/bin/env bash
# run.sh — start JobScout backend (uvicorn :8000) and frontend (Vite :5173)
# for local development. Clears any stale uvicorn first to avoid the DuckDB
# single-writer lock crash. Backend logs -> /tmp/jobscout_api.log,
# frontend logs -> /tmp/jobscout_fe.log. Stop with scripts/stop.sh.
set -euo pipefail

# --- Resolve repo root from this script's own location and cd there ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- Prefer the project's isolated virtualenv if present ---
# Keeps the backend off any shared (e.g. anaconda) interpreter. Falls back to
# whatever `uvicorn` is on PATH when no .venv exists.
if [[ -x "${REPO_ROOT}/.venv/bin/uvicorn" ]]; then
  UVICORN="${REPO_ROOT}/.venv/bin/uvicorn"
  echo "Using project virtualenv: ${REPO_ROOT}/.venv"
else
  UVICORN="uvicorn"
  echo "No .venv found — using 'uvicorn' from PATH ($(command -v uvicorn 2>/dev/null || echo 'NOT FOUND'))."
  echo "  Tip: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
fi

# Backend port — defaults to 8001 (8000 is often taken by another local app);
# override with JOBSCOUT_API_PORT. Keep the Vite proxy in frontend/vite.config.ts
# pointed at the same port.
API_PORT="${JOBSCOUT_API_PORT:-8001}"
API_LOG="/tmp/jobscout_api.log"
FE_LOG="/tmp/jobscout_fe.log"
API_PID_FILE="/tmp/jobscout_api.pid"
FE_PID_FILE="/tmp/jobscout_fe.pid"
API_URL="http://127.0.0.1:${API_PORT}/openapi.json"

# --- PREFLIGHT: .env presence and key sanity (warn, don't hard-fail) ---
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "ERROR: .env not found at ${REPO_ROOT}/.env (copy .env.example and fill keys)." >&2
  exit 1
fi

check_key() {
  local key="$1"
  # Match KEY=, KEY="", KEY=, or commented/missing -> warn.
  local val
  val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${REPO_ROOT}/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  # Strip surrounding quotes and whitespace.
  val="$(printf '%s' "${val}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  if [[ -z "${val}" ]]; then
    echo "WARNING: ${key} appears unset/empty in .env — related features may not work." >&2
  fi
}
check_key "GOOGLE_API_KEY"
check_key "DEEPSEEK_API_KEY"
check_key "ADZUNA_APP_ID"

# --- Clear stale backend processes to release the DuckDB write lock ---
echo "Clearing any stale 'uvicorn backend.jobscout' processes..."
pkill -f "uvicorn backend.jobscout" || true
sleep 1

# --- Start the backend (must run from repo root: loads sources.yaml etc.) ---
echo "Starting backend (uvicorn) -> ${API_LOG}"
: > "${API_LOG}"
"${UVICORN}" backend.jobscout.api.main:app --host 127.0.0.1 --port "${API_PORT}" \
  >> "${API_LOG}" 2>&1 &
API_PID=$!
echo "${API_PID}" > "${API_PID_FILE}"

# --- Poll until the backend answers 200 on /openapi.json (timeout ~20s) ---
echo -n "Waiting for backend on ${API_URL} "
backend_up=0
for _ in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "${API_URL}" 2>/dev/null || true)"
  if [[ "${code}" == "200" ]]; then
    backend_up=1
    echo "OK"
    break
  fi
  # Bail early if the backend process already died.
  if ! kill -0 "${API_PID}" 2>/dev/null; then
    echo "FAILED (process exited)"
    break
  fi
  echo -n "."
  sleep 0.5
done

if [[ "${backend_up}" -ne 1 ]]; then
  echo "ERROR: backend did not become healthy within ~20s." >&2
  echo "----- last 20 lines of ${API_LOG} -----" >&2
  tail -n 20 "${API_LOG}" >&2 || true
  echo "---------------------------------------" >&2
  exit 1
fi

# --- Start the frontend (Vite dev server, proxies /api -> :8000) ---
echo "Starting frontend (npm run dev) -> ${FE_LOG}"
: > "${FE_LOG}"
(
  cd "${REPO_ROOT}/frontend" && npm run dev
) >> "${FE_LOG}" 2>&1 &
FE_PID=$!
echo "${FE_PID}" > "${FE_PID_FILE}"

# --- Summary ---
cat <<EOF

JobScout is starting up.

  Frontend : http://localhost:5173
  API docs : http://localhost:${API_PORT}/docs

  Backend log  : ${API_LOG}  (PID $(cat "${API_PID_FILE}" 2>/dev/null || echo '?'))
  Frontend log : ${FE_LOG}  (PID $(cat "${FE_PID_FILE}" 2>/dev/null || echo '?'))

  Check health : scripts/health.sh
  Stop both    : scripts/stop.sh

Note: the frontend may take a few seconds more to compile — tail ${FE_LOG} if needed.
EOF
