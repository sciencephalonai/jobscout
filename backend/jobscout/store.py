"""Weaviate v4 client — precomputed-vector (Vectorizer.none()) Job collection."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import weaviate
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.init import Auth
from weaviate.util import generate_uuid5

from jobscout.config import settings
from jobscout.models import Job

log = logging.getLogger(__name__)

COLLECTION_NAME = "Job"


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
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency or "",
        "url": job.url,
        "skills": job.skills or [],
        "enrichment_status": job.enrichment_status,
        "work_auth_required": job.work_auth_required or "",
        "restrictions": job.restrictions or "",
        "company_size_bucket": job.company_size_bucket or "",
        "employment_type": job.employment_type,
        "category": job.category,
        "locations": job.locations or [],
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
    return Job(
        job_id=resolved_id,
        source=props.get("source", ""),
        source_job_id=props.get("source_job_id") or None,
        title=props.get("title", ""),
        company=props.get("company") or None,
        location_raw=props.get("location_raw") or None,
        country=props.get("country") or None,
        city=props.get("city") or None,
        remote_mode=props.get("remote_mode", "unknown"),
        description=props.get("description") or None,
        url=props.get("url", ""),
        salary_min=props.get("salary_min"),
        salary_max=props.get("salary_max"),
        salary_currency=props.get("salary_currency") or None,
        posted_date=_parse_dt(props.get("posted_date")),
        posted_date_est=bool(props.get("posted_date_est", False)),
        ingested_at=_parse_dt(props.get("ingested_at")) or datetime.now(UTC),
        yoe_min=props.get("yoe_min"),
        yoe_max=props.get("yoe_max"),
        visa_sponsorship=props.get("visa_sponsorship", "not_mentioned"),
        work_auth_required=props.get("work_auth_required") or None,
        restrictions=props.get("restrictions") or None,
        company_size_bucket=props.get("company_size_bucket") or None,
        employment_type=props.get("employment_type") or "unknown",
        category=props.get("category") or "other",
        locations=props.get("locations") or [],
        skills=props.get("skills") or [],
        seniority=props.get("seniority", "unclear"),
        enrichment_status=props.get("enrichment_status", "pending"),
        raw_payload=None,
    )


class WeaviateStore:
    """Thin wrapper around the Weaviate v4 client focused on the Job collection."""

    def __init__(self, url: str = settings.weaviate_url) -> None:
        if settings.weaviate_cluster_url and settings.weaviate_api_key:
            # Weaviate Cloud (WCD). connect_to_weaviate_cloud accepts the REST
            # endpoint with or without a scheme and derives the gRPC endpoint.
            cluster_url = settings.weaviate_cluster_url
            if not cluster_url.startswith(("http://", "https://")):
                cluster_url = f"https://{cluster_url}"
            self._client = weaviate.connect_to_weaviate_cloud(
                cluster_url=cluster_url,
                auth_credentials=Auth.api_key(settings.weaviate_api_key),
                skip_init_checks=True,
            )
        else:
            parsed = urlparse(url)
            self._client = weaviate.connect_to_local(
                host=parsed.hostname or "localhost",
                port=parsed.port or 8080,
            )
        self._ensure_collection()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        """Create the Job collection, recreating it if the schema is stale.

        Categorical (enum) properties use Tokenization.FIELD so that exact-match
        filters on values like "no", "yes", "remote" are never stripped as BM25
        stopwords.  If the collection exists but was created with the old word
        tokenizer, it is dropped and recreated automatically — data is
        re-ingested on the next ingestion run.
        """
        if self._client.collections.exists(COLLECTION_NAME):
            # Check whether the existing collection has the correct tokenization.
            # If visa_sponsorship uses word tokenization (the old default), drop
            # and recreate so filters work correctly.
            try:
                col = self._client.collections.get(COLLECTION_NAME)
                props = {p.name: p for p in col.config.get().properties}
                vp = props.get("visa_sponsorship")
                if vp is not None and getattr(vp, "tokenization", None) != Tokenization.FIELD:
                    log.warning(
                        "Job collection has stale tokenization — recreating. "
                        "Data will be re-ingested on next run."
                    )
                    self._client.collections.delete(COLLECTION_NAME)
                else:
                    self._migrate_collection()
                    return
            except Exception:
                self._migrate_collection()
                return

        # Categorical enum properties use FIELD tokenization so that
        # short values ("no", "yes", "remote") are never dropped as stopwords.
        def _kw(name: str, **kw: object) -> Property:
            return Property(name=name, data_type=DataType.TEXT,
                            tokenization=Tokenization.FIELD, **kw)

        self._client.collections.create(
            name=COLLECTION_NAME,
            vectorizer_config=Configure.Vectorizer.none(),
            inverted_index_config=Configure.inverted_index(index_null_state=True),
            properties=[
                Property(name="job_id", data_type=DataType.TEXT,
                         tokenization=Tokenization.FIELD, skip_vectorization=True),
                Property(name="title", data_type=DataType.TEXT),
                Property(name="company", data_type=DataType.TEXT),
                Property(name="description", data_type=DataType.TEXT),
                Property(name="city", data_type=DataType.TEXT),
                Property(name="country", data_type=DataType.TEXT),
                _kw("remote_mode"),
                _kw("visa_sponsorship"),
                Property(name="yoe_min", data_type=DataType.INT),
                Property(name="yoe_max", data_type=DataType.INT),
                _kw("seniority"),
                _kw("source"),
                Property(name="posted_date", data_type=DataType.DATE),
                Property(name="posted_date_est", data_type=DataType.BOOL),
                Property(name="salary_min", data_type=DataType.NUMBER),
                Property(name="salary_max", data_type=DataType.NUMBER),
                _kw("salary_currency"),
                Property(name="url", data_type=DataType.TEXT,
                         tokenization=Tokenization.FIELD, skip_vectorization=True),
                Property(name="skills", data_type=DataType.TEXT_ARRAY),
                _kw("enrichment_status"),
                Property(name="work_auth_required", data_type=DataType.TEXT),
                Property(name="restrictions", data_type=DataType.TEXT),
                _kw("company_size_bucket"),
                _kw("employment_type"),
                _kw("category"),
                Property(name="locations", data_type=DataType.TEXT_ARRAY),
                Property(name="location_raw", data_type=DataType.TEXT),
                Property(name="ingested_at", data_type=DataType.DATE),
            ],
        )

    def _migrate_collection(self) -> None:
        """Add any properties missing from a previously-created collection.

        Weaviate supports adding properties to an existing collection without a
        rebuild, so this is non-destructive (existing objects keep their data).
        """
        collection = self._client.collections.get(COLLECTION_NAME)
        try:
            existing = {p.name for p in collection.config.get().properties}
        except Exception:
            return
        missing = [
            ("company_size_bucket", DataType.TEXT),
            ("employment_type", DataType.TEXT),
            ("locations", DataType.TEXT_ARRAY),
            ("category", DataType.TEXT),
        ]
        for prop_name, dtype in missing:
            if prop_name not in existing:
                try:
                    collection.config.add_property(
                        Property(name=prop_name, data_type=dtype)
                    )
                    log.info("migrated Job collection: added %s", prop_name)
                except Exception:
                    log.warning("could not add %s property", prop_name, exc_info=True)

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

    def upsert(self, job: Job, vector: list[float] | None = None) -> None:
        """Insert or update a single job.

        If an object with the same deterministic UUID already exists it is
        fully replaced (all properties + vector updated).  Otherwise a new
        object is inserted.
        """
        uid = _job_uuid(job.job_id)
        collection = self._client.collections.get(COLLECTION_NAME)
        props = _job_to_props(job)

        try:
            existing = collection.query.fetch_object_by_id(uid)
        except Exception:
            existing = None

        if existing is not None:
            # Replace all properties in-place
            collection.data.replace(
                uuid=uid,
                properties=props,
                vector=vector,
            )
        else:
            collection.data.insert(
                properties=props,
                uuid=uid,
                vector=vector,
            )

    def upsert_many(self, jobs: list[tuple[Job, list[float] | None]]) -> None:
        """Batch upsert a list of (Job, vector) pairs.

        Uses ``collection.batch.dynamic()`` for throughput; Weaviate handles
        automatic flushing and error collection.
        """
        collection = self._client.collections.get(COLLECTION_NAME)
        with collection.batch.dynamic() as batch:
            for job, vector in jobs:
                uid = _job_uuid(job.job_id)
                props = _job_to_props(job)
                batch.add_object(
                    properties=props,
                    uuid=uid,
                    vector=vector,
                )

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

    def purge_older_than(self, cutoff: datetime) -> int:
        """Delete all jobs whose posted_date (or ingested_at when date is unknown) is before *cutoff*.

        Returns the number of objects deleted.
        """
        from weaviate.classes.query import Filter

        collection = self._client.collections.get(COLLECTION_NAME)
        deleted = 0

        # 1. Jobs with a known posted_date that is too old.
        result = collection.data.delete_many(
            where=Filter.by_property("posted_date").less_than(cutoff)
        )
        deleted += result.successful if result.successful else 0

        # 2. Jobs with no posted_date but ingested more than a month ago.
        result2 = collection.data.delete_many(
            where=(
                Filter.by_property("posted_date").is_none(True)
                & Filter.by_property("ingested_at").less_than(cutoff)
            )
        )
        deleted += result2.successful if result2.successful else 0

        return deleted

    def close(self) -> None:
        self._client.close()

    # Context manager support so callers can use `with WeaviateStore() as store:`
    def __enter__(self) -> WeaviateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
