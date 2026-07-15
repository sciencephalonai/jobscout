"""Weaviate v4 client — precomputed-vector (Vectorizer.none()) Job collection."""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import weaviate
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.init import AdditionalConfig, Auth, Timeout
from weaviate.util import generate_uuid5

from jobscout.config import settings
from jobscout.enrich import detect_recruiter_post
from jobscout.models import Job
from jobscout.normalize import detect_new_grad_program

log = logging.getLogger(__name__)

COLLECTION_NAME = "Job"

# Boot resilience: a transient/slow Weaviate connection must not kill startup.
# We skip the connect-time health check (queries still surface per-request errors)
# and retry a few times before raising the friendly error.
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_S = 2.0
_INIT_TIMEOUT_S = 30


def _job_uuid(job_id: str) -> str:
    """Deterministic Weaviate UUID derived from the 16-char hex dedup hash."""
    return str(generate_uuid5(job_id))


def _job_to_props(job: Job) -> dict:
    """Serialise a Job to a flat dict of Weaviate property values."""

    def _dt_or_none(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()

    return {
        "job_id": job.job_id,
        "title": job.title,
        "company": job.company or "",
        "description": job.description or "",
        "city": job.city or "",
        "country": job.country or "",
        "remote_mode": job.remote_mode,
        "visa_sponsorship": job.visa_sponsorship,
        "yoe_min": job.yoe_min,
        "yoe_max": job.yoe_max,
        "seniority": job.seniority,
        "source": job.source,
        "posted_date": _dt_or_none(job.posted_date),
        "posted_date_est": job.posted_date_est,
        "is_active": job.is_active,
        "last_seen_at": _dt_or_none(job.last_seen_at),
        "closed_at": _dt_or_none(job.closed_at),
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency or "",
        "url": job.url,
        "skills": job.skills or [],
        "enrichment_status": job.enrichment_status,
        "work_auth_required": job.work_auth_required or "",
        "restrictions": job.restrictions or "",
        "company_size_bucket": job.company_size_bucket or "",
        "security_clearance": job.security_clearance,
        "citizenship_required": job.citizenship_required,
        "eligibility_evidence": job.eligibility_evidence or [],
        "employer_type": job.employer_type,
        "cap_exempt": job.cap_exempt,
        "known_h1b_sponsor": job.known_h1b_sponsor,
        "known_everify": job.known_everify,
        "is_recruiter_post": job.is_recruiter_post,
        "new_grad_program": job.new_grad_program,
        "category": job.category,
        "employment_type": job.employment_type,
        "location_raw": job.location_raw or "",
        "ingested_at": _dt_or_none(job.ingested_at),
    }


def _props_to_job(props: dict, job_id: str | None = None) -> Job:
    """Reconstruct a Job from a Weaviate property dict."""

    def _parse_dt(v: str | datetime | None) -> datetime | None:
        if not v:
            return None
        # The Weaviate v4 client deserializes DATE properties to timezone-aware
        # datetime objects; only fall back to ISO-string parsing for safety.
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=UTC)
        try:
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None

    resolved_id: str = job_id or props.get("job_id", "")
    source = props.get("source", "")
    title = props.get("title", "")
    company = props.get("company") or None
    description = props.get("description") or None
    return Job(
        job_id=resolved_id,
        source=source,
        source_job_id=props.get("source_job_id") or None,
        title=title,
        company=company,
        location_raw=props.get("location_raw") or None,
        country=props.get("country") or None,
        city=props.get("city") or None,
        remote_mode=props.get("remote_mode", "unknown"),
        description=description,
        url=props.get("url", ""),
        salary_min=props.get("salary_min"),
        salary_max=props.get("salary_max"),
        salary_currency=props.get("salary_currency") or None,
        posted_date=_parse_dt(props.get("posted_date")),
        posted_date_est=bool(props.get("posted_date_est", False)),
        ingested_at=_parse_dt(props.get("ingested_at")) or datetime.now(UTC),
        is_active=props.get("is_active") is not False,
        last_seen_at=_parse_dt(props.get("last_seen_at")),
        closed_at=_parse_dt(props.get("closed_at")),
        yoe_min=props.get("yoe_min"),
        yoe_max=props.get("yoe_max"),
        visa_sponsorship=props.get("visa_sponsorship", "not_mentioned"),
        work_auth_required=props.get("work_auth_required") or None,
        restrictions=props.get("restrictions") or None,
        company_size_bucket=props.get("company_size_bucket") or None,
        skills=props.get("skills") or [],
        seniority=props.get("seniority", "unclear"),
        # Use `or default` (not .get(k, default)) because Weaviate returns
        # objects migrated before these properties existed with the key PRESENT
        # and value None — .get's default would not apply and Pydantic (which
        # requires a Literal, not None) would reject the record.
        security_clearance=props.get("security_clearance") or "unclear",
        citizenship_required=bool(props.get("citizenship_required") or False),
        eligibility_evidence=props.get("eligibility_evidence") or [],
        employer_type=props.get("employer_type") or "unclear",
        cap_exempt=props.get("cap_exempt") or "unknown",
        known_h1b_sponsor=bool(props.get("known_h1b_sponsor") or False),
        known_everify=bool(props.get("known_everify") or False),
        # Re-evaluate this derived heuristic on read so records written by an
        # older, over-broad detector do not keep showing direct employers as
        # recruiters until an expensive re-ingestion/backfill occurs.
        is_recruiter_post=(
            detect_recruiter_post(company, source, description)
            if company
            else bool(props.get("is_recruiter_post") or False)
        ),
        # Like recruiter status, this deterministic field evolves as real ATS
        # formats are discovered. Re-evaluate on read so legacy rows with an
        # explicit deep ``Employee Type: New College Grad`` marker immediately
        # participate in junior-profile matching without a destructive backfill.
        new_grad_program=(
            bool(props.get("new_grad_program") or False)
            or detect_new_grad_program(title, description)
        ),
        category=props.get("category") or "other",
        employment_type=props.get("employment_type") or "full_time",
        enrichment_status=props.get("enrichment_status", "pending"),
        raw_payload=None,
    )


