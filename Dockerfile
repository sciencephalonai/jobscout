# Backend container for JobScout (API only; Weaviate runs via docker-compose, the
# frontend builds to static assets separately). A reproducible run + a deploy target.
# The relational store (DuckDB) and resume files are local by default — mount a volume
# for /app/data + the DuckDB file, or switch relational_backend/blob_backend before
# a real multi-instance deployment (see docs/pre-deployment-checklist.md).
FROM python:3.11-slim

WORKDIR /app

# Install the package (setuptools finds `jobscout` under backend/, per pyproject).
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .

# Runtime config read CWD-relative (the company registry sync). No secrets/PII copied;
# provide .env + data via env vars / a mounted volume at run time.
COPY sources.yaml sources.discovered.yaml ./
COPY data/company_targets.yaml ./data/company_targets.yaml

EXPOSE 8000
CMD ["uvicorn", "jobscout.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
