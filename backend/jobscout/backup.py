"""Weaviate index backup — export/import jobs **with their vectors**.

The whole point: ``include_vector=True`` pulls the already-computed vectors out of
Weaviate, so a restore writes them straight back with **zero embedding calls** (no
Gemini quota spent). It's a pure file download/upload, not a re-embed.

Export format: a gzipped JSON-Lines file. Line 1 is a header
``{"_header": {embed_backend, embed_model, dim, count, exported_at}}``; every
subsequent line is ``{"job": <Job.model_dump>, "vector": [floats]}``.

Restoring into an index whose vectors are a different dimension (i.e. a different
embedding model) is refused — you can't mix models in one collection.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobscout.config import settings
from jobscout.models import Job
from jobscout.store import COLLECTION_NAME, WeaviateStore, _props_to_job

DEFAULT_EXPORT_PATH = Path("data/weaviate_export.jsonl.gz")


def _extract_vector(obj: Any) -> list[float] | None:
    """Weaviate v4 returns vectors as ``{"default": [...]}`` when include_vector=True."""
    v = getattr(obj, "vector", None)
    if isinstance(v, dict):
        return v.get("default") or next(iter(v.values()), None)
    return v


def export_index(store: WeaviateStore, out_path: Path = DEFAULT_EXPORT_PATH) -> dict[str, Any]:
    """Stream every job + vector to ``out_path`` (gzipped JSONL). No embedding calls.

    Returns the header dict (embed_backend/model/dim/count/exported_at).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body_path = out_path.with_suffix(out_path.suffix + ".body.tmp")

    coll = store._client.collections.get(COLLECTION_NAME)
    count = 0
    dim: int | None = None
    try:
        with gzip.open(body_path, "wt", encoding="utf-8") as body:
            for obj in coll.iterator(include_vector=True):
                props = dict(obj.properties)
                vec = _extract_vector(obj)
                if dim is None and vec is not None:
                    dim = len(vec)
                job = _props_to_job(props, job_id=str(props.get("job_id", "")))
                body.write(json.dumps({"job": job.model_dump(mode="json"), "vector": vec}) + "\n")
                count += 1

        header = {
            "embed_backend": "gemini",
            "embed_model": settings.embed_model,
            "dim": dim,
            "count": count,
            "exported_at": datetime.now(UTC).isoformat(),
        }
        with gzip.open(out_path, "wt", encoding="utf-8") as out:
            out.write(json.dumps({"_header": header}) + "\n")
            with gzip.open(body_path, "rt", encoding="utf-8") as body:
                for line in body:
                    out.write(line)
    finally:
        if body_path.exists():
            body_path.unlink()
    return header


def read_header(in_path: Path = DEFAULT_EXPORT_PATH) -> dict[str, Any]:
    """Read just the header line of an export file."""
    with gzip.open(Path(in_path), "rt", encoding="utf-8") as f:
        return json.loads(f.readline())["_header"]


def _live_dim(store: WeaviateStore) -> int | None:
    """Vector dimension currently stored in the index, or None if empty."""
    coll = store._client.collections.get(COLLECTION_NAME)
    res = coll.query.fetch_objects(limit=1, include_vector=True)
    for obj in res.objects:
        v = _extract_vector(obj)
        if v is not None:
            return len(v)
    return None


def import_index(store: WeaviateStore, in_path: Path = DEFAULT_EXPORT_PATH) -> int:
    """Restore jobs + vectors from an export file. Zero embedding calls.

    Refuses if the target index already holds vectors of a different dimension
    (a different embedding model) — restoring there would corrupt search.
    Returns the number of jobs imported.
    """
    in_path = Path(in_path)
    header = read_header(in_path)
    file_dim = header.get("dim")
    live = _live_dim(store)
    if live is not None and file_dim is not None and live != file_dim:
        raise ValueError(
            f"Refusing import: target index vectors are {live}-dim but the export is "
            f"{file_dim}-dim ({header.get('embed_model')}). These are different embedding "
            f"models and cannot be mixed. Import into an empty/matching index, or re-embed."
        )

    n = 0
    with gzip.open(in_path, "rt", encoding="utf-8") as f:
        f.readline()  # skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            job = Job(**rec["job"])
            store.upsert(job, vector=rec.get("vector"))  # provided vector → no embedding
            n += 1
    return n
