#!/usr/bin/env bash
# check.sh — the one quality gate. Runs every linter/type-checker/test the project
# has, for both backend and frontend, and fails on the first problem. This is what a
# CI job (and a pre-push habit) should run. Everything here is already configured;
# nothing new is installed.
#
#   ./scripts/check.sh            # run all gates
#
# Requires: the backend virtualenv (.venv) and frontend node_modules installed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-.venv/bin/python}"

echo "== ruff =="
"$PY" -m ruff check backend/jobscout backend/tests

echo "== mypy =="
"$PY" -m mypy backend/jobscout

echo "== pytest =="       # from repo root so sources.yaml (CWD-relative) resolves
"$PY" -m pytest backend/tests -q

echo "== frontend: tsc + vite build =="
( cd frontend && npx tsc --noEmit && npx vite build )

echo "== all checks passed =="
