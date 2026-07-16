"""Normalize raw adapter dicts into canonical Job objects."""

from __future__ import annotations

import hashlib
import html
import json
import re
from contextlib import suppress
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


_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("data_ml_ai", [
        "data scientist", "data science", "machine learning", "ml engineer",
        "ai engineer", "nlp engineer", "deep learning", "llm engineer",
        "data engineer", "data analyst", "analytics engineer",
        "research scientist", "applied scientist", "quantitative researcher",
        "research engineer", "ai/ml", "ml/ai",
    ]),
    ("devops_infra", [
        "devops", "site reliability engineer", "sre",
        "infrastructure engineer", "platform engineer", "cloud engineer",
        "reliability engineer", "build engineer", "network engineer",
        "systems administrator", "sysadmin", "storage engineer",
        "database administrator", "dba",
    ]),
    ("security", [
        "security engineer", "security analyst", "security architect",
        "security researcher", "cybersecurity", "infosec",
        "penetration test", "soc analyst", "security grc",
        "information security",
    ]),
    ("product_mgmt", [
        "product manager", "product owner", "product lead",
    ]),
    ("design_ux", [
        "ux designer", "ui designer", "product designer",
        "graphic designer", "visual designer", "ux researcher",
        "ui/ux", "user experience designer", "creative director",
        "art director",
    ]),
    ("management", [
        "engineering manager", "technical lead manager",
        "program manager", "project manager",
        "director", "vice president",
        "head of", "cto", "ceo", "cfo", "cpo", "cio",
    ]),
    ("software_eng", [
        "software engineer", "software developer", "full stack", "fullstack",
        "full-stack", "frontend engineer", "frontend developer",
        "backend engineer", "backend developer", "mobile engineer",
        "mobile developer", "ios engineer", "ios developer",
        "android engineer", "android developer", "web developer", "web engineer",
        "robotic software", "simulation software",
    ]),
]


def derive_category(title: str) -> str:
    """Infer a broad job category from the title via keyword matching."""
    low = title.lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in low for kw in keywords):
            return category
    return "other"


# Phrases that mark an explicit new-grad / university / early-career / rotational
# program — the best-fit, least-contested roles for fresh graduates.
_NEW_GRAD_HINTS = (
    "new grad", "new graduate", "new-grad", "newgrad", "new college grad",
    "recent graduate", "recent grad", "university graduate", "university grad",
    "early career", "early-career", "earlycareer", "rotational program", "rotation program",
    "graduate program", "grad program", "graduate rotational", "campus hire", "campus recruit",
    "university recruiting", "university program", "leadership development program",
    "entry level program", "entry-level program", "apprenticeship", "apprentice program",
)
# Bound the description scan so a passing mention deep in a JD doesn't over-trigger.
_NEW_GRAD_DESC_CHARS = 800
_STRUCTURED_NEW_GRAD = re.compile(
    r"\b(?:employee type|career level|job level|position type)\s*:?\s*"
    r"(?:new college grad|new grad(?:uate)?|early[ -]career)\b",
    re.I,
)


def detect_new_grad_program(title: str | None, description: str | None = None) -> bool:
    """True when the posting is an explicit new-grad / early-career / rotational program.

    Title-weighted (most programs say it in the title); also scans the start of the
    description. Deterministic keyword match — no LLM, runs for every source at ingest.
    """
    hay = (title or "").lower()
    if any(h in hay for h in _NEW_GRAD_HINTS):
        return True
    if description:
        plain_description = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(description))
        )
        # Some ATS descriptions put an authoritative classification such as
        # ``Employee Type: New College Grad`` after the benefits/requirements
        # body.  This structured label is safe to scan across the full text;
        # the looser prose hints remain bounded below to avoid incidental hits.
        if _STRUCTURED_NEW_GRAD.search(plain_description):
            return True
        bounded = plain_description[:_NEW_GRAD_DESC_CHARS].lower()
        return any(h in bounded for h in _NEW_GRAD_HINTS)
    return False


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


