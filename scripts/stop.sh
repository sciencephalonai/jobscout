#!/usr/bin/env bash
# stop.sh — stop the JobScout backend and frontend started by scripts/run.sh.
# Kills via the recorded PID files when present, then falls back to pkill on
# the uvicorn and vite command lines. Safe to run repeatedly.
set -uo pipefail

API_PID_FILE="/tmp/jobscout_api.pid"
FE_PID_FILE="/tmp/jobscout_fe.pid"

stopped_any=0

kill_pidfile() {
  local label="$1" file="$2"
  if [[ -f "${file}" ]]; then
    local pid
    pid="$(cat "${file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      echo "Stopped ${label} (PID ${pid})."
      stopped_any=1
    else
      echo "${label}: no live process for PID '${pid:-<empty>}' (stale PID file)."
    fi
    rm -f "${file}" || true
  else
    echo "${label}: no PID file (${file})."
  fi
}

kill_pidfile "backend" "${API_PID_FILE}"
kill_pidfile "frontend" "${FE_PID_FILE}"

# --- Fallback: catch anything the PID files missed (orphans/double-starts) ---
if pkill -f "uvicorn backend.jobscout" 2>/dev/null; then
  echo "Stopped stray 'uvicorn backend.jobscout' process(es) via pkill."
  stopped_any=1
fi
if pkill -f "vite" 2>/dev/null; then
  echo "Stopped stray 'vite' process(es) via pkill."
  stopped_any=1
fi

if [[ "${stopped_any}" -eq 0 ]]; then
  echo "Nothing to stop — JobScout does not appear to be running."
fi
