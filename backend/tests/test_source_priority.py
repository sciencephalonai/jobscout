"""Tests for source priority / ingestion order."""

from jobscout.services.source_config import _SOURCE_ORDER


def test_direct_ats_sources_run_before_aggregators() -> None:
    direct = ["greenhouse", "lever", "ashby", "workable", "workday", "rippling"]
    aggregators = ["remotive", "arbeitnow", "jobicy", "remoteok", "workingnomads", "themuse"]

    for direct_source in direct:
        for aggregator in aggregators:
            assert _SOURCE_ORDER.index(direct_source) < _SOURCE_ORDER.index(aggregator)


def test_scraper_source_stays_last() -> None:
    assert _SOURCE_ORDER[-1] == "jobspy"
