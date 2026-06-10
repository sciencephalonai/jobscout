"""Normalize raw adapter dicts into canonical Job objects."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

import dateparser
import ftfy

from jobscout.models import Job


def fix_mojibake(s: str | None) -> str | None:
    """Repair mis-decoded UTF-8 (e.g. ``storyâ€"one`` -> ``story—one``).

    Some upstream feeds serve UTF-8 that gets decoded as latin-1/cp1252, producing
    mojibake in titles and descriptions. ``ftfy`` reverses it.
    """
    if not s:
        return s
    return ftfy.fix_text(s)


# ---------------------------------------------------------------------------
# Regex to strip trailing legal-entity suffixes from company names
# ---------------------------------------------------------------------------
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|co|gmbh|plc|pty|pvt|sa|ag|nv|bv|ab|oy)\b\.?$",
    re.IGNORECASE,
)

# Strip everything that is not a letter, digit, or ASCII space
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
# Collapse runs of whitespace
_WHITESPACE = re.compile(r"\s+")

# Title-dedup helpers: drop parentheticals/brackets and work-mode qualifiers so the
# same role reposted with cosmetic variations collapses to one dedup id.
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_TITLE_QUALIFIERS = re.compile(
    r"\b(remote|hybrid|on[- ]?site|wfh|work from home|telecommute)\b", re.IGNORECASE
)

# Free-text employment/job-type → canonical bucket.
_EMPLOYMENT_TYPES = {
    "full_time": ("full time", "full-time", "fulltime", "permanent", "regular"),
    "part_time": ("part time", "part-time", "parttime"),
    "contract": ("contract", "contractor", "freelance", "consultant", "b2b", "c2c", "1099"),
    "internship": ("intern", "internship", "co-op", "coop", "working student", "werkstudent"),
    "temporary": ("temporary", "temp", "seasonal", "fixed term", "fixed-term"),
}


def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, remove common company suffixes.

    Used for the dedup hash, not for display.
    """
    if not s:
        return ""
    text = s.lower()
    # Remove punctuation (keep letters, digits, spaces)
    text = _PUNCT.sub(" ", text)
    # Strip trailing company-type suffixes iteratively (e.g. "Acme Corp LLC")
    prev = None
    while prev != text:
        prev = text
        text = _COMPANY_SUFFIXES.sub("", text).strip()
    # Collapse whitespace
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def normalize_title(title: str) -> str:
    """Normalize a title for dedup: drop parentheticals and work-mode qualifiers so
    the same role reposted with cosmetic variations collapses to one id."""
    if not title:
        return ""
    t = _PARENS.sub(" ", title)
    t = _TITLE_QUALIFIERS.sub(" ", t)
    return normalize_text(t)


def normalize_employment_type(raw: str | None) -> str:
    """Map a free-text employment/job-type string to a canonical bucket.

    Returns one of full_time|part_time|contract|internship|temporary|unknown.
    """
    if not raw:
        return "unknown"
    low = str(raw).lower()
    for bucket, needles in _EMPLOYMENT_TYPES.items():
        if any(n in low for n in needles):
            return bucket
    return "unknown"


def compute_job_id(company: str | None, title: str, city: str | None) -> str:
    """SHA256(normalize(company)|normalize_title(title)|normalize(city))[:16].

    ``normalize_title`` collapses cosmetic repost variations (parentheticals,
    "(Remote)" etc.) so the same role from multiple boards dedups to one id.
    """
    parts = "|".join(
        [
            normalize_text(company or ""),
            normalize_title(title),
            normalize_text(city or ""),
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def parse_posted_date(
    raw: str | datetime | None,
) -> tuple[datetime | None, bool]:
    """Return (datetime_utc, is_estimated).

    - If *raw* is already a ``datetime``: return it (UTC-normalised) with
      ``is_estimated=False``.
    - If *raw* is a non-empty string: parse with ``dateparser``; the result is
      treated as estimated (``is_estimated=True``) because relative text like
      "3 days ago" is imprecise.
    - If *raw* is ``None`` or the string cannot be parsed: return
      ``(None, False)`` — the caller should fall back to ``ingested_at``.
    """
    if raw is None:
        return None, False

    if isinstance(raw, datetime):
        # Ensure timezone-aware UTC
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=UTC)
        else:
            raw = raw.astimezone(UTC)
        return raw, False

    # String path
    raw_str = str(raw).strip()
    if not raw_str:
        return None, False

    # Try ISO parse first — if it round-trips cleanly it is an exact date,
    # not an estimated one.
    try:
        dt = datetime.fromisoformat(raw_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        return dt, False
    except ValueError:
        pass

    # Strip portal prefixes like "Posted today", "Posted 3 days ago"
    clean_str = re.sub(r"^posted\s+", "", raw_str, flags=re.IGNORECASE).strip()

    # Fall back to dateparser for relative strings ("3 days ago", "yesterday"…)
    parsed = dateparser.parse(
        clean_str,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
            "PREFER_DAY_OF_MONTH": "first",
        },
    )
    if parsed is None:
        return None, False
    return parsed.astimezone(UTC), True


def normalize_remote(raw: str | None) -> Literal["remote", "onsite", "hybrid", "unknown"]:
    """Map varied strings to ``remote|onsite|hybrid|unknown``."""
    if not raw:
        return "unknown"
    raw_l = raw.lower()
    if any(w in raw_l for w in ["remote", "work from home", "wfh", "distributed"]):
        return "remote"
    if any(w in raw_l for w in ["hybrid"]):
        return "hybrid"
    if any(w in raw_l for w in ["onsite", "on-site", "on site", "office", "in-person"]):
        return "onsite"
    return "unknown"


_US_COUNTRY = {
    "us", "usa", "u.s.", "u.s.a.", "united states",
    "united states of america", "america",
}
_US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}
# A remote job is US-eligible when, after removing the word "remote" and
# punctuation, the leftover geography is empty or a generic/global/US term.
# Anything else (a specific foreign place like "regensburg" or "brazil") is not.
_GENERIC_REMOTE = {
    "", "anywhere", "anywhere in the world", "anywhere in world", "worldwide",
    "global", "international", "everywhere", "fully", "flexible", "distributed",
    "home", "home based", "work from home", "wfh", "us", "usa",
    "united states", "america", "north america", "americas", "us based",
    "us only", "usa only", "remote us", "global remote",
}


# Major US cities (tech hubs) that are overwhelmingly US — used to recognise
# bare "San Francisco" / "Seattle" style locations with no state code.
_US_CITIES = {
    "san francisco", "seattle", "new york", "brooklyn", "boston", "austin",
    "los angeles", "san diego", "denver", "atlanta", "dallas", "houston",
    "portland", "philadelphia", "phoenix", "pittsburgh", "minneapolis",
    "nashville", "charlotte", "raleigh", "durham", "mountain view",
    "palo alto", "sunnyvale", "menlo park", "santa clara", "bellevue",
    "chicago", "salt lake city", "san mateo", "redwood city",
}


def _has_us_signal(loc: str, tokens: list[str]) -> bool:
    if any(tok in loc for tok in ("united states", "u.s.", "u.s.a")):
        return True
    if {"usa", "us", "america"} & set(tokens):
        return True
    if any(name in loc for name in _US_STATE_NAMES):
        return True
    # "City, ST" pattern with a real US state abbreviation.
    if any(re.search(rf",\s*{ab}\b", loc) for ab in _US_STATE_ABBR):
        return True
    # Bare major US city names (no state code).
    if any(city in loc for city in _US_CITIES):
        return True
    return False


def is_us_job(
    country: str | None,
    location_raw: str | None,
    remote_mode: str = "unknown",
) -> bool:
    """Heuristic: does this job belong on a US-only board?

    Keeps US-located roles (country US, or a US state / "City, ST" / US mention)
    and remote roles that are US-eligible or geographically unspecified. Drops
    roles tied to a clearly non-US country/region.
    """
    c = (country or "").strip().lower()
    if c:
        # An explicit country wins: US tokens keep, anything else (gb, in, …) drops.
        return c in _US_COUNTRY

    loc = (location_raw or "").strip().lower()
    tokens = re.findall(r"[a-z]+", loc)
    if _has_us_signal(loc, tokens):
        return True

    if remote_mode == "remote":
        # Strip the word "remote"/punctuation and see what geography is left.
        # If nothing specific remains (or only a generic global term), it's
        # US-eligible; a specific non-US place (e.g. "Regensburg", "Brazil") is
        # NOT a US job and is dropped.
        residual = re.sub(r"\bremote\b", " ", loc)
        residual = re.sub(r"[^a-z ]", " ", residual)
        residual = re.sub(r"\s+", " ", residual).strip()
        return residual in _GENERIC_REMOTE

    # Onsite/hybrid with no US signal (e.g. "London", "Berlin") → not a US job.
    return False


# Employer types a curated adapter may stamp directly (cap-exempt sourcing).
_VALID_EMPLOYER_TYPES = {
    "university", "hospital", "nonprofit", "government", "for_profit", "unclear",
}


def raw_to_job(raw: dict[str, Any], source: str) -> Job:
    """Convert a raw adapter dict to a canonical :class:`~jobscout.models.Job`.

    Adapters yield dicts with any combination of:
        title, company, location, city, country, remote, description, url,
        salary_min, salary_max, salary_currency, posted_date, source_job_id,
        employer_type

    All fields are optional **except** ``title`` and ``url``.  ``employer_type``
    lets a curated adapter (e.g. a university Workday tenant) stamp the
    cap-exempt employer class directly instead of relying on LLM inference.
    """
    now_utc = datetime.now(UTC)

    # Repair mojibake in human-readable text at the boundary (titles/companies/
    # descriptions from feeds that mis-encode UTF-8).
    title: str = (fix_mojibake(str(raw.get("title", ""))) or "").strip()
    url: str = str(raw.get("url", "")).strip()

    company: str | None = raw.get("company") or None
    if company:
        company = (fix_mojibake(str(company)) or "").strip() or None

    location_raw: str | None = raw.get("location") or None
    if location_raw:
        location_raw = str(location_raw).strip() or None

    city: str | None = raw.get("city") or None
    if city:
        city = str(city).strip() or None

    country: str | None = raw.get("country") or None
    if country:
        country = str(country).strip() or None

    # posted_date
    posted_date_raw = raw.get("posted_date")
    posted_date, posted_date_est = parse_posted_date(posted_date_raw)
    if posted_date is None:
        # Spec §9 rule 3: fall back to ingested_at, mark estimated
        posted_date = now_utc
        posted_date_est = True

    # salary
    salary_min: float | None = None
    salary_max: float | None = None
    if raw.get("salary_min") is not None:
        try:
            salary_min = float(raw["salary_min"])
        except (TypeError, ValueError):
            pass
    if raw.get("salary_max") is not None:
        try:
            salary_max = float(raw["salary_max"])
        except (TypeError, ValueError):
            pass

    # Persist the original dict as JSON for audit / re-enrichment
    raw_payload: str | None = None
    try:
        raw_payload = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        pass

    job_id = compute_job_id(company, title, city)

    employer_type_raw = raw.get("employer_type")
    employer_type = cast(
        "Literal['university','hospital','nonprofit','government','for_profit','unclear']",
        employer_type_raw if employer_type_raw in _VALID_EMPLOYER_TYPES else "unclear",
    )

    return Job(
        job_id=job_id,
        source=source,
        source_job_id=raw.get("source_job_id") or None,
        title=title,
        company=company,
        location_raw=location_raw,
        country=country,
        city=city,
        remote_mode=normalize_remote(raw.get("remote")),
        description=fix_mojibake(raw.get("description")) or None,
        url=url,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=raw.get("salary_currency") or None,
        posted_date=posted_date,
        posted_date_est=posted_date_est,
        ingested_at=now_utc,
        # LLM-enriched fields left at defaults; enrichment worker fills them in
        yoe_min=None,
        yoe_max=None,
        visa_sponsorship="not_mentioned",
        work_auth_required=None,
        restrictions=None,
        skills=[],
        seniority="unclear",
        # Curated adapters may stamp this directly; otherwise enrichment fills it.
        employer_type=employer_type,
        enrichment_status="pending",
        raw_payload=raw_payload,
    )
