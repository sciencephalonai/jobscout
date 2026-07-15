"""Resume ingestion: extract text from a dropped file + parse it into a UserProfile.

Two pure-ish steps:
  1. ``extract_resume_text(filename, data)`` — pull plain text out of a PDF / DOCX /
     TXT / JSON / arbitrary upload. No LLM.
  2. ``parse_resume_to_profile(text, label)`` — one DeepSeek call that extracts the
     candidate's REAL skills / years / target titles into a :class:`UserProfile`.

Truthfulness rule (borrowed from the resume-writing skill, applied generically): the
parser extracts only what the resume supports and never invents skills. Downstream,
a JD keyword counts as a match only if it is in this extracted skill set; everything
else is surfaced as a gap.
"""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from jobscout.blob import blob_store
from jobscout.config import settings
from jobscout.enrich import (
    EnrichmentError,
    _strip_code_fences,
    chat_json,
    llm_is_configured,
)
from jobscout.models import ResumeSection, StructuredResume, UserProfile

logger = logging.getLogger(__name__)

_MAX_RESUME_CHARS = 12_000  # cap text sent to the model (cost control)
_VALID_SENIORITY = {
    "intern", "junior", "mid", "senior", "staff", "principal", "lead",
    "manager", "director", "vp", "c_level",
}

_SECTION_HEADINGS = {
    "education": "Education",
    "academic background": "Education",
    "experience": "Work experience",
    "work experience": "Work experience",
    "professional experience": "Work experience",
    "employment history": "Work experience",
    "projects": "Projects",
    "selected projects": "Projects",
    "personal projects": "Projects",
    "skills": "Skills",
    "technical skills": "Skills",
    "certifications": "Certifications",
    "achievements": "Achievements",
    "awards": "Achievements",
    "publications": "Publications",
    "research": "Research",
    "volunteering": "Volunteering",
    "leadership": "Leadership",
    "activities": "Activities",
    "interests": "Interests",
}

# JSON-resume exports are commonly flattened to one line by earlier imports.
# These markers recover the meaningful user-facing sections without inventing
# fields or discarding the lossless ``resume_text`` source.
_FLAT_SECTION_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\beducation\s+institution\b", re.IGNORECASE), "Education"),
    (re.compile(r"\bskills\s+(?:programming[_ ]languages|technical)\b", re.IGNORECASE), "Skills"),
    (re.compile(r"\b(?:achievements[_ ]publications|publications?)\s+title\b", re.IGNORECASE), "Achievements & publications"),
    (re.compile(r"\bexperience\s+company\b", re.IGNORECASE), "Work experience"),
    (re.compile(r"\bprojects?\s+name\b", re.IGNORECASE), "Projects"),
]
_FLAT_FIELD_BREAK = re.compile(
    r"\s+(?=(?:institution|degree|field|cgpa|start_date|end_date|location|"
    r"company|title|date|technologies|highlights|links|github|live)\b)",
    re.IGNORECASE,
)


def _extract_flat_sections(text: str) -> list[ResumeSection]:
    """Recover readable sections from a previously flattened JSON resume."""
    hits = [
        (match.start(), match.end(), heading)
        for pattern, heading in _FLAT_SECTION_MARKERS
        for match in [pattern.search(text)]
        if match is not None
    ]
    hits.sort()
    if len(hits) < 2:
        return []

    sections: list[ResumeSection] = []
    for index, (_start, end, heading) in enumerate(hits):
        next_start = hits[index + 1][0] if index + 1 < len(hits) else len(text)
        # Drop the marker label, then introduce gentle line breaks before the
        # source keys. This is a presentation transform only; resume_text stays
        # untouched and remains the canonical record.
        content = _FLAT_FIELD_BREAK.sub("\n", text[end:next_start]).strip()
        if content:
            sections.append(ResumeSection(heading=heading, content=content))
    return sections


def needs_section_rebuild(sections: list[ResumeSection], text: str | None) -> bool:
    """Whether legacy one-blob sections should be re-rendered from raw text."""
    return (
        len(sections) == 1
        and sections[0].heading == "Additional information"
        and len(_extract_flat_sections(text or "")) >= 2
    )


def compose_resume_text(sections: list[ResumeSection]) -> str:
    """Inverse of :func:`extract_resume_sections`: rebuild the flat resume text.

    Used when the user edits sections in the Profile UI — the recomposed text is
    what semantic matching embeds (the embedding cache is keyed by its hash, so
    edits re-embed automatically).
    """
    parts: list[str] = []
    for sec in sections:
        heading = (sec.heading or "").strip()
        content = (sec.content or "").strip()
        if not heading and not content:
            continue
        parts.append(f"{heading}\n{content}" if heading else content)
    return "\n\n".join(parts)


