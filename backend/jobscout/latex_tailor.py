"""LaTeX resume generation engine — per-profile, canonical-constrained, audited.

The default tailoring engine (``settings.tailor_engine == "latex"``). Unlike the
legacy Node toolkit (private to one candidate), this works for *any* profile: the
profile's own resume text is the sole fact source. Flow:

1. Gate the job (reuse ``tailor.resume_tailoring_gate``).
2. Ask the LLM (shared ``enrich.chat_json`` failover) for a **structured content
   plan** — sections/bullets selected and reworded from the candidate's resume and
   tailored to the job. The model returns JSON, never raw LaTeX, so a fixed template
   + deterministic escaped injection removes the whole class of LaTeX-injection and
   compile-fragility bugs (the maximal form of "constrain the template surface").
3. Render the plan to LaTeX → ``xelatex`` PDF, and to Markdown → ``pandoc`` DOCX.
4. **Audit (warn-only):** flag employers/titles/schools/numbers in the plan that are
   not grounded in the source. Never blocks a build.
5. Score before/after AI-reduction metrics (``resume_metrics``).

Requires ``xelatex`` + ``pandoc`` on PATH (see docs/configuration.md).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jobscout import resume_metrics
from jobscout.blob import blob_store
from jobscout.config import settings
from jobscout.enrich import (
    _strip_code_fences,
    active_llm_configuration,
    chat_json,
    llm_is_configured,
)
from jobscout.models import Job, UserProfile
from jobscout.tailor import (
    TailoredResume,
    TailoringError,
    resume_tailoring_gate,
    tailored_resume_path,
)

log = logging.getLogger(__name__)

_MAX_SOURCE_CHARS = 24_000
_MAX_JD_CHARS = 8_000
_MAX_ROLES = 8
_MAX_BULLETS = 12
_MAX_BULLET_CHARS = 400
_MAX_EDU = 8
_MAX_SKILLS = 14
_MAX_ADDITIONAL = 6
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9]+")

# LaTeX special characters → escaped forms. Backslash MUST be handled first.
_LATEX_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


# ── Template + escaping ───────────────────────────────────────────────────────

def _template_path() -> Path:
    """Locate ``resume_template.tex`` (override dir or the bundled template)."""
    if settings.resume_template_dir:
        candidate = Path(settings.resume_template_dir).expanduser() / "resume_template.tex"
        if candidate.is_file():
            return candidate
    bundled = Path(__file__).parent / "resume_templates" / "resume_template.tex"
    if not bundled.is_file():
        raise TailoringError("The LaTeX resume template is missing.")
    return bundled


def _esc(text: Any) -> str:
    """Escape a value for safe injection into LaTeX."""
    out = str(text or "")
    out = out.replace("\\", _LATEX_MAP["\\"])
    for ch, repl in _LATEX_MAP.items():
        if ch == "\\":
            continue
        out = out.replace(ch, repl)
    # Normalize the em-dash "AI tell" to an en-dash for date-range readability.
    return out.replace("—", "–")


# ── Candidate source + prompt ─────────────────────────────────────────────────

def _canonical_source(profile: UserProfile) -> str:
    """The candidate's own resume text — the only permitted fact source."""
    source = (profile.resume_text or "").strip()
    if not source:
        raise TailoringError(
            "This profile has no resume text to tailor from. Upload or paste a resume first."
        )
    return source[:_MAX_SOURCE_CHARS]


def _job_context(job: Job) -> str:
    location = job.location_raw or ", ".join(filter(None, (job.city, job.country)))
    parts = [
        f"Title: {job.title or ''}",
        f"Company: {job.company or ''}",
        f"Location: {location}",
        f"Description:\n{(job.description or '')[:_MAX_JD_CHARS]}",
    ]
    return "\n".join(parts)


_SYSTEM_PROMPT = (
    "You are a meticulous, truthful resume writer. You tailor an existing resume to a "
    "specific job WITHOUT inventing anything. Return ONLY valid JSON."
)


