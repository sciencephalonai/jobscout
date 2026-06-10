"""Tests for deduplication logic — compute_job_id and normalize_text interaction."""

from __future__ import annotations

import re

from jobscout.normalize import compute_job_id, normalize_text

# ---------------------------------------------------------------------------
# Normalization-equality tests (same canonical form → same job_id)
# ---------------------------------------------------------------------------

class TestDedupEquality:
    def test_same_inputs_same_id(self):
        """Identical company/title/city always yield the same job_id."""
        id_a = compute_job_id("Stripe", "Software Engineer", "New York")
        id_b = compute_job_id("Stripe", "Software Engineer", "New York")
        assert id_a == id_b

    def test_different_casing_same_id(self):
        """Case differences should be normalized away."""
        id_lower = compute_job_id("stripe", "software engineer", "new york")
        id_upper = compute_job_id("STRIPE", "SOFTWARE ENGINEER", "NEW YORK")
        id_mixed = compute_job_id("Stripe", "Software Engineer", "New York")
        assert id_lower == id_upper == id_mixed

    def test_company_inc_suffix_stripped(self):
        """'Stripe Inc' and 'stripe' should hash identically after normalization."""
        id_with = compute_job_id("Stripe Inc", "Engineer", "New York")
        id_without = compute_job_id("stripe", "engineer", "new york")
        assert id_with == id_without

    def test_company_llc_suffix_stripped(self):
        """'Startup LLC' and 'startup' should hash identically."""
        id_with = compute_job_id("Startup LLC", "Engineer", "Austin")
        id_without = compute_job_id("startup", "engineer", "austin")
        assert id_with == id_without

    def test_company_ltd_suffix_stripped(self):
        """'BritishCo Ltd' and 'britishco' should hash identically."""
        id_with = compute_job_id("BritishCo Ltd", "Engineer", "London")
        id_without = compute_job_id("britishco", "engineer", "london")
        assert id_with == id_without

    def test_city_case_insensitive(self):
        """'New York' and 'new york' should produce the same job_id."""
        id_a = compute_job_id("Stripe", "Engineer", "New York")
        id_b = compute_job_id("Stripe", "Engineer", "new york")
        assert id_a == id_b

    def test_extra_whitespace_normalized(self):
        """Extra internal and leading/trailing whitespace is collapsed."""
        id_clean = compute_job_id("Acme", "Data Engineer", "San Francisco")
        id_spaced = compute_job_id("  Acme  ", "  Data   Engineer  ", "  San Francisco  ")
        assert id_clean == id_spaced

    def test_punctuation_stripped(self):
        """Punctuation differences are stripped before hashing."""
        id_plain = compute_job_id("Acme", "Engineer", "New York")
        id_punct = compute_job_id("Acme,", "Engineer.", "New York!")
        assert id_plain == id_punct


# ---------------------------------------------------------------------------
# Inequality tests (different inputs → different job_id)
# ---------------------------------------------------------------------------

class TestDedupInequality:
    def test_different_title_different_id(self):
        id_a = compute_job_id("Stripe", "Frontend Engineer", "New York")
        id_b = compute_job_id("Stripe", "Backend Engineer", "New York")
        assert id_a != id_b

    def test_different_company_different_id(self):
        id_a = compute_job_id("Stripe", "Engineer", "New York")
        id_b = compute_job_id("Plaid", "Engineer", "New York")
        assert id_a != id_b

    def test_different_city_different_id(self):
        id_a = compute_job_id("Stripe", "Engineer", "New York")
        id_b = compute_job_id("Stripe", "Engineer", "San Francisco")
        assert id_a != id_b

    def test_none_vs_value_company_different(self):
        id_none = compute_job_id(None, "Engineer", "Austin")
        id_real = compute_job_id("Acme", "Engineer", "Austin")
        assert id_none != id_real

    def test_none_vs_value_city_different(self):
        id_none = compute_job_id("Acme", "Engineer", None)
        id_real = compute_job_id("Acme", "Engineer", "Austin")
        assert id_none != id_real


# ---------------------------------------------------------------------------
# Format tests
# ---------------------------------------------------------------------------

class TestJobIdFormat:
    def test_job_id_is_exactly_16_chars(self):
        job_id = compute_job_id("Stripe", "Software Engineer", "New York")
        assert len(job_id) == 16

    def test_job_id_only_hex_chars(self):
        job_id = compute_job_id("Google", "Site Reliability Engineer", "Mountain View")
        assert re.fullmatch(r"[0-9a-f]{16}", job_id) is not None

    def test_job_id_lowercase_hex(self):
        """Hex digits must be lowercase (sha256.hexdigest is always lowercase)."""
        job_id = compute_job_id("Meta", "ML Engineer", "Menlo Park")
        assert job_id == job_id.lower()

    def test_various_inputs_all_produce_16_hex(self):
        cases = [
            ("", "Engineer", ""),
            (None, "Intern", None),
            ("Acme Corp LLC", "Director of Engineering", "New York"),
            ("startup", "cto", "remote"),
        ]
        for company, title, city in cases:
            job_id = compute_job_id(company, title, city)
            assert re.fullmatch(r"[0-9a-f]{16}", job_id) is not None, (
                f"Failed for inputs: company={company!r}, title={title!r}, city={city!r}"
            )


# ---------------------------------------------------------------------------
# normalize_text round-trip tests
# ---------------------------------------------------------------------------

class TestNormalizeTextForDedup:
    def test_normalize_text_idempotent(self):
        """Running normalize_text twice gives the same result as once."""
        text = "Acme Inc, LLC."
        once = normalize_text(text)
        twice = normalize_text(once)
        assert once == twice

    def test_normalize_empty(self):
        assert normalize_text("") == ""

    def test_normalize_whitespace_only(self):
        assert normalize_text("   ") == ""

    def test_normalize_strips_all_known_suffixes(self):
        """All legal suffixes defined in the spec should be removable."""
        suffixes = ["Inc", "LLC", "Ltd", "Corp", "Co", "GmbH", "PLC", "Pty"]
        for suffix in suffixes:
            result = normalize_text(f"Company {suffix}")
            assert suffix.lower() not in result.split(), (
                f"Suffix '{suffix}' was not stripped from 'Company {suffix}'"
            )