class WeaviateStore:
    """Thin wrapper around the Weaviate v4 client focused on the Job collection."""

    def __init__(self, url: str = settings.weaviate_url) -> None:
        self._url = url
        have_cloud = bool(settings.weaviate_cluster_url and settings.weaviate_api_key)
        mode = (settings.storage_mode or "both").lower()

        # Primary store: cloud only when explicitly requested AND creds present;
        # otherwise local — the always-available canonical store. A requested but
        # unreachable cloud auto-degrades to local (never a fatal startup).
        primary = "cloud" if (mode == "cloud" and have_cloud) else "local"
        try:
            self._client = self._connect(primary, retries=_CONNECT_RETRIES)
        except Exception as exc:  # noqa: BLE001
            if primary == "cloud":
                log.warning("cloud primary unreachable (%s) — degrading to local", exc)
                self._client = self._connect("local", retries=_CONNECT_RETRIES)
                primary = "local"
            else:
                raise
        self.primary_target = primary
        self._ensure_collection(self._client)

        # Optional cloud mirror for storage_mode="both": best-effort dual-write so
        # switching backends never loses data. A dead/asleep cloud is logged and
        # skipped — it never blocks local ingest.
        self._mirror: Any | None = None
        self.mirror_target: str | None = None
        if mode == "both" and have_cloud and primary == "local":
            try:
                self._mirror = self._connect("cloud", retries=1)
                self._ensure_collection(self._mirror)
                self.mirror_target = "cloud"
            except Exception as exc:  # noqa: BLE001
                log.warning("cloud mirror unavailable (%s) — writing local only", exc)
                self._mirror = None

    def _connect(self, target: str, retries: int) -> Any:
        """Open one Weaviate client (``target`` = ``"local"`` | ``"cloud"``).

        Retries transient failures and touches the server so a dead endpoint (e.g.
        an asleep cloud returning 301) surfaces here. Raises a friendly
        RuntimeError once retries are exhausted.
        """
        cfg = AdditionalConfig(timeout=Timeout(init=_INIT_TIMEOUT_S))
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            client = None
            try:
                if target == "cloud":
                    cluster_url = settings.weaviate_cluster_url
                    if not cluster_url.startswith(("http://", "https://")):
                        cluster_url = f"https://{cluster_url}"
                    client = weaviate.connect_to_weaviate_cloud(
                        cluster_url=cluster_url,
                        auth_credentials=Auth.api_key(settings.weaviate_api_key),
                        skip_init_checks=True,
                        additional_config=cfg,
                    )
                else:
                    parsed = urlparse(self._url)
                    client = weaviate.connect_to_local(
                        host=parsed.hostname or "localhost",
                        port=parsed.port or 8080,
                        skip_init_checks=True,
                        additional_config=cfg,
                    )
                client.collections.exists(COLLECTION_NAME)  # surface dead endpoints now
                return client
            except Exception as exc:  # noqa: BLE001 — retry transient failures
                last_exc = exc
                try:
                    if client is not None:
                        client.close()
                except Exception:  # noqa: BLE001
                    pass
                if attempt < retries:
                    log.warning(
                        "weaviate_connect_retry target=%s attempt=%s/%s err=%s",
                        target, attempt, retries, exc,
                    )
                    time.sleep(_CONNECT_BACKOFF_S)
        where = settings.weaviate_cluster_url if target == "cloud" else self._url
        hint = (
            "Check WEAVIATE_CLUSTER_URL + WEAVIATE_API_KEY in .env and that the cluster is awake."
            if target == "cloud" else
            "Start it with `docker-compose up -d`, or set WEAVIATE_CLUSTER_URL + WEAVIATE_API_KEY to use cloud."
        )
        raise RuntimeError(
            f"Could not connect to Weaviate at {where} after {retries} attempts. "
            f"{hint} (original error: {type(last_exc).__name__}: {last_exc})"
        ) from last_exc

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _ensure_collection(self, client: Any) -> None:
        """Create the Job collection on *client* if it doesn't already exist."""
        if client.collections.exists(COLLECTION_NAME):
            self._migrate_collection(client)
            return

        client.collections.create(
            name=COLLECTION_NAME,
            vectorizer_config=Configure.Vectorizer.none(),
            # index_null_state lets filters use is_none() — build_filters() emits
            # `yoe_min IS NULL OR yoe_min <= X` so jobs with unknown YoE are kept.
            inverted_index_config=Configure.inverted_index(index_null_state=True),
            properties=[
                Property(
                    name="job_id",
                    data_type=DataType.TEXT,
                    skip_vectorization=True,
                ),
                Property(name="title", data_type=DataType.TEXT),
                Property(name="company", data_type=DataType.TEXT),
                Property(name="description", data_type=DataType.TEXT),
                Property(name="city", data_type=DataType.TEXT),
                Property(name="country", data_type=DataType.TEXT),
                Property(name="remote_mode", data_type=DataType.TEXT),
                Property(name="visa_sponsorship", data_type=DataType.TEXT),
                Property(name="yoe_min", data_type=DataType.INT),
                Property(name="yoe_max", data_type=DataType.INT),
                Property(name="seniority", data_type=DataType.TEXT),
                Property(name="source", data_type=DataType.TEXT),
                Property(name="posted_date", data_type=DataType.DATE),
                Property(name="posted_date_est", data_type=DataType.BOOL),
                Property(name="is_active", data_type=DataType.BOOL),
                Property(name="last_seen_at", data_type=DataType.DATE),
                Property(name="closed_at", data_type=DataType.DATE),
                Property(name="salary_min", data_type=DataType.NUMBER),
                Property(name="salary_max", data_type=DataType.NUMBER),
                Property(name="salary_currency", data_type=DataType.TEXT),
                Property(
                    name="url",
                    data_type=DataType.TEXT,
                    skip_vectorization=True,
                ),
                Property(name="skills", data_type=DataType.TEXT_ARRAY),
                Property(name="enrichment_status", data_type=DataType.TEXT),
                Property(name="work_auth_required", data_type=DataType.TEXT),
                Property(name="restrictions", data_type=DataType.TEXT),
                Property(name="company_size_bucket", data_type=DataType.TEXT),
                Property(name="security_clearance", data_type=DataType.TEXT),
                Property(name="citizenship_required", data_type=DataType.BOOL),
                Property(name="eligibility_evidence", data_type=DataType.TEXT_ARRAY),
                Property(name="employer_type", data_type=DataType.TEXT),
                Property(name="cap_exempt", data_type=DataType.TEXT),
                Property(name="known_h1b_sponsor", data_type=DataType.BOOL),
                Property(name="known_everify", data_type=DataType.BOOL),
                Property(name="is_recruiter_post", data_type=DataType.BOOL),
                Property(name="new_grad_program", data_type=DataType.BOOL),
                Property(name="category", data_type=DataType.TEXT,
                         tokenization=Tokenization.FIELD),
                Property(name="employment_type", data_type=DataType.TEXT,
                         tokenization=Tokenization.FIELD),
                Property(name="location_raw", data_type=DataType.TEXT),
                Property(name="ingested_at", data_type=DataType.DATE),
            ],
        )

    def _migrate_collection(self, client: Any) -> None:
        """Add any properties missing from a previously-created collection.

        Weaviate supports adding properties to an existing collection without a
        rebuild, so this is non-destructive (existing objects keep their data).
        """
        collection = client.collections.get(COLLECTION_NAME)
        try:
            existing = {p.name for p in collection.config.get().properties}
        except Exception:
            return
        # Properties that may be missing from an older collection. Adding a
        # property to an existing collection is non-destructive in Weaviate.
        _MIGRATIONS: list[tuple[str, DataType]] = [
            ("company_size_bucket", DataType.TEXT),
            ("security_clearance", DataType.TEXT),
            ("citizenship_required", DataType.BOOL),
            ("eligibility_evidence", DataType.TEXT_ARRAY),
            ("is_active", DataType.BOOL),
            ("last_seen_at", DataType.DATE),
            ("closed_at", DataType.DATE),
            ("employer_type", DataType.TEXT),
            ("cap_exempt", DataType.TEXT),
            ("known_h1b_sponsor", DataType.BOOL),
            ("known_everify", DataType.BOOL),
            ("is_recruiter_post", DataType.BOOL),
            ("category", DataType.TEXT),
            ("employment_type", DataType.TEXT),
            ("new_grad_program", DataType.BOOL),
        ]
        for name, data_type in _MIGRATIONS:
            if name in existing:
                continue
            try:
                collection.config.add_property(Property(name=name, data_type=data_type))
                log.info("migrated Job collection: added %s", name)
            except Exception:
                log.warning("could not add %s property", name, exc_info=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def update_fields(self, job_id: str, fields: dict) -> None:
        """Partially update an existing job's properties, preserving its vector.

        Used by the enrichment worker — unlike ``upsert``/``replace`` this does
        NOT touch the stored embedding.
        """
        collection = self._client.collections.get(COLLECTION_NAME)
        collection.data.update(uuid=_job_uuid(job_id), properties=fields)

    @staticmethod
    def _write_one(client: Any, uid: Any, props: dict, vector: list[float] | None) -> None:
        """Insert-or-replace one object on *client* (same deterministic UUID)."""
        collection = client.collections.get(COLLECTION_NAME)
        try:
            existing = collection.query.fetch_object_by_id(uid)
        except Exception:
            existing = None
        if existing is not None:
            collection.data.replace(uuid=uid, properties=props, vector=vector)
        else:
            collection.data.insert(properties=props, uuid=uid, vector=vector)

    def upsert(self, job: Job, vector: list[float] | None = None) -> None:
        """Insert or update a single job on the primary store, then best-effort on
        the cloud mirror (storage_mode="both") so a backend switch never loses data."""
        uid = _job_uuid(job.job_id)
        props = _job_to_props(job)
        self._write_one(self._client, uid, props, vector)
        if self._mirror is not None:
            try:
                self._write_one(self._mirror, uid, props, vector)
            except Exception as exc:  # noqa: BLE001 — mirror is best-effort
                log.warning("cloud mirror upsert failed for %s: %s", job.job_id, exc)

    @staticmethod
    def _batch_write(client: Any, jobs: list[tuple[Job, list[float] | None]]) -> None:
        collection = client.collections.get(COLLECTION_NAME)
        with collection.batch.dynamic() as batch:
            for job, vector in jobs:
                batch.add_object(
                    properties=_job_to_props(job),
                    uuid=_job_uuid(job.job_id),
                    vector=vector,
                )

    def upsert_many(self, jobs: list[tuple[Job, list[float] | None]]) -> None:
        """Batch upsert (Job, vector) pairs to the primary, then best-effort to the
        cloud mirror. Weaviate handles automatic flushing + error collection."""
        self._batch_write(self._client, jobs)
        if self._mirror is not None:
            try:
                self._batch_write(self._mirror, jobs)
            except Exception as exc:  # noqa: BLE001 — mirror is best-effort
                log.warning("cloud mirror batch upsert failed: %s", exc)

    def search_near_vector(
        self,
        vector: list[float],
        filters: Any | None = None,
        limit: int = 5,
    ) -> list[Job]:
        """Pure vector nearest-neighbour search (used by resume matching).

        Embeds nothing itself — the caller passes a query vector (e.g. an
        embedded resume). The same metadata ``filters`` used for keyword search
        apply here, so resume matching honours the same eligibility cuts.
        """
        collection = self._client.collections.get(COLLECTION_NAME)
        response = collection.query.near_vector(
            near_vector=vector,
            limit=limit,
            filters=filters,
        )
        jobs: list[Job] = []
        for obj in response.objects:
            props = dict(obj.properties)
            jobs.append(_props_to_job(props, job_id=str(props.get("job_id", ""))))
        return jobs

    def near_vector_scores(
        self,
        vector: list[float],
        filters: Any | None = None,
        limit: int = 500,
    ) -> dict[str, float]:
        """Return {job_id: similarity 0–1} for the nearest jobs to *vector*.

        Used to blend semantic resume↔job similarity into the match score. Cosine
        distance d∈[0,2] → similarity = 1 - d/2. Jobs beyond ``limit`` get no entry
        (the caller treats missing as 'no semantic signal').
        """
        from weaviate.classes.query import MetadataQuery

        collection = self._client.collections.get(COLLECTION_NAME)
        response = collection.query.near_vector(
            near_vector=vector,
            limit=limit,
            filters=filters,
            return_metadata=MetadataQuery(distance=True),
        )
        scores: dict[str, float] = {}
        for obj in response.objects:
            jid = str(dict(obj.properties).get("job_id", ""))
            if not jid:
                continue
            dist = getattr(obj.metadata, "distance", None)
            scores[jid] = max(0.0, min(1.0, 1.0 - dist / 2)) if dist is not None else 0.5
        return scores

    def get_by_id(self, job_id: str) -> Job | None:
        """Fetch a single Job by its 16-char dedup hash. Returns None if not found."""
        uid = _job_uuid(job_id)
        collection = self._client.collections.get(COLLECTION_NAME)
        try:
            obj = collection.query.fetch_object_by_id(uid)
        except Exception:
            return None
        if obj is None:
            return None
        return _props_to_job(dict(obj.properties), job_id=job_id)

    def find_by_url(self, url: str) -> Job | None:
        """Return the job with this exact apply URL, if the index has it."""
        from weaviate.classes.query import Filter

        collection = self._client.collections.get(COLLECTION_NAME)
        try:
            res = collection.query.fetch_objects(
                filters=Filter.by_property("url").equal(url), limit=1
            )
        except Exception:  # noqa: BLE001
            return None
        if not res.objects:
            return None
        props = dict(res.objects[0].properties)
        return _props_to_job(props, job_id=props.get("job_id", ""))

    def purge_older_than(self, cutoff: datetime) -> int:
        """Delete jobs whose ``posted_date`` (or ``ingested_at`` when date is unknown)
        is before *cutoff*. Returns the number of objects deleted. Explicit cleanup
        only — never called automatically."""
        from weaviate.classes.query import Filter

        collection = self._client.collections.get(COLLECTION_NAME)
        deleted = 0
        r1 = collection.data.delete_many(
            where=Filter.by_property("posted_date").less_than(cutoff)
        )
        deleted += r1.successful or 0
        r2 = collection.data.delete_many(
            where=(
                Filter.by_property("posted_date").is_none(True)
                & Filter.by_property("ingested_at").less_than(cutoff)
            )
        )
        deleted += r2.successful or 0
        return deleted

    def backend_status(self) -> dict:
        """Report the active vector-store backends (for the Settings panel)."""
        return {
            "primary": self.primary_target,
            "mirror": self.mirror_target,                 # "cloud" or None
            "dual_write": self._mirror is not None,
        }

    def close(self) -> None:
        self._client.close()
        if self._mirror is not None:
            with contextlib.suppress(Exception):
                self._mirror.close()

    # Context manager support so callers can use `with WeaviateStore() as store:`
    def __enter__(self) -> WeaviateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
