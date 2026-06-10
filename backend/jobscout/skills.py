"""Skill canonicalization + fuzzy matching for the verdict/match layer.

Job descriptions and resumes name the same skill many ways ("JS" / "JavaScript",
"postgres" / "PostgreSQL", "sklearn" / "scikit-learn"). Exact set-intersection
misses these, which both deflates the match score and creates false "gap" chips.

This module canonicalizes a skill string and decides whether two skills mean the
same thing, using only the stdlib (a curated alias map + token/substring checks +
``difflib`` ratio). Conservative on purpose: we'd rather miss a loose match than
invent one (the matched/gap chips are the user's audit trail).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Common synonyms / abbreviations → canonical form. Extend freely.
_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "postgres": "postgresql",
    "psql": "postgresql",
    "pg": "postgresql",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "cv": "computer vision",
    "ds": "data science",
    "ai": "artificial intelligence",
    "genai": "generative ai",
    "llm": "large language models",
    "llms": "large language models",
    "tf": "tensorflow",
    "gcp": "google cloud",
    "gcs": "google cloud",
    "aws": "amazon web services",
    "az": "azure",
    "k8": "kubernetes",
    "ci/cd": "cicd",
    "ci cd": "cicd",
    "node": "nodejs",
    "node.js": "nodejs",
    "react.js": "react",
    "reactjs": "react",
    "next.js": "nextjs",
    "golang": "go",
    "c++": "cpp",
    "c#": "csharp",
    ".net": "dotnet",
    "rest api": "rest",
    "restful": "rest",
    "spark": "apache spark",
    "pyspark": "apache spark",
    "tf2": "tensorflow",
    "torch": "pytorch",
    "huggingface": "hugging face",
}

_RATIO_THRESHOLD = 0.9  # difflib similarity above which two skills are "the same"


def canonicalize(skill: str) -> str:
    """Lowercase, strip punctuation (keeping +/#), and apply the alias map."""
    s = (skill or "").strip().lower()
    if not s:
        return ""
    # Keep + and # (c++, c#) but normalize other punctuation to spaces.
    cleaned = re.sub(r"[^a-z0-9+#./ -]", " ", s)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    # Light singularization (skills, models → skill, model) — only for long words.
    if len(cleaned) > 4 and cleaned.endswith("s") and not cleaned.endswith("ss"):
        singular = cleaned[:-1]
        if singular in _ALIASES:
            return _ALIASES[singular]
    return cleaned


def _same_skill(a: str, b: str) -> bool:
    """True if canonical skills *a* and *b* mean the same thing."""
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens, b_tokens = set(a.split()), set(b.split())
    # Token-subset: "data science" matches "data science engineer".
    if a_tokens and b_tokens and (a_tokens <= b_tokens or b_tokens <= a_tokens):
        return True
    # Substring (guard very short strings to avoid "go" ⊂ "django").
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    # Fuzzy ratio for typos / minor variants.
    return SequenceMatcher(None, a, b).ratio() >= _RATIO_THRESHOLD


def skills_overlap(
    job_skills: list[str], profile_skills: list[str]
) -> tuple[list[str], list[str]]:
    """Return (matched, gaps) over the JOB's skills.

    ``matched`` = job skills the profile supports (fuzzy); ``gaps`` = the rest.
    Both are returned in the job's original wording (deduped, canonical-keyed).
    """
    prof_canon = [canonicalize(s) for s in profile_skills if s and canonicalize(s)]
    matched: list[str] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for raw in job_skills:
        jc = canonicalize(raw)
        if not jc or jc in seen:
            continue
        seen.add(jc)
        if any(_same_skill(jc, pc) for pc in prof_canon):
            matched.append(raw.strip())
        else:
            gaps.append(raw.strip())
    return matched, gaps
