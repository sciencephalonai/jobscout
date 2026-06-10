"""Tests for deduplication logic — compute_job_id and normalize_text interaction.

Dedup key is company + title ONLY. Location is intentionally excluded so the
same role posted across multiple cities collapses to one job (locations are
aggregated onto it at ingest).
"""

from __future__ import annotations

import re

from jobscout.normalize import compute_job_id, normalize_text

# ---------------------------------------------------------------------------
# Equality tests (same canonical company+title → same job_id)
# ---------------------------------------------------------------------------

class TestDedupEquality:
    def test_same_inputs_same_id(self):
        assert compute_job_id("Stripe", "Software Engineer") == compute_job_id(
            "Stripe", "Software Engineer"
        )

    def test_different_casing_same_id(self):
        assert (
            compute_job_id("stripe", "software engineer")
            == compute_job_id("STRIPE", "SOFTWARE ENGINEER")
            == compute_job_id("Stripe", "Software Engineer")
        )

    def test_company_inc_suffix_stripped(self):
        assert compute_job_id("Stripe Inc", "Engineer") == compute_job_id("stripe", "engineer")

    def test_company_llc_suffix_stripped(self):
        assert compute_job_id("Startup LLC", "Engineer") == compute_job_id("startup", "engineer")

    def test_extra_whitespace_normalized(self):
        assert compute_job_id("Acme", "Data Engineer") == compute_job_id(
            "  Acme  ", "  Data   Engineer  "
        )

    def test_punctuation_stripped(self):
        assert compute_job_id("Acme", "Engineer") == compute_job_id("Acme,", "Engineer.")

    def test_title_remote_qualifier_collapses(self):
        """Cosmetic work-mode/parenthetical noise in titles collapses."""
        base = compute_job_id("Acme", "Data Analyst")
        assert compute_job_id("Acme", "Data Analyst (Remote)") == base
        assert compute_job_id("Acme", "Data Analyst - Remote") == base


# ---------------------------------------------------------------------------
# Location is NOT part of the key — same role across cities collapses
# ---------------------------------------------------------------------------

class TestLocationIgnored:
    def test_same_company_title_different_intent_collapses(self):
        """The same role 'reposted for many cities' is one job."""
        ny = compute_job_id("Stripe", "Engineer")
        sf = compute_job_id("Stripe", "Engineer")
        assert ny == sf


# ---------------------------------------------------------------------------
# Inequality tests (different company/title → different job_id)
# ---------------------------------------------------------------------------

class TestDedupInequality:
    def test_different_title_different_id(self):
        assert compute_job_id("Stripe", "Frontend Engineer") != compute_job_id(
            "Stripe", "Backend Engineer"
        )

    def test_different_company_different_id(self):
        assert compute_job_id("Stripe", "Engineer") != compute_job_id("Plaid", "Engineer")

    def test_none_vs_value_company_different(self):
        assert compute_job_id(None, "Engineer") != compute_job_id("Acme", "Engineer")


# ---------------------------------------------------------------------------
# Format tests
# ---------------------------------------------------------------------------

class TestJobIdFormat:
    def test_job_id_is_exactly_16_chars(self):
        assert len(compute_job_id("Stripe", "Software Engineer")) == 16

    def test_job_id_only_hex_chars(self):
        assert re.fullmatch(r"[0-9a-f]{16}", compute_job_id("Google", "SRE")) is not None

    def test_job_id_lowercase_hex(self):
        job_id = compute_job_id("Meta", "ML Engineer")
        assert job_id == job_id.lower()

    def test_various_inputs_all_produce_16_hex(self):
        cases = [("", "Engineer"), (None, "Intern"), ("Acme Corp LLC", "Director of Engineering")]
        for company, title in cases:
            job_id = compute_job_id(company, title)
            assert re.fullmatch(r"[0-9a-f]{16}", job_id) is not None, (
                f"Failed for company={company!r}, title={title!r}"
            )


# ---------------------------------------------------------------------------
# normalize_text round-trip tests
# ---------------------------------------------------------------------------

class TestNormalizeTextForDedup:
    def test_normalize_text_idempotent(self):
        once = normalize_text("Acme Inc, LLC.")
        assert once == normalize_text(once)

    def test_normalize_empty(self):
        assert normalize_text("") == ""

    def test_normalize_whitespace_only(self):
        assert normalize_text("   ") == ""

    def test_normalize_strips_all_known_suffixes(self):
        for suffix in ["Inc", "LLC", "Ltd", "Corp", "Co", "GmbH", "PLC", "Pty"]:
            result = normalize_text(f"Company {suffix}")
            assert suffix.lower() not in result.split()
