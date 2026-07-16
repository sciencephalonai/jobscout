"""Deterministic, evidence-backed work-authorization extraction.

The LLM is useful for broad enrichment, but explicit hiring constraints should
not depend on a probabilistic interpretation.  These small, conservative
patterns only promote requirements when the job description says so directly.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, TypedDict


class EligibilitySignals(TypedDict):
    visa_sponsorship: Literal["no"] | None
    citizenship_required: bool
    security_clearance: Literal["required"] | None
    evidence: list[str]


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NO_SPONSORSHIP = re.compile(
    r"\b(?:no|without|cannot|can't|unable to|will not|does not|don't)\b[^.\n]{0,90}"
    r"\b(?:visa|immigration|employment)\s+sponsor(?:ship|ing)?\b"
    # Reversed order: "sponsorship is not available/offered/provided/possible".
    r"|\b(?:visa\s+|immigration\s+|employment\s+)?sponsorship\b[^.\n]{0,60}"
    r"\bnot\b[^.\n]{0,20}\b(?:available|offered|provided|possible)\b"
    # "authorized to work ... without (the need for) sponsorship".
    r"|\bwithout\b[^.\n]{0,45}\bsponsorship\b"
    r"|\b(?:must|require)\b[^.\n]{0,70}\b(?:permanent|unrestricted|indefinite)\b[^.\n]{0,55}"
    r"\bwork(?:ing)?\s+authorization\b",
    re.IGNORECASE,
)

# Defense/weapons-domain signal. Not by itself a legal wall, but for a
# sponsorship-needing candidate these postings are near-certain US-person/ITAR
# requirements even when the JD omits the boilerplate (verdict.py decides what
# to do with it per profile).
_DEFENSE_DOMAIN = re.compile(
    r"\b(?:weapon(?:s)?\s+system|missile|munition|warfighter|"
    r"itar|ear\s+(?:compliance|regulations)|us\s+persons?\s+(?:only|status|requirement)|"
    r"cleared\s+facility|clearance[- ]eligible|ability\s+to\s+obtain\s+a?\s*(?:security\s+)?clearance|"
    r"unmanned\s+(?:air(?:craft)?|aerial)\s+(?:system|vehicle)|\buas\b|\buav\b|"
    r"electronic\s+warfare|radar\s+(?:system|sensor)|rf\s+sensor|"
    r"defen[sc]e\s+(?:contractor|industry|sector|program)s?)\b",
    re.IGNORECASE,
)


# Major defense primes/contractors. Their engineering roles are almost always
# US-person/ITAR-bound even when a feed-truncated JD omits the boilerplate
# (verified escapes: L3Harris "Associate Software Engineer", RTX "Software
# Engineer 1" with keyword-free descriptions).
_DEFENSE_COMPANIES = frozenset({
    "northrop grumman", "lockheed martin", "raytheon", "rtx",
    "l3harris", "l3harris technologies", "leidos", "bae systems",
    "general dynamics", "general atomics", "anduril", "anduril industries",
    "ultra", "ultra intelligence and communications", "cubic",
    "the aerospace corporation", "aerospace corporation", "draper", "mitre",
    "booz allen hamilton", "caci", "saic", "peraton", "sierra nevada corporation",
    "spacex",  # ITAR (launch/export controlled) despite being commercial
})


def detect_defense_domain(
    title: str | None, description: str | None, company: str | None = None
) -> str | None:
    """Return the matched defense/weapons evidence string or None.

    Deterministic and conservative: exact keyword classes over title +
    description (HTML treated as spacing), plus a curated contractor list —
    feed-truncated JDs at defense primes routinely omit the US-person
    boilerplate that full postings carry.
    """
    name = " ".join((company or "").lower().split())
    if name and name in _DEFENSE_COMPANIES:
        return f"defense contractor ({company})"
    hay = f"{title or ''} {re.sub(r'<[^>]+>', ' ', description or '')}"[:20_000]
    m = _DEFENSE_DOMAIN.search(hay)
    return m.group(0) if m else None
_CITIZENSHIP = re.compile(
    r"\b(?:u\.?s\.?\s*(?:citizen|citizens)|us\s*(?:citizen|citizens)|u\.?s\.?\s*person|"
    r"green\s*card\s*(?:holder|holders|only|required)|permanent\s+resident(?:s)?\s+only)\b"
    r"|\b(?:itar|ear|export[- ]control(?:led)?)\b",
    re.IGNORECASE,
)
_CLEARANCE = re.compile(
    r"\b(?:active|current|existing|valid|required)\b[^.\n]{0,65}"
    r"\b(?:security\s+clearance|ts/sci|top\s+secret|secret\s+clearance)\b"
    r"|\b(?:security\s+clearance|ts/sci|top\s+secret|secret\s+clearance)\b[^.\n]{0,65}"
    r"\b(?:required|must|active|current)\b",
    re.IGNORECASE,
)


def _snippet(sentence: str) -> str:
    """Return a stable, compact proof snippet suitable for the UI."""
    normalized = " ".join(sentence.split())
    return normalized[:240].rstrip()


@lru_cache(maxsize=8_192)
def _extract_cached(description: str) -> tuple[str | None, bool, str | None, tuple[str, ...]]:
    """Memoized core of :func:`extract_work_authorization_evidence`.

    The sentence-split + 3-regex scan runs for every job on every personalized
    request; descriptions are immutable, so the result is cached as an immutable
    tuple (the public function rebuilds a fresh dict/list for callers).
    """
    signals = _scan(description)
    return (
        signals["visa_sponsorship"],
        signals["citizenship_required"],
        signals["security_clearance"],
        tuple(signals["evidence"]),
    )


def extract_work_authorization_evidence(description: str | None) -> EligibilitySignals:
    """Extract only unambiguous eligibility requirements and their source text.

    A generic EEO statement or question about future sponsorship is deliberately
    not enough to mark a role ineligible.  Results are deterministic, bounded,
    and safe to run before any paid model call.
    """
    if not description:
        return {
            "visa_sponsorship": None,
            "citizenship_required": False,
            "security_clearance": None,
            "evidence": [],
        }
    visa, citizenship, clearance, evidence = _extract_cached(description)
    return {
        "visa_sponsorship": visa,  # type: ignore[typeddict-item]
        "citizenship_required": citizenship,
        "security_clearance": clearance,  # type: ignore[typeddict-item]
        "evidence": list(evidence),
    }


def _scan(description: str | None) -> EligibilitySignals:
    """The actual regex scan (uncached; see the wrapper above)."""
    signals: EligibilitySignals = {
        "visa_sponsorship": None,
        "citizenship_required": False,
        "security_clearance": None,
        "evidence": [],
    }
    if not description:
        return signals

    # HTML is normally retained by direct ATS feeds. Treat tags as spacing so a
    # phrase split by markup still remains visible to the regexes.
    text = re.sub(r"<[^>]+>", " ", description)[:20_000]
    # Avoid treating the period in U.S. as a sentence boundary when retaining
    # the evidence snippet. The normalized form is still clear to candidates.
    text = re.sub(r"\bu\.s\.", "US", text, flags=re.IGNORECASE)

    # Fast path: one pass per pattern over the whole document. A pattern that
    # matches nowhere cannot match any sentence, so the per-sentence loop (which
    # otherwise ran 3 regexes × ~100 sentences × every job) is skipped entirely
    # for the ~90% of postings that state no restriction at all.
    has_sponsorship = bool(_NO_SPONSORSHIP.search(text))
    has_citizenship = bool(_CITIZENSHIP.search(text))
    has_clearance = bool(_CLEARANCE.search(text))
    if not (has_sponsorship or has_citizenship or has_clearance):
        return signals

    snippets: list[str] = []
    for sentence in _SENTENCE.split(text):
        if not sentence.strip():
            continue
        matched = False
        if has_sponsorship and _NO_SPONSORSHIP.search(sentence):
            signals["visa_sponsorship"] = "no"
            matched = True
        if has_citizenship and _CITIZENSHIP.search(sentence):
            signals["citizenship_required"] = True
            matched = True
        if has_clearance and _CLEARANCE.search(sentence):
            signals["security_clearance"] = "required"
            matched = True
        if matched:
            snippet = _snippet(sentence)
            if snippet and snippet not in snippets:
                snippets.append(snippet)

    signals["evidence"] = snippets[:4]
    return signals
