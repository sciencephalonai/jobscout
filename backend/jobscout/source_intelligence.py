"""Source provenance shared by ingestion, ranking, and the UI API contract.

The same job can arrive from a company ATS and several aggregators.  The
application URL should favour the primary employer source, while discovery
sources remain useful for recall.  Keeping that distinction in one small,
deterministic module makes it visible to the candidate instead of hiding it in
deduplication internals.
"""

from __future__ import annotations

from typing import Literal

SourceKind = Literal["primary", "government", "curated", "aggregator", "scraper"]

# Official public ATS feeds and direct employer boards.  These are the preferred
# source for both screening and the application link.
PRIMARY_SOURCES = frozenset({
    "greenhouse", "lever", "ashby", "workable", "workday", "rippling",
    "recruitee", "smartrecruiters",
})
GOVERNMENT_SOURCES = frozenset({"usajobs"})
# Community-curated feeds whose records link straight to the employer's ATS
# apply page (e.g. the SimplifyJobs new-grad list). Application-link quality is
# direct; metadata richness is below a true ATS pull, so they lose dedup
# tiebreaks to primaries but count as direct for filtering.
CURATED_SOURCES = frozenset({"simplify"})
SCRAPER_SOURCES = frozenset({"jobspy"})


def source_kind(source: str | None) -> SourceKind:
    """Classify a source by how close it is to the employer's posting."""
    value = (source or "").strip().lower()
    if value in PRIMARY_SOURCES:
        return "primary"
    if value in GOVERNMENT_SOURCES:
        return "government"
    if value in CURATED_SOURCES:
        return "curated"
    if value in SCRAPER_SOURCES:
        return "scraper"
    return "aggregator"


def source_authority(source: str | None) -> int:
    """Lower values win when duplicate postings are collapsed."""
    kind = source_kind(source)
    return {"primary": 0, "government": 0, "curated": 1, "aggregator": 2, "scraper": 3}[kind]


def source_label(source: str | None) -> str:
    """Short human-readable provenance label for cards and job details."""
    kind = source_kind(source)
    return {
        "primary": "Direct employer ATS",
        "government": "Official government board",
        "curated": "Curated new-grad feed (direct apply link)",
        "aggregator": "Discovery source",
        "scraper": "Scraped discovery source",
    }[kind]


def is_primary_source(source: str | None) -> bool:
    """True for direct application sources (primary ATS, government, curated feeds)."""
    return source_kind(source) in {"primary", "government", "curated"}