# Title keywords are high-precision (a role that says "Intern"/"Contract" in the
# title almost always is one). Description phrases add recall but are matched as
# multi-word phrases to avoid false positives from legal boilerplate (e.g. the
# bare word "contract" in "employment contract" on a full-time posting).
_EMPLOYMENT_TITLE_RULES: list[tuple[str, list[str]]] = [
    ("internship", ["intern", "internship", "co-op", "co op", "coop"]),
    ("contract", [
        "contract", "contractor", "c2c", "1099", "freelance",
        "temp-to-perm", "temp to perm", "contract-to-hire", "contract to hire",
    ]),
    ("part_time", ["part-time", "part time"]),
    ("temporary", ["temporary", "seasonal", "fixed-term", "fixed term"]),
]

_EMPLOYMENT_DESC_PHRASES: list[tuple[str, list[str]]] = [
    ("internship", ["internship", "intern position", "summer intern"]),
    ("contract", [
        "contract position", "contract role", "contract opportunity",
        "this is a contract", "w2 contract", "month contract",
        "contract-to-hire", "contract to hire",
    ]),
    ("part_time", ["part-time", "part time"]),
    ("temporary", ["temporary position", "seasonal", "fixed-term", "fixed term"]),
]


def derive_employment_type(title: str, description: str | None = None) -> str:
    """Infer a canonical employment-type bucket from a job's title/description.

    Returns one of full_time|part_time|contract|internship|temporary, defaulting
    to ``full_time`` (the common case) when no clearer signal is present. Title
    keywords take precedence over description phrases.
    """
    low_t = (title or "").lower()
    for bucket, needles in _EMPLOYMENT_TITLE_RULES:
        if any(n in low_t for n in needles):
            return bucket
    if description:
        low_d = description.lower()[:3000]
        for bucket, phrases in _EMPLOYMENT_DESC_PHRASES:
            if any(p in low_d for p in phrases):
                return bucket
    return "full_time"


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
        raw = raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
        return raw, False

    # String path
    raw_str = str(raw).strip()
    if not raw_str:
        return None, False

    # Try ISO parse first — if it round-trips cleanly it is an exact date,
    # not an estimated one.
    try:
        dt = datetime.fromisoformat(raw_str)
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
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
# punctuation, the leftover geography is empty or an explicitly-US / no-geography
# term. Anything else — a specific foreign place ("regensburg", "brazil") OR a
# deliberately global scope ("worldwide", "anywhere") — is not: global-scoped
# postings on aggregator boards skew non-US employers and spam.
_GENERIC_REMOTE = {
    "", "fully", "flexible", "distributed",
    "home", "home based", "work from home", "wfh", "us", "usa",
    "united states", "america", "north america", "americas", "us based",
    "us only", "usa only", "remote us",
}

# Country names (and unambiguous regions) as they appear in ATS location strings.
# Used to catch jobs whose adapter over-stamped country="us" at the tenant level
# (e.g. a global Workday board) while the per-job location names a foreign place.
# NOTE: checked only AFTER _has_us_signal, so US homonyms ("New Mexico",
# "Georgia", "Washington") are already resolved as US by then.
_NON_US_PLACES = {
    "afghanistan", "argentina", "australia", "austria", "bangladesh", "belgium",
    "brazil", "bulgaria", "canada", "chile", "china", "colombia", "costa rica",
    "croatia", "czech republic", "czechia", "denmark", "ecuador", "egypt",
    "estonia", "finland", "france", "germany", "greece", "hong kong", "hungary",
    "india", "indonesia", "ireland", "israel", "italy", "japan", "kenya",
    "latvia", "lithuania", "malaysia", "mexico", "netherlands", "new zealand",
    "nigeria", "norway", "pakistan", "peru", "philippines", "poland",
    "portugal", "romania", "russia", "saudi arabia", "serbia", "singapore",
    "slovakia", "slovenia", "south africa", "south korea", "korea", "spain",
    "sweden", "switzerland", "taiwan", "thailand", "turkey", "ukraine",
    "united arab emirates", "united kingdom", "uruguay", "vietnam",
    "emea", "apac", "latam",
    # 3-letter ISO codes seen in ATS location strings ("Bangalore,IND",
    # "Singapore,SGP"). Only codes that aren't English words / US homonyms.
    "ind", "sgp", "gbr", "deu", "chn", "isr", "twn", "kor", "jpn", "vnm",
    "mys", "phl", "idn", "irl", "esp", "nld", "swe", "prt", "ukr", "tur",
    # (no "chl"/"col" — they collide with US campus building codes)
    "tha", "nzl", "arg", "pak", "bgd", "lka", "egy", "zaf",
}
_NON_US_PLACES_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in sorted(_NON_US_PLACES, key=len, reverse=True)) + r")\b"
)