def _plan_prompt(job: Job, source: str) -> str:
    """Prompt the model for a canonical-constrained, job-tailored content plan."""
    return f"""Rewrite this candidate's resume, tailored to the job below.

HARD RULES:
- Use ONLY facts present in CANDIDATE RESUME. Never invent employers, job titles,
  dates, degrees, institutions, tools, or numeric metrics. If a number is not in
  the source, do not state one.
- You MAY reorder, re-emphasize, trim, and reword to match the job's priorities.
- Every experience bullet leads with the business outcome, is concise, and ends with
  a period. Avoid em-dashes and marketing buzzwords (leverage, synergy, robust, etc.).

Return JSON with EXACTLY this shape:
{{
  "name": "candidate full name from the resume",
  "contact_line": "email · phone · location · US Citizen (only parts present in source)",
  "summary": "2-3 sentence executive profile tailored to the job, from source facts",
  "experience": [
    {{"company": "", "location": "", "title": "", "dates": "", "bullets": ["", ""]}}
  ],
  "education": [{{"degree": "", "institution": "", "year": ""}}],
  "skills": [{{"category": "", "items": "comma-separated skills"}}],
  "additional": [{{"title": "e.g. Publications", "lines": ["", ""]}}]
}}

=== JOB ===
{_job_context(job)}

=== CANDIDATE RESUME ===
{source}
"""


# ── Plan validation ───────────────────────────────────────────────────────────

