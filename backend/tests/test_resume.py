"""Tests for resume text extraction + the matched/gap keyword logic."""

from __future__ import annotations

import json

import jobscout.resume as resume
from jobscout.models import Job, UserProfile
from jobscout.resume import extract_resume_sections, extract_resume_text
from jobscout.verdict import _skill_score

# ── Text extraction (no LLM) ──────────────────────────────────────────────────

def test_extract_txt():
    text = extract_resume_text("resume.txt", b"Python, SQL, and PyTorch experience")
    assert "Python" in text and "PyTorch" in text


def test_extract_json_flattens_values():
    payload = json.dumps({
        "name": "V", "skills": ["python", "sql"], "exp": {"company": "Acme"},
    }).encode()
    text = extract_resume_text("resume.json", payload)
    assert "python" in text and "sql" in text and "Acme" in text


def test_extract_unknown_extension_falls_back_to_decode():
    text = extract_resume_text("resume.weird", b"plain text resume body")
    assert "plain text resume body" in text


def test_extract_empty():
    assert extract_resume_text("x.txt", b"") == ""


def test_resume_sections_preserve_standard_and_custom_headings():
    sections = extract_resume_sections(
        "Candidate Name\nEDUCATION\nMSc in Data Science\n\nWORK EXPERIENCE\nAcme\n\nPUBLICATIONS\nPaper A\n\nOPEN SOURCE\nProject B"
    )
    assert [section.heading for section in sections] == [
        "Additional information", "Education", "Work experience", "Publications", "OPEN SOURCE",
    ]
    assert sections[-1].content == "Project B"


def test_resume_sections_recover_flattened_json_resume_structure():
    sections = extract_resume_sections(
        "name V education institution UMD degree MS skills programming_languages Python SQL "
        "achievements_publications title Paper experience company Acme title Data Engineer "
        "projects name Portfolio technologies React"
    )
    assert [section.heading for section in sections] == [
        "Education", "Skills", "Achievements & publications", "Work experience", "Projects",
    ]
    assert "UMD" in sections[0].content
    assert "Acme" in sections[3].content


def test_original_resume_is_saved_with_a_safe_local_name(tmp_path, monkeypatch):
    monkeypatch.setattr(resume.settings, "resume_storage_dir", str(tmp_path))
    name = resume.store_original_resume("profile-1", "../V Resume.pdf", b"original bytes")
    assert name == "V Resume.pdf"
    assert resume.resume_file_path("profile-1", name).read_bytes() == b"original bytes"


# ── Matched + gaps (the truthfulness rule) ───────────────────────────────────

def _job(skills):
    return Job(job_id="j", source="ashby", title="X", url="http://x", skills=skills)


def test_skill_score_matched_and_gaps():
    job = _job(["python", "kubernetes", "rust"])
    profile = UserProfile(label="p", skills=["python", "sql"])
    score, matched, gaps = _skill_score(job, profile)
    assert matched == ["python"]                 # resume supports python → match
    assert "kubernetes" in gaps and "rust" in gaps  # not in resume → gaps
    assert "python" not in gaps                   # never both matched and gap


def test_skill_score_no_invented_matches():
    # A job skill NOT in the resume must never appear as matched.
    job = _job(["go", "scala"])
    profile = UserProfile(label="p", skills=["python"])
    _score, matched, gaps = _skill_score(job, profile)
    assert matched == []
    assert set(gaps) == {"go", "scala"}