# Unambiguous foreign cities that show up in job TITLES on remote aggregator
# boards ("Software Engineer, Platform - Busan, South Korea" with location
# "Remote"). Kept small and unambiguous — NO names that are also common US
# places (dublin OH, vancouver WA, melbourne FL, london OH, paris TX …); the
# titles that use those almost always append the country, which the country
# list catches.
_NON_US_CITIES = {
    "montreal", "toronto", "berlin", "munich",
    "madrid", "barcelona", "bilbao", "amsterdam", "warsaw", "lisbon",
    "zurich", "stockholm", "copenhagen", "prague", "vienna", "brussels",
    "busan", "seoul", "hsinchu", "taipei", "tokyo", "osaka", "bangalore",
    "bengaluru", "hyderabad", "chennai", "mumbai", "pune", "gurgaon", "noida",
    "tel aviv", "sao paulo", "belo horizonte", "bogota", "buenos aires",
    "mexico city", "guadalajara", "ho chi minh", "hanoi", "manila", "jakarta",
    "kyiv", "bucharest", "belgrade", "istanbul", "cairo", "lagos", "nairobi",
    "sydney", "auckland",
}
_TITLE_FOREIGN_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(p)
        for p in sorted(_NON_US_PLACES | _NON_US_CITIES, key=len, reverse=True)
    )
    + r")\b"
)


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
    # Strong signals first: explicit US mentions and full state names.
    if any(tok in loc for tok in ("united states", "u.s.", "u.s.a")):
        return True
    if {"usa", "us", "america"} & set(tokens):
        return True
    if any(name in loc for name in _US_STATE_NAMES):
        return True
    # Weak signals below can be faked by foreign strings — a trailing ISO
    # country code reads as a state abbreviation ("Bangalore, Karnataka, in"
    # → Indiana). Suppress them when the location also names a foreign place.
    if _TITLE_FOREIGN_RE.search(loc):
        return False
    # "City, ST" pattern with a real US state abbreviation.
    if any(re.search(rf",\s*{ab}\b", loc) for ab in _US_STATE_ABBR):
        return True
    # Bare major US city names (no state code).
    return any(city in loc for city in _US_CITIES)


def _has_explicit_us_location(loc: str) -> bool:
    """True when one location segment explicitly identifies a US location.

    This is deliberately narrower than :func:`_has_us_signal`: it is used only
    to rescue mixed-country multi-location jobs whose primary ``country`` field
    is foreign.  Full state names such as "Georgia" are not sufficient on their
    own because they are ambiguous outside the United States.
    """
    tokens = re.findall(r"[a-z]+", loc)
    if any(marker in loc for marker in ("united states", "u.s.", "u.s.a")):
        return True
    if {"usa", "us"} & set(tokens):
        return True
    return any(re.search(rf",\s*{abbr}\b", loc) for abbr in _US_STATE_ABBR)