def _clean_str(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _validate_plan(raw: Any) -> dict[str, Any]:
    """Coerce and bound the model's JSON into a safe, render-ready plan."""
    if not isinstance(raw, dict):
        raise TailoringError("The resume planner did not return a JSON object.")
    name = _clean_str(raw.get("name"), 120)
    if not name:
        raise TailoringError("The resume planner produced no candidate name.")

    experience: list[dict[str, Any]] = []
    for role in (raw.get("experience") or [])[:_MAX_ROLES]:
        if not isinstance(role, dict):
            continue
        bullets = [
            _clean_str(b, _MAX_BULLET_CHARS)
            for b in (role.get("bullets") or [])[:_MAX_BULLETS]
            if _clean_str(b)
        ]
        company = _clean_str(role.get("company"))
        if not company and not bullets:
            continue
        experience.append({
            "company": company,
            "location": _clean_str(role.get("location")),
            "title": _clean_str(role.get("title")),
            "dates": _clean_str(role.get("dates"), 60),
            "bullets": bullets,
        })

    education = [
        {
            "degree": _clean_str(e.get("degree")),
            "institution": _clean_str(e.get("institution")),
            "year": _clean_str(e.get("year"), 40),
        }
        for e in (raw.get("education") or [])[:_MAX_EDU]
        if isinstance(e, dict) and (_clean_str(e.get("degree")) or _clean_str(e.get("institution")))
    ]
    skills = [
        {"category": _clean_str(s.get("category"), 80), "items": _clean_str(s.get("items"), 600)}
        for s in (raw.get("skills") or [])[:_MAX_SKILLS]
        if isinstance(s, dict) and _clean_str(s.get("items"))
    ]
    additional = []
    for sec in (raw.get("additional") or [])[:_MAX_ADDITIONAL]:
        if not isinstance(sec, dict):
            continue
        lines = [_clean_str(x, 400) for x in (sec.get("lines") or [])[:20] if _clean_str(x)]
        title = _clean_str(sec.get("title"), 80)
        if title and lines:
            additional.append({"title": title, "lines": lines})

    if not experience:
        raise TailoringError("The resume planner produced no usable experience entries.")
    return {
        "name": name,
        "contact_line": _clean_str(raw.get("contact_line"), 300),
        "summary": _clean_str(raw.get("summary"), 1200),
        "experience": experience,
        "education": education,
        "skills": skills,
        "additional": additional,
    }


# ── Renderers (LaTeX / Markdown / plaintext) ──────────────────────────────────

def _plan_to_latex(plan: dict[str, Any], template: str) -> str:
    exp_parts: list[str] = []
    for role in plan["experience"]:
        exp_parts.append(
            f"\\roleheader{{{_esc(role['company'])}}}{{{_esc(role['location'])}}}"
            f"{{{_esc(role['title'])}}}{{{_esc(role['dates'])}}}"
        )
        if role["bullets"]:
            items = "\n".join(f"  \\bulletitem{{{_esc(b)}}}" for b in role["bullets"])
            exp_parts.append(f"\\begin{{bullets}}\n{items}\n\\end{{bullets}}")
    edu = "\n".join(
        f"\\edurow{{{_esc(e['degree'])}}}{{{_esc(e['institution'])}}}{{{_esc(e['year'])}}}"
        for e in plan["education"]
    )
    skills = "\n".join(
        f"\\skillcat{{{_esc(s['category'])}}}{{{_esc(s['items'])}}}" for s in plan["skills"]
    )
    additional_parts: list[str] = []
    for sec in plan["additional"]:
        items = "\n".join(f"  \\bulletitem{{{_esc(x)}}}" for x in sec["lines"])
        additional_parts.append(
            f"\\section{{{_esc(sec['title'])}}}\n\\begin{{bullets}}\n{items}\n\\end{{bullets}}"
        )
    replacements = {
        "%%NAME%%": _esc(plan["name"]),
        "%%CONTACT_LINE%%": _esc(plan["contact_line"]),
        "%%SUMMARY%%": _esc(plan["summary"]),
        "%%EXPERIENCE%%": "\n\n".join(exp_parts),
        "%%EDUCATION%%": edu,
        "%%SKILLS%%": skills,
        "%%ADDITIONAL%%": "\n\n".join(additional_parts),
    }
    out = template
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


def _plan_to_markdown(plan: dict[str, Any]) -> str:
    lines = [f"# {plan['name']}", "", plan["contact_line"], "", "## Executive Profile",
             plan["summary"], "", "## Professional Experience"]
    for role in plan["experience"]:
        header = f"**{role['company']}**"
        if role["location"]:
            header += f" — {role['location']}"
        lines += ["", header, f"*{role['title']}* · {role['dates']}"]
        lines += [f"- {b}" for b in role["bullets"]]
    lines += ["", "## Education"]
    for e in plan["education"]:
        lines.append(f"**{e['degree']}**, {e['institution']} — {e['year']}")
    lines += ["", "## Technical Competencies"]
    for s in plan["skills"]:
        lines.append(f"**{s['category']}:** {s['items']}")
    for sec in plan["additional"]:
        lines += ["", f"## {sec['title']}"] + [f"- {x}" for x in sec["lines"]]
    return "\n".join(lines) + "\n"


def _plan_plaintext(plan: dict[str, Any]) -> str:
    """The prose used for metric scoring (summary + bullets + skills)."""
    parts = [plan["summary"]]
    for role in plan["experience"]:
        parts.extend(role["bullets"])
    for s in plan["skills"]:
        parts.append(s["items"])
    for sec in plan["additional"]:
        parts.extend(sec["lines"])
    return "\n".join(p for p in parts if p)


# ── Audit (warn-only) ─────────────────────────────────────────────────────────

def _grounded(value: str, source_lower: str) -> bool:
    """True unless every significant word of *value* is absent from the source."""
    words = re.findall(r"[a-z]{4,}", value.lower())
    if not words:
        return True
    return any(w in source_lower for w in words)


def audit_plan(plan: dict[str, Any], source: str) -> list[str]:
    """Flag entities/numbers not grounded in the source. Never raises."""
    source_lower = source.lower()
    source_digits = set(re.findall(r"\d[\d,\.]*", source))
    source_digits_norm = {d.replace(",", "") for d in source_digits}
    warnings: list[str] = []

    def flag(kind: str, value: str) -> None:
        if len(warnings) < 12 and value and not _grounded(value, source_lower):
            warnings.append(f"Unverified {kind}: '{value}'")

    for role in plan["experience"]:
        flag("employer", role["company"])
        flag("title", role["title"])
        for b in role["bullets"]:
            for num in re.findall(r"\d[\d,\.]*%?", b):
                norm = num.rstrip("%").replace(",", "")
                if (len(norm.replace(".", "")) >= 2 and norm not in source_digits_norm
                        and len(warnings) < 12):
                    warnings.append(f"Unverified metric '{num}' in a bullet — confirm it.")
    for e in plan["education"]:
        flag("institution", e["institution"])
        flag("degree", e["degree"])
    return warnings


# ── Compilation ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ}, check=False,
    )
    if result.returncode != 0:
        tail = (result.stdout or "")[-1200:] + (result.stderr or "")[-400:]
        raise TailoringError(f"{cmd[0]} failed:\n{tail.strip()}")
    return result