def extract_resume_sections(text: str) -> list[ResumeSection]:
    """Split an extracted resume into readable, source-ordered sections.

    Detection is deliberately conservative: recognised headings get a friendly
    name, all other all-caps / short heading-like lines keep their original
    wording, and every non-heading line remains in exactly one section. The
    lossless ``resume_text`` stays canonical even for awkward PDF extraction.
    """
    source = (text or "").strip()
    flat_sections = _extract_flat_sections(source)
    if flat_sections:
        return flat_sections

    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[ResumeSection] = []
    heading = "Additional information"
    content: list[str] = []

    def flush() -> None:
        """Emit the section accumulated so far and reset the buffer."""
        body = "\n".join(content).strip()
        if body:
            sections.append(ResumeSection(heading=heading, content=body))

    for line in lines:
        stripped = line.strip()
        normalized = " ".join(stripped.lower().rstrip(":").split())
        short = 1 <= len(stripped) <= 64
        heading_like = short and (
            normalized in _SECTION_HEADINGS
            or (stripped == stripped.upper() and any(char.isalpha() for char in stripped))
        )
        if heading_like:
            flush()
            heading = _SECTION_HEADINGS.get(normalized, stripped.rstrip(":"))
            content = []
        else:
            content.append(line)
    flush()

    if sections:
        return sections
    stripped = (text or "").strip()
    return [ResumeSection(heading="Resume", content=stripped)] if stripped else []


def dedupe_name(candidate: str, taken: set[str]) -> str:
    """Return *candidate*, or "candidate (2)" / "(3)"… until it's unique in *taken*.

    For filenames the counter goes before the extension ("resume (2).docx").
    Prevents the silent duplicate profile/resume names that made identically
    labeled records indistinguishable in the UI.
    """
    if candidate not in taken:
        return candidate
    path = Path(candidate)
    stem, suffix = (path.stem, path.suffix) if path.suffix else (candidate, "")
    n = 2
    while f"{stem} ({n}){suffix}" in taken:
        n += 1
    return f"{stem} ({n}){suffix}"


def _safe_resume_filename(filename: str) -> str:
    """Return a display filename without allowing a user-controlled path."""
    name = Path(filename or "resume").name.strip()
    return name or "resume"


def _resume_suffix(filename: str | None) -> str:
    """Return a safe, lowercased file suffix (``.bin`` when unusable)."""
    suffix = Path(filename or "").suffix.lower()
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        return ".bin"
    return suffix


def resume_file_path(profile_id: str, filename: str | None) -> Path:
    """Legacy single-resume path: ``{dir}/{profile_id}{suffix}``.

    Retained for profiles uploaded before the resume library (their one file
    still lives here). New library uploads use :func:`library_resume_path`.
    """
    return Path(settings.resume_storage_dir) / f"{profile_id}{_resume_suffix(filename)}"


def library_resume_path(profile_id: str, resume_id: str, filename: str | None) -> Path:
    """Per-resume path: ``{dir}/{profile_id}/{resume_id}{suffix}``.

    A profile-scoped directory keyed by the resume id, so a second upload never
    overwrites the first (the pre-library flat path did). ``profile_id`` and
    ``resume_id`` are app-generated UUIDs, so no traversal is possible.
    """
    return Path(settings.resume_storage_dir) / profile_id / f"{resume_id}{_resume_suffix(filename)}"


def store_original_resume(profile_id: str, filename: str, data: bytes) -> str:
    """Persist the original resume file (via the BlobStore seam) and return its safe name."""
    safe_name = _safe_resume_filename(filename)
    blob_store.write(resume_file_path(profile_id, safe_name), data)
    return safe_name


def store_library_resume(profile_id: str, resume_id: str, filename: str, data: bytes) -> Path:
    """Persist one library resume file (via the BlobStore seam) and return its path."""
    path = library_resume_path(profile_id, resume_id, _safe_resume_filename(filename))
    blob_store.write(path, data)
    return path


# ---------------------------------------------------------------------------
# 1. Text extraction (no LLM)
# ---------------------------------------------------------------------------