def is_us_job(
    country: str | None,
    location_raw: str | None,
    remote_mode: str = "unknown",
    title: str | None = None,
) -> bool:
    """Heuristic: does this job belong on a US-only board?

    Keeps US-located roles (country US, or a US state / "City, ST" / US mention)
    and remote roles that are US-eligible or geographically unspecified. Drops
    roles tied to a clearly non-US country/region — including ones whose only
    geographic hint is a foreign place in the *title* ("… - Busan, South Korea"
    with location "Remote").
    """
    c = (country or "").strip().lower()
    loc = (location_raw or "").strip().lower()
    tokens = re.findall(r"[a-z]+", loc)
    ttl = (title or "").strip().lower()

    def _title_is_foreign() -> bool:
        # Only trusted when the location itself gave no US signal.
        return bool(ttl) and bool(_TITLE_FOREIGN_RE.search(ttl))

    if c:
        # A singular non-US country normally drops immediately. Workday's
        # country descriptor represents only the primary location, however, so
        # keep a semicolon-delimited multi-location posting when another segment
        # explicitly names a US option.
        if c not in _US_COUNTRY:
            segments = [segment.strip() for segment in loc.split(";") if segment.strip()]
            return len(segments) > 1 and any(
                _has_explicit_us_location(segment) for segment in segments
            )
        # country="us" may be a tenant-level stamp from a global board (e.g. a
        # multinational's Workday tenant): don't trust it when the per-job
        # location/title names a foreign place and carries no US signal.
        # US-signal check runs FIRST so "New Mexico"/"Georgia" stay US.
        if loc and _has_us_signal(loc, tokens):
            return True
        if loc and _NON_US_PLACES_RE.search(loc):
            return False
        return not _title_is_foreign()

    if _has_us_signal(loc, tokens):
        return True

    if remote_mode == "remote":
        # Strip the word "remote"/punctuation and see what geography is left.
        # If nothing specific remains (or only a generic global term), it's
        # US-eligible; a specific non-US place (e.g. "Regensburg", "Brazil") is
        # NOT a US job and is dropped. A foreign place in the title ("…, Busan,
        # South Korea") also disqualifies — remote aggregators put the target
        # geography there.
        residual = re.sub(r"\bremote\b", " ", loc)
        residual = re.sub(r"[^a-z ]", " ", residual)
        residual = re.sub(r"\s+", " ", residual).strip()
        return residual in _GENERIC_REMOTE and not _title_is_foreign()

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
        with suppress(TypeError, ValueError):
            salary_min = float(raw["salary_min"])
    if raw.get("salary_max") is not None:
        with suppress(TypeError, ValueError):
            salary_max = float(raw["salary_max"])

    # Persist the original dict as JSON for audit / re-enrichment
    raw_payload: str | None = None
    with suppress(TypeError, ValueError):
        raw_payload = json.dumps(raw, default=str)

    job_id = compute_job_id(company, title, city)

    employer_type_raw = raw.get("employer_type")
    employer_type = cast(
        "Literal['university','hospital','nonprofit','government','for_profit','unclear']",
        employer_type_raw if employer_type_raw in _VALID_EMPLOYER_TYPES else "unclear",
    )

    # Employment type: prefer a source-provided value (some adapters carry one),
    # else derive heuristically from title/description. Never "unknown" — falls
    # back to full_time so the full_time filter is the sensible default bucket.
    description = fix_mojibake(raw.get("description")) or None
    src_emp = normalize_employment_type(
        raw.get("employment_type") or raw.get("job_type")
    )
    employment_type = cast(
        "Literal['full_time','part_time','contract','internship','temporary']",
        src_emp if src_emp != "unknown" else derive_employment_type(title, description),
    )
    category = cast(
        "Literal['software_eng','data_ml_ai','devops_infra','security','product_mgmt','design_ux','management','other']",
        derive_category(title),
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
        category=category,
        employment_type=employment_type,
        description=description,
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
        # A curated new-grad source (e.g. SimplifyJobs) may assert the flag in
        # the raw dict; the keyword detector remains the fallback for the rest.
        new_grad_program=bool(raw.get("new_grad_program"))
        or detect_new_grad_program(title, description),
        enrichment_status="pending",
        raw_payload=raw_payload,
    )
