"""H-1B sponsor lookup + sponsorship-likelihood derivation.

A job posting almost never states visa sponsorship (≈96% are silent), so the raw
``visa_sponsorship`` field is a weak filter on its own. This module adds two
stronger, deterministic signals:

1. ``is_known_h1b_sponsor(company)`` — does this company appear in the public DoL
   H-1B/LCA filer list (``data/h1b_sponsors.txt``)? A company that has sponsored
   before is very likely to sponsor again, regardless of the JD text. Matching is
   exact on the NORMALIZED name (see ``normalize_text``) to avoid false positives.

2. ``derive_sponsorship_likelihood(...)`` — fold the visa field, cap-exempt
   employer type, citizenship requirement, and H-1B history into a single advisory
   signal: ``"likely" | "unknown" | "no"``.

Both are pure/deterministic and make no network or LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

# NOTE: ``normalize_text`` is imported lazily inside the functions that use it.
# A top-level import would create a cycle (models → sponsors → normalize → models),
# since ``models`` imports ``derive_sponsorship_likelihood`` from here.

# Curated company-name lists live at the repo root (parent of backend/).
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_SPONSORS_FILE = _DATA_DIR / "h1b_sponsors.txt"
_EVERIFY_FILE = _DATA_DIR / "everify_employers.txt"

# Lazily-loaded normalized name sets, keyed by file path.
_name_sets: dict[Path, set[str]] = {}


def _load_name_set(path: Path) -> set[str]:
    """Load + cache the normalized set of company names from a curated txt file."""
    cached = _name_sets.get(path)
    if cached is not None:
        return cached
    from jobscout.normalize import normalize_text  # lazy — avoids import cycle

    names: set[str] = set()
    try:
        for line in path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            norm = normalize_text(s)
            if norm:
                names.add(norm)
    except FileNotFoundError:
        names = set()
    _name_sets[path] = names
    return names


def is_known_h1b_sponsor(company: str | None) -> bool:
    """True if *company* (normalized) is a known H-1B filer from public DoL data."""
    if not company:
        return False
    from jobscout.normalize import normalize_text  # lazy — avoids import cycle

    return normalize_text(company) in _load_name_set(_SPONSORS_FILE)


def is_everify_employer(company: str | None) -> bool:
    """True if *company* (normalized) is a known E-Verify participant.

    Advisory only — the list is curated, not exhaustive (USCIS publishes no bulk
    feed and warns that absence does not imply non-enrollment). Required signal for
    the 24-month STEM OPT extension; verify on e-verify.gov before relying on it.
    """
    if not company:
        return False
    from jobscout.normalize import normalize_text  # lazy — avoids import cycle

    return normalize_text(company) in _load_name_set(_EVERIFY_FILE)


SponsorshipLikelihood = Literal["likely", "unknown", "no"]


def derive_sponsorship_likelihood(
    visa_sponsorship: str,
    cap_exempt: str,
    citizenship_required: bool,
    known_h1b_sponsor: bool,
) -> SponsorshipLikelihood:
    """Fold the available signals into an advisory sponsorship likelihood.

    - Explicit refusal or citizenship requirement → ``"no"`` (the only thing to hide).
    - Explicit "yes", a known H-1B filer, or a cap-exempt employer → ``"likely"``.
    - Otherwise (the ~96% that say nothing) → ``"unknown"`` (surfaced, not hidden).
    """
    if citizenship_required or visa_sponsorship == "no":
        return "no"
    if visa_sponsorship == "yes" or known_h1b_sponsor or cap_exempt in ("yes", "likely"):
        return "likely"
    return "unknown"