def extract_resume_text(filename: str, data: bytes) -> str:
    """Extract plain text from a resume upload, dispatching on file extension.

    Supports .pdf (pypdf), .docx (python-docx), .txt/.md (decode), .json (flatten
    string values). Anything else falls back to a lenient utf-8 decode, so a
    user can drop "anything" with text in it.
    """
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            return _extract_pdf(data)
        if name.endswith(".docx"):
            return _extract_docx(data)
        if name.endswith(".json"):
            return _extract_json(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("structured extract failed for %s (%s) — falling back to decode", filename, exc)
    # .txt / .md / unknown → lenient decode.
    return data.decode("utf-8", errors="ignore").strip()


def _extract_pdf(data: bytes) -> str:
    """Extract plain text from a PDF upload (PyMuPDF)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    """Extract plain text from a DOCX upload."""
    import docx

    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs).strip()


def _extract_json(data: bytes) -> str:
    """Flatten a JSON resume into a text blob (all string/number leaf values)."""
    obj = json.loads(data.decode("utf-8", errors="ignore"))
    out: list[str] = []

    def walk(node: Any) -> None:
        """Recursively collect text from a DOCX XML node."""
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None:
            out.append(str(node))

    walk(obj)
    return " ".join(out).strip()


# ---------------------------------------------------------------------------
# 2. Resume → UserProfile (one DeepSeek call)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a precise resume parser. You return ONLY a single JSON object, no prose."
)

_USER_PROMPT = """Extract a candidate profile from the resume below.

Return ONLY a JSON object with EXACTLY these keys:
- "skills": array of concrete technical skills/tools the resume ACTUALLY shows (lowercased,
  deduped, max 80). Do NOT invent skills the resume does not support.
- "target_titles": array of role titles this candidate is a fit for, in their own words
  (e.g. "data scientist", "machine learning engineer"), max 12.
- "interests": array of explicit technical/domain interests supported by an Interests,
  Projects, Research, or Summary section (e.g. "computer vision", "genai"), max 12.
  Use an empty array when the resume does not support any clear interests.
- "yoe_max": integer — total years of professional experience shown (0 for new grad / student).
- "seniority_max": one of "intern","junior","mid","senior","staff","principal","lead",
  "manager","director","vp","c_level".
- "needs_sponsorship": boolean — true if the resume suggests the person needs visa sponsorship
  (e.g. international student, F-1/OPT, "authorized with sponsorship"). If unclear, use true
  (the safer default for surfacing more roles).

Extract only what the resume supports. Never invent skills or inflate experience.

RESUME:
{text}
"""


def parse_resume_to_profile(text: str, label: str) -> UserProfile:
    """Parse resume text into a saved-ready :class:`UserProfile` via one DeepSeek call.

    Raises:
        EnrichmentError: if DeepSeek is unconfigured or the call/parse hard-fails.
    """
    if not llm_is_configured():
        raise EnrichmentError("Selected LLM API key is not configured — cannot parse resume.")
    full_text = (text or "").strip()
    # The LLM receives a bounded window to keep profile extraction predictable
    # in cost. We still store the whole document on the returned profile below,
    # and semantic/deep matching work from that full canonical source text.
    snippet = full_text[:_MAX_RESUME_CHARS]
    if not snippet.strip():
        raise EnrichmentError("Empty resume text — nothing to parse.")

    try:
        content = chat_json(_SYSTEM_PROMPT, _USER_PROMPT.format(text=snippet))
    except Exception as exc:  # noqa: BLE001
        raise EnrichmentError(f"Resume parse call failed: {exc}") from exc
    if not content:
        raise EnrichmentError("Resume parse returned empty content.")
    try:
        raw = json.loads(_strip_code_fences(content))
    except (ValueError, TypeError) as exc:
        raise EnrichmentError(f"Resume parse returned unparseable JSON: {exc}") from exc

    skills = [str(s).strip().lower() for s in (raw.get("skills") or []) if str(s).strip()]
    titles = [str(t).strip() for t in (raw.get("target_titles") or []) if str(t).strip()]
    interests = [
        str(item).strip().lower()
        for item in (raw.get("interests") or [])
        if str(item).strip()
    ]
    seniority = raw.get("seniority_max")
    if seniority not in _VALID_SENIORITY:
        seniority = "mid"
    try:
        yoe_max = int(raw.get("yoe_max") or 0)
    except (TypeError, ValueError):
        yoe_max = 0

    return UserProfile(
        label=label,
        skills=list(dict.fromkeys(skills))[:80],
        target_titles=list(dict.fromkeys(titles))[:12],
        interests=list(dict.fromkeys(interests))[:12],
        seniority_max=seniority,
        yoe_max=max(0, yoe_max),
        needs_sponsorship=bool(raw.get("needs_sponsorship", True)),
        resume_text=full_text,
        resume_sections=extract_resume_sections(full_text),
        # Typed sections (second LLM call). Best-effort: structuring failure
        # must never sink an upload — the flat text is the canonical fallback.
        structured_resume=_try_structured(full_text),
    )


def _try_structured(text: str) -> StructuredResume | None:
    """Best-effort structured parse; None on any failure (flat text remains)."""
    try:
        return parse_structured_resume(text)
    except Exception:  # noqa: BLE001
        logger.warning("structured resume parse failed; keeping flat sections", exc_info=True)
        return None


_STRUCTURE_SYSTEM_PROMPT = (
    "You are a precise resume parser. Extract the resume into the given JSON schema. "
    "EXTRACTION ONLY: never invent, embellish, or reword facts; copy bullet text verbatim; "
    "omit any field the resume does not state (use null / empty list). Dates stay in the "
    "resume's own format."
)

_STRUCTURE_USER_PROMPT = """Parse this resume into JSON with EXACTLY these keys:
{{
  "summary": string|null,
  "education": [{{"institution": str, "degree": str|null, "field_of_study": str|null, "gpa": str|null,
                 "start_date": str|null, "end_date": str|null, "location": str|null, "honors": [str]}}],
  "experience": [{{"company": str, "title": str, "location": str|null, "start_date": str|null,
                  "end_date": str|null, "current": bool, "summary": str|null, "bullets": [str]}}],
  "projects": [{{"name": str, "technologies": [str], "url": str|null, "github_url": str|null,
                "start_date": str|null, "end_date": str|null, "bullets": [str]}}],
  "certifications": [{{"name": str, "issuer": str|null, "date": str|null, "credential_id": str|null,
                      "url": str|null}}],
  "publications": [{{"title": str, "venue": str|null, "date": str|null, "url": str|null,
                    "authors": [str], "description": str|null}}],
  "achievements": [{{"title": str, "issuer": str|null, "date": str|null, "description": str|null}}],
  "skill_categories": [{{"name": str, "skills": [str]}}],
  "custom_sections": [{{"title": str, "bullets": [str]}}]
}}

ROUTING (classify every item by WHAT IT IS, not by the heading it sits under — a combined heading
like "Achievements & Publications" MUST be split item by item):
- paper / conference / journal / preprint / "Published in" / has a DOI  -> publications
  (venue = the conference or journal, e.g. "ICSTE-23"; url = the DOI link; description = the
  explanatory sentence, verbatim)
- award / honor / scholarship / rank / dean's list / hackathon or competition win / leadership
  recognition -> achievements
- license / certificate / completed course credential (AWS, Azure, Coursera, …) -> certifications
- only a heading that fits NONE of the above -> custom_sections (keep its original title)

Rules: copy text verbatim; never merge two distinct items into one; preserve source order.
Label style: skill-category names and custom-section titles must be human-readable Title Case
("Programming Languages", "Cloud and DevOps") — never snake_case or lowercase identifiers.

RESUME:
{text}
"""


# Machine-y labels the LLM sometimes emits for the two free-text label fields
# (skill-category names, custom-section titles). They were rendered verbatim in
# the UI ("programming_languages", "achievements_publications"), so normalize
# defensively — the parse is non-deterministic and a prompt rule alone is not a
# guarantee.
_LABEL_ACRONYMS = {
    "ai": "AI", "ml": "ML", "genai": "GenAI", "llm": "LLM", "nlp": "NLP",
    "api": "API", "apis": "APIs", "ui": "UI", "ux": "UX", "sql": "SQL",
    "aws": "AWS", "gcp": "GCP", "devops": "DevOps", "mlops": "MLOps",
    "ci": "CI", "cd": "CD", "it": "IT", "qa": "QA", "hr": "HR", "bi": "BI",
}
_LABEL_SMALL_WORDS = {"and", "or", "of", "for", "the", "in", "to", "with", "&"}


def prettify_label(raw: str) -> str:
    """Turn ``programming_languages`` into ``Programming Languages``.

    Already-human labels ("Cloud and DevOps", "Frameworks & Libraries") are
    returned untouched — only machine-shaped ones (separators, all-lowercase)
    are rewritten.
    """
    label = " ".join((raw or "").replace("_", " ").replace("-", " ").split())
    if not label:
        return ""
    if not label.islower():
        return label  # already cased by a human or the model — trust it
    words = []
    for i, word in enumerate(label.split(" ")):
        if word in _LABEL_ACRONYMS:
            words.append(_LABEL_ACRONYMS[word])
        elif i > 0 and word in _LABEL_SMALL_WORDS:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def parse_structured_resume(text: str) -> StructuredResume:
    """Extract a typed :class:`StructuredResume` from flat resume text (one LLM call).

    Extraction-only by prompt contract — the model must not invent or reword
    facts. Raises :class:`EnrichmentError` on hard failures, mirroring
    :func:`parse_resume_to_profile`.
    """
    if not llm_is_configured():
        raise EnrichmentError("Selected LLM API key is not configured — cannot parse resume.")
    snippet = (text or "").strip()[:_MAX_RESUME_CHARS]
    if not snippet:
        raise EnrichmentError("Empty resume text — nothing to parse.")
    try:
        content = chat_json(
            _STRUCTURE_SYSTEM_PROMPT, _STRUCTURE_USER_PROMPT.format(text=snippet)
        )
    except Exception as exc:  # noqa: BLE001
        raise EnrichmentError(f"Structured resume parse failed: {exc}") from exc
    if not content:
        raise EnrichmentError("Structured resume parse returned empty content.")
    try:
        data = json.loads(content)
        parsed = StructuredResume.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise EnrichmentError(f"Structured resume JSON invalid: {exc}") from exc

    for category in parsed.skill_categories:
        category.name = prettify_label(category.name)
    for section in parsed.custom_sections:
        section.title = prettify_label(section.title)
    return parsed


def compose_resume_text_from_structured(sr: StructuredResume) -> str:
    """Canonical flat text from the typed sections.

    This is what semantic matching embeds, so every structured edit flows into
    the same text the engine reads (the embedding cache is keyed by its hash).
    """
    parts: list[str] = []
    if sr.summary:
        parts.append(f"Summary\n{sr.summary.strip()}")
    if sr.education:
        lines = ["Education"]
        for e in sr.education:
            head = " · ".join(x for x in (e.institution, e.degree, e.field_of_study) if x)
            meta = " · ".join(x for x in (
                f"{e.start_date or ''} – {e.end_date or ''}".strip(" –") or None,
                f"GPA {e.gpa}" if e.gpa else None, e.location,
            ) if x)
            lines.append(head + (f"\n{meta}" if meta else ""))
            lines.extend(f"- {h}" for h in e.honors)
        parts.append("\n".join(lines))
    if sr.experience:
        lines = ["Experience"]
        for w in sr.experience:
            dates = f"{w.start_date or ''} – {'Present' if w.current else (w.end_date or '')}".strip(" –")
            head = " · ".join(x for x in (w.title, w.company, w.location, dates or None) if x)
            lines.append(head)
            if w.summary:
                lines.append(w.summary)
            lines.extend(f"- {b}" for b in w.bullets)
        parts.append("\n".join(lines))
    if sr.projects:
        lines = ["Projects"]
        for pr in sr.projects:
            tech = ", ".join(pr.technologies)
            head = pr.name + (f" | {tech}" if tech else "")
            lines.append(head)
            lines.extend(f"- {b}" for b in pr.bullets)
        parts.append("\n".join(lines))
    if sr.certifications:
        lines = ["Certifications"]
        for c in sr.certifications:
            lines.append(" · ".join(x for x in (c.name, c.issuer, c.date) if x))
        parts.append("\n".join(lines))
    if sr.publications:
        lines = ["Publications"]
        for pub in sr.publications:
            head = " · ".join(x for x in (pub.title, pub.venue, pub.date) if x)
            lines.append(head)
            if pub.description:
                lines.append(pub.description)
            if pub.url:
                lines.append(pub.url)
        parts.append("\n".join(lines))
    if sr.achievements:
        lines = ["Achievements"]
        for ach in sr.achievements:
            head = " · ".join(x for x in (ach.title, ach.issuer, ach.date) if x)
            lines.append(head)
            if ach.description:
                lines.append(ach.description)
        parts.append("\n".join(lines))
    if sr.skill_categories:
        lines = ["Skills"]
        for cat in sr.skill_categories:
            lines.append(f"{cat.name}: {', '.join(cat.skills)}")
        parts.append("\n".join(lines))
    for sec in sr.custom_sections:
        lines = [sec.title]
        lines.extend(f"- {b}" for b in sec.bullets)
        parts.append("\n".join(lines))
    return "\n\n".join(p for p in parts if p.strip())