def _compile_pdf(tex: str, workdir: Path) -> Path:
    if shutil.which("xelatex") is None:
        raise TailoringError("xelatex is not installed. Install TeX Live (see docs/configuration.md).")
    tex_path = workdir / "resume.tex"
    tex_path.write_text(tex, encoding="utf-8")
    # Two passes so \section rules and any refs settle.
    for _ in range(2):
        _run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
              "resume.tex"], cwd=workdir, timeout=60)
    pdf = workdir / "resume.pdf"
    if not pdf.is_file():
        raise TailoringError("xelatex finished without producing a PDF.")
    return pdf


def _compile_docx(markdown: str, workdir: Path) -> Path:
    if shutil.which("pandoc") is None:
        raise TailoringError("pandoc is not installed. Install pandoc (see docs/configuration.md).")
    md_path = workdir / "resume.md"
    docx = workdir / "resume.docx"
    md_path.write_text(markdown, encoding="utf-8")
    _run(["pandoc", "resume.md", "-o", "resume.docx", "--from=markdown", "--standalone"],
         cwd=workdir, timeout=45)
    if not docx.is_file():
        raise TailoringError("pandoc finished without producing a DOCX.")
    return docx


def _display_filename(plan: dict[str, Any], job: Job) -> str:
    who = _SAFE_FILENAME.sub("_", plan["name"]).strip("_") or "Resume"
    where = _SAFE_FILENAME.sub("_", (job.company or "")).strip("_")
    stem = "_".join(p for p in ("Resume", who, where) if p)
    return f"{stem[:175].rstrip('_')}.docx"


# ── Entry point ───────────────────────────────────────────────────────────────

def build_latex_resume(job: Job, profile: UserProfile) -> TailoredResume:
    """Build a truthful, job-tailored PDF + DOCX for a profile, with AI-reduction metrics."""
    gate_warnings = resume_tailoring_gate(job, profile)
    if not llm_is_configured():
        raise TailoringError("Select an LLM provider and add its API key before tailoring a resume.")
    source = _canonical_source(profile)

    try:
        raw = json.loads(_strip_code_fences(chat_json(_SYSTEM_PROMPT, _plan_prompt(job, source)) or ""))
    except (ValueError, TypeError) as exc:
        raise TailoringError(f"Resume planner returned unusable JSON: {exc}") from exc
    except TailoringError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a bounded request failure
        raise TailoringError(f"Resume planner request failed: {exc}") from exc

    plan = _validate_plan(raw)
    template = _template_path().read_text(encoding="utf-8")
    tex = _plan_to_latex(plan, template)
    markdown = _plan_to_markdown(plan)

    docx_out = tailored_resume_path(profile.id, job.job_id)
    pdf_out = docx_out.with_suffix(".pdf")
    docx_out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jobscout-latex-") as tmp:
        workdir = Path(tmp)
        # Persist through the blob seam so files land on disk (local) or in
        # Supabase Storage (hosted) depending on the configured backend.
        blob_store.write(pdf_out, _compile_pdf(tex, workdir).read_bytes())
        blob_store.write(docx_out, _compile_docx(markdown, workdir).read_bytes())

    audit_warnings = audit_plan(plan, source)
    before = resume_metrics.compute_metrics(profile.resume_text or "")
    after = resume_metrics.compute_metrics(_plan_plaintext(plan))
    metrics = {
        "before": before,
        "after": after,
        "delta": resume_metrics.delta(before, after),
        "ai_risk_before": (before.get("composite") or {}).get("ai_risk_score"),
        "ai_risk_after": (after.get("composite") or {}).get("ai_risk_score"),
        "humanization_before": (before.get("composite") or {}).get("humanization_score"),
        "humanization_after": (after.get("composite") or {}).get("humanization_score"),
    }

    provider, _key, model = active_llm_configuration()
    filename = _display_filename(plan, job)
    log.info("latex_resume_built profile=%s job=%s provider=%s", profile.id, job.job_id, provider)
    return TailoredResume(
        path=docx_out,
        filename=filename,
        notes=[],
        warnings=gate_warnings + audit_warnings,
        provider=provider,
        model=model,
        pdf_path=pdf_out,
        metrics=metrics,
        engine="latex",
    )
