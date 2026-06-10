"""Tests for jobscout.normalize — normalization and raw_to_job conversion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobscout.normalize import (
    compute_job_id,
    normalize_remote,
    normalize_text,
    parse_posted_date,
    raw_to_job,
)

# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_lowercases_input(self):
        assert normalize_text("Software Engineer") == "software engineer"

    def test_strips_punctuation(self):
        # Commas, dots, hyphens, apostrophes should all be removed
        assert normalize_text("Sr. Engineer, LLC.") == "sr engineer"

    def test_removes_inc_suffix(self):
        assert normalize_text("Acme Inc") == "acme"

    def test_removes_llc_suffix(self):
        assert normalize_text("Startup LLC") == "startup"

    def test_removes_ltd_suffix(self):
        assert normalize_text("BritishCo Ltd") == "britishco"

    def test_removes_corp_suffix(self):
        assert normalize_text("MegaCorp Corp") == "megacorp"

    def test_collapses_whitespace(self):
        assert normalize_text("  too   many   spaces  ") == "too many spaces"

    def test_empty_string_returns_empty(self):
        assert normalize_text("") == ""

    def test_removes_stacked_suffixes(self):
        # "Acme Corp LLC" — both Corp and LLC should be stripped
        result = normalize_text("Acme Corp LLC")
        assert "corp" not in result
        assert "llc" not in result
        assert "acme" in result

    def test_unicode_letters_preserved(self):
        # Letters outside ASCII should be kept; only punctuation stripped
        result = normalize_text("Ñoño S.A.")
        assert "ñoño" in result

    def test_digits_preserved(self):
        assert normalize_text("Company123 Inc") == "company123"


# ---------------------------------------------------------------------------
# compute_job_id
# ---------------------------------------------------------------------------

class TestComputeJobId:
    def test_returns_16_char_hex(self):
        job_id = compute_job_id("Stripe", "Backend Engineer")
        assert len(job_id) == 16
        assert all(c in "0123456789abcdef" for c in job_id)

    def test_deterministic(self):
        a = compute_job_id("Stripe", "Backend Engineer")
        b = compute_job_id("Stripe", "Backend Engineer")
        assert a == b

    def test_different_titles_differ(self):
        id_a = compute_job_id("Stripe", "Frontend Engineer")
        id_b = compute_job_id("Stripe", "Backend Engineer")
        assert id_a != id_b

    def test_different_companies_differ(self):
        id_a = compute_job_id("Stripe", "Backend Engineer")
        id_b = compute_job_id("Plaid", "Backend Engineer")
        assert id_a != id_b

    def test_location_does_not_affect_id(self):
        """Location is excluded from the key — same role collapses across cities."""
        assert compute_job_id("Stripe", "Backend Engineer") == compute_job_id(
            "Stripe", "Backend Engineer"
        )

    def test_none_company_accepted(self):
        job_id = compute_job_id(None, "Engineer")
        assert len(job_id) == 16

    def test_all_none_except_title(self):
        job_id = compute_job_id(None, "Engineer")
        assert len(job_id) == 16

    def test_only_hex_chars(self):
        job_id = compute_job_id("Google", "SWE")
        import re
        assert re.fullmatch(r"[0-9a-f]{16}", job_id) is not None


# ---------------------------------------------------------------------------
# parse_posted_date
# ---------------------------------------------------------------------------

class TestParsePostedDate:
    def test_none_returns_none_false(self):
        dt, est = parse_posted_date(None)
        assert dt is None
        assert est is False

    def test_datetime_object_returned_utc(self):
        now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)
        dt, est = parse_posted_date(now)
        assert dt == now
        assert est is False

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2024, 5, 1, 12, 0, 0)
        dt, est = parse_posted_date(naive)
        assert dt.tzinfo is not None
        assert est is False

    def test_iso_string_exact_date(self):
        dt, est = parse_posted_date("2024-05-01T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 5 and dt.day == 1
        assert est is False

    def test_iso_string_no_timezone(self):
        dt, est = parse_posted_date("2024-05-01T10:00:00")
        assert dt is not None
        assert dt.tzinfo is not None
        assert est is False

    def test_relative_3_days_ago(self):
        dt, est = parse_posted_date("3 days ago")
        assert dt is not None
        assert est is True
        # Should be approximately 3 days ago
        now = datetime.now(UTC)
        assert abs((now - dt).days - 3) <= 1

    def test_relative_posted_today(self):
        dt, est = parse_posted_date("Posted today")
        assert dt is not None
        assert est is True
        now = datetime.now(UTC)
        assert abs((now - dt).total_seconds()) < 86400 * 2  # within 2 days

    def test_empty_string_returns_none_false(self):
        dt, est = parse_posted_date("   ")
        assert dt is None
        assert est is False

    def test_unparseable_string_returns_none_false(self):
        dt, est = parse_posted_date("not a date at all xyzzy")
        assert dt is None
        assert est is False

    def test_datetime_with_non_utc_tz_converted(self):
        from datetime import timezone

        eastern = timezone(timedelta(hours=-5))
        aware = datetime(2024, 6, 1, 8, 0, 0, tzinfo=eastern)
        dt, est = parse_posted_date(aware)
        assert dt is not None
        assert dt.tzinfo == UTC
        assert dt.hour == 13  # 8am ET = 13:00 UTC
        assert est is False


# ---------------------------------------------------------------------------
# normalize_remote
# ---------------------------------------------------------------------------

class TestNormalizeRemote:
    def test_remote_string(self):
        assert normalize_remote("Remote") == "remote"

    def test_work_from_home(self):
        assert normalize_remote("Work from home") == "remote"

    def test_wfh_abbreviation(self):
        assert normalize_remote("WFH") == "remote"

    def test_distributed(self):
        assert normalize_remote("Distributed team") == "remote"

    def test_hybrid(self):
        assert normalize_remote("Hybrid") == "hybrid"

    def test_onsite_variants(self):
        assert normalize_remote("Onsite") == "onsite"
        assert normalize_remote("On-site") == "onsite"
        assert normalize_remote("On site") == "onsite"

    def test_office(self):
        assert normalize_remote("In office") == "onsite"

    def test_in_person(self):
        assert normalize_remote("In-person") == "onsite"

    def test_empty_string_returns_unknown(self):
        assert normalize_remote("") == "unknown"

    def test_none_returns_unknown(self):
        assert normalize_remote(None) == "unknown"

    def test_unrecognized_returns_unknown(self):
        assert normalize_remote("Flexible") == "unknown"

    def test_case_insensitive(self):
        assert normalize_remote("REMOTE") == "remote"
        assert normalize_remote("HYBRID") == "hybrid"


# ---------------------------------------------------------------------------
# raw_to_job
# ---------------------------------------------------------------------------

class TestRawToJob:
    def test_minimal_raw_dict(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        assert job.title == "Engineer"
        assert job.url == "https://example.com"
        assert job.source == "test"
        assert len(job.job_id) == 16
        assert job.enrichment_status == "pending"

    def test_job_id_is_16_hex_chars(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        import re
        assert re.fullmatch(r"[0-9a-f]{16}", job.job_id) is not None

    def test_full_raw_dict(self):
        raw = {
            "title": "Senior Data Engineer",
            "url": "https://jobs.example.com/123",
            "company": "Acme Inc",
            "location": "New York, NY",
            "city": "New York",
            "country": "US",
            "remote": "Hybrid",
            "description": "We are looking for a senior data engineer...",
            "salary_min": 120000,
            "salary_max": 160000,
            "salary_currency": "USD",
            "posted_date": "2024-05-01T00:00:00+00:00",
            "source_job_id": "ext-abc123",
        }
        job = raw_to_job(raw, source="adzuna")
        assert job.title == "Senior Data Engineer"
        assert job.company == "Acme Inc"
        assert job.city == "New York"
        assert job.country == "US"
        assert job.remote_mode == "hybrid"
        assert job.salary_min == 120000.0
        assert job.salary_max == 160000.0
        assert job.salary_currency == "USD"
        assert job.posted_date is not None
        assert job.posted_date_est is False
        assert job.source_job_id == "ext-abc123"
        assert job.source == "adzuna"

    def test_missing_optional_fields_are_none(self):
        raw = {"title": "Intern", "url": "https://example.com/job"}
        job = raw_to_job(raw, source="test")
        assert job.company is None
        assert job.city is None
        assert job.country is None
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.description is None

    def test_fallback_posted_date_when_missing(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        before = datetime.now(UTC)
        job = raw_to_job(raw, source="test")
        after = datetime.now(UTC)
        # When no posted_date given, falls back to ingested_at with est=True
        assert job.posted_date_est is True
        assert before <= job.posted_date <= after

    def test_raw_payload_is_serialized_json(self):
        import json
        raw = {"title": "Engineer", "url": "https://example.com", "extra": "data"}
        job = raw_to_job(raw, source="test")
        assert job.raw_payload is not None
        parsed = json.loads(job.raw_payload)
        assert parsed["extra"] == "data"

    def test_invalid_salary_skipped(self):
        raw = {
            "title": "Engineer",
            "url": "https://example.com",
            "salary_min": "not-a-number",
            "salary_max": None,
        }
        job = raw_to_job(raw, source="test")
        assert job.salary_min is None
        assert job.salary_max is None

    def test_enrichment_status_defaults_to_pending(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        assert job.enrichment_status == "pending"

    def test_visa_sponsorship_defaults_to_not_mentioned(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        assert job.visa_sponsorship == "not_mentioned"

    def test_skills_defaults_to_empty_list(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        assert job.skills == []

    def test_seniority_defaults_to_unclear(self):
        raw = {"title": "Engineer", "url": "https://example.com"}
        job = raw_to_job(raw, source="test")
        assert job.seniority == "unclear"
