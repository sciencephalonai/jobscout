"""Tests for the US-only job filter (normalize.is_us_job)."""

import pytest

from jobscout.normalize import is_us_job


class TestIsUsJob:
    @pytest.mark.parametrize(
        "country,location,remote,expected",
        [
            # Explicit country wins
            ("us", None, "unknown", True),
            ("usa", None, "unknown", True),
            ("gb", None, "unknown", False),
            ("in", None, "unknown", False),
            ("germany", None, "remote", False),
            # Workday exposes only the primary country for multi-location jobs.
            # Keep a mixed posting when another location is explicitly in the US.
            ("Vietnam", "Vietnam, Hanoi; US, CA, Santa Clara", "onsite", True),
            ("Vietnam", "Vietnam, Hanoi; Vietnam, Ho Chi Minh City", "onsite", False),
            # country="us" tenant stamp overridden by a clearly foreign location
            # (global Workday boards: Intel/NVIDIA/KLA)
            ("us", "India, Bangalore", "onsite", False),
            ("us", "Israel, Raanana", "unknown", False),
            ("us", "Newport, United Kingdom", "onsite", False),
            # ...but US homonyms / US-signal locations keep the stamp
            ("us", "3201 New Mexico Campus (Washington, DC)", "onsite", True),
            ("us", "Atlanta, GA", "onsite", True),
            ("us", "Georgia", "onsite", True),
            ("us", "USA-CA-Milpitas-KLA", "onsite", True),
            ("us", "LVD 1st Floor", "onsite", True),
            # US locations
            (None, "Austin, TX", "onsite", True),
            (None, "San Francisco, CA", "remote", True),
            (None, "New York, NY", "remote", True),
            (None, "United States", "unknown", True),
            (None, "USA Only", "remote", True),
            # Bare US city names (no state code)
            (None, "San Francisco, Seattle", "unknown", True),
            (None, "Seattle", "onsite", True),
            (None, "Boston", "onsite", True),
            # Non-US onsite
            (None, "London, UK", "onsite", False),
            (None, "Berlin, Germany", "onsite", False),
            (None, "Toronto, Canada", "unknown", False),
            # Remote: no-geography is US-eligible; global scope is NOT
            # ("Worldwide" aggregator postings skew non-US employers/spam,
            # and each one saved burns LLM enrich + embed budget).
            (None, "Anywhere", "remote", False),
            (None, "Worldwide", "remote", False),
            (None, "Global", "remote", False),
            (None, "Fully Remote", "remote", True),
            (None, "100% Remote", "remote", True),
            (None, "Flexible / Remote", "remote", True),
            (None, "Remote (US)", "remote", True),
            (None, "", "remote", True),
            # Remote: specific foreign place is NOT US (the leak we fixed)
            (None, "Regensburg", "remote", False),
            (None, "Brazil", "remote", False),
            (None, "Remote - Europe", "remote", False),
            # Trailing ISO country code must NOT read as a US state abbr
            # ("…, in" ≠ Indiana; "…,IND" / "…,SGP" are country codes).
            (None, "Bangalore, Karnataka, in", "unknown", False),
            (None, "Bangalore,IND", "onsite", False),
            (None, "Singapore,SGP", "onsite", False),
            (None, "Mountain View, CALIFORNIA, us", "unknown", True),
            (None, "Indianapolis, IND", "onsite", True),  # state name wins
            ("us", "Bangalore,IND", "onsite", False),  # code beats tenant stamp
            # No location, not remote → not a US job
            (None, "", "unknown", False),
        ],
    )
    def test_is_us_job(self, country, location, remote, expected):
        assert is_us_job(country, location, remote) is expected

    @pytest.mark.parametrize(
        "country,location,remote,title,expected",
        [
            # Remote aggregator rows whose only geography is a foreign place in
            # the TITLE (the Speechify pattern) → dropped.
            (None, "Remote", "remote", "Software Engineer, Platform - Busan, South Korea", False),
            (None, "Remote", "remote", "Software Engineer, iOS - Montreal, Canada", False),
            (None, "Remote", "remote", "Clinical Data Engineer (Pharma/CRO, India-Remote)", False),
            (None, "Remote", "remote", "Staff Software Engineer - AI Marketing - CANADA", False),
            (None, "Remote", "remote", "Contract Software Engineer - Portugal", False),
            # Plain titles unaffected.
            (None, "Remote", "remote", "Senior Data Scientist", True),
            (None, "Remote (US)", "remote", "Software Engineer - Busan Team Lead", True),  # US loc wins
            # country="us" stamp + campus location + foreign title → dropped;
            # US-signal location still wins first.
            ("us", "", "unknown", "Machine Learning Intern - Hanoi", False),
            ("us", "Austin, TX", "onsite", "ML Engineer - LATAM markets", True),
        ],
    )
    def test_is_us_job_title_guard(self, country, location, remote, title, expected):
        assert is_us_job(country, location, remote, title=title) is expected
