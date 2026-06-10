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
            # Remote: generic/global is US-eligible
            (None, "Anywhere", "remote", True),
            (None, "Worldwide", "remote", True),
            (None, "Fully Remote", "remote", True),
            (None, "100% Remote", "remote", True),
            (None, "Flexible / Remote", "remote", True),
            (None, "Remote (US)", "remote", True),
            (None, "", "remote", True),
            # Remote: specific foreign place is NOT US (the leak we fixed)
            (None, "Regensburg", "remote", False),
            (None, "Brazil", "remote", False),
            (None, "Remote - Europe", "remote", False),
            # No location, not remote → not a US job
            (None, "", "unknown", False),
        ],
    )
    def test_is_us_job(self, country, location, remote, expected):
        assert is_us_job(country, location, remote) is expected
