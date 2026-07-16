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
from functools import lru_cache

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
    "sb3": "stable-baselines3",
    "rl": "reinforcement learning",
    "powerbi": "power bi",
    "power-bi": "power bi",
    "gen ai": "generative ai",
    "rag": "rag pipelines",
    "llmops": "large language models",
    "sklearn pipelines": "scikit-learn",
    "ms sql": "sql server",
    "postgres sql": "postgresql",
    "etl pipelines": "etl",
    "data pipelines": "etl",
    "ci-cd": "cicd",
    "github actions": "cicd",
    "sagemaker": "amazon web services",
    "databricks": "apache spark",
}

# Umbrella JD skills → concrete profile skills that demonstrate them (canonical
# forms). One-directional on purpose: having pytorch implies "machine learning",
# but having "machine learning" does NOT imply pytorch — gaps for concrete tools
# stay honest. Fixes e.g. "Gap: machine learning" on an ML-engineer JD for a
# profile listing pytorch/tensorflow/scikit-learn.
_IMPLIED_BY: dict[str, frozenset[str]] = {
    "machine learning": frozenset({
        "pytorch", "tensorflow", "keras", "scikit-learn", "xgboost", "cnn",
        "deep learning", "computer vision", "natural language processing",
        "reinforcement learning", "stable-baselines3",
    }),
    "deep learning": frozenset({"pytorch", "tensorflow", "keras", "cnn"}),
    "artificial intelligence": frozenset({
        "machine learning", "deep learning", "large language models",
        "generative ai", "pytorch", "tensorflow",
    }),
    "data science": frozenset({
        "pandas", "numpy", "scikit-learn", "machine learning", "statistics", "r",
    }),
    "generative ai": frozenset({
        "large language models", "langchain", "claude api", "openai api",
        "fine-tuning", "rag pipelines",
    }),
    "large language models": frozenset({
        "langchain", "claude api", "openai api", "rag pipelines",
        "prompt engineering", "generative ai",
    }),
    "cloud": frozenset({"amazon web services", "azure", "google cloud", "docker", "vercel"}),
    "reinforcement learning": frozenset({
        "stable-baselines3", "gymnasium", "ppo", "q-learning",
    }),
    "computer vision": frozenset({"opencv", "cnn", "resnet", "albumentations", "pytorch"}),
    "natural language processing": frozenset({
        "large language models", "transformers", "hugging face", "spacy", "nltk",
    }),
    "etl": frozenset({"apache spark", "pandas", "airflow", "clickhouse", "sql"}),
    "transfer learning": frozenset({"pytorch", "tensorflow", "keras", "resnet", "hugging face"}),
    "mlops": frozenset({"docker", "cicd", "github actions", "mlflow"}),
    "statistics": frozenset({
        "hypothesis testing", "regression", "r", "statistical modeling",
    }),
    "data visualization": frozenset({
        "tableau", "power bi", "matplotlib", "seaborn", "plotly",
    }),
}

_RATIO_THRESHOLD = 0.9  # difflib similarity above which two skills are "the same"


@lru_cache(maxsize=20_000)
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


@lru_cache(maxsize=200_000)
def _same_skill(a: str, b: str) -> bool:
    """True if canonical skills *a* and *b* mean the same thing.

    Memoized: scoring a 500-job candidate window compares the same
    profile-skill × job-skill pairs thousands of times, and the difflib
    similarity check dominated verdict latency (~8.5s per window) without it.
    """
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
        elif any(
            _same_skill(pc, implied)
            for implied in _IMPLIED_BY.get(jc, ())
            for pc in prof_canon
        ):
            # Umbrella JD term demonstrated by a concrete profile skill.
            matched.append(raw.strip())
        else:
            gaps.append(raw.strip())
    return matched, gaps


def profile_skills_mentioned_in_text(
    text: str | None, profile_skills: list[str]
) -> list[str]:
    """Return verified profile skills explicitly named in a job description.

    This is a deterministic fallback for legacy/new jobs whose enrichment did
    not produce a structured ``job.skills`` list. It searches only skills the
    profile already supports, including curated aliases, so it cannot invent
    candidate evidence. Very short ambiguous names (``R``, ``Go``) are skipped
    unless a richer structured extraction supplies them.
    """
    haystack = (text or "").casefold()
    if not haystack:
        return []

    inverse_aliases: dict[str, set[str]] = {}
    for alias, canonical in _ALIASES.items():
        inverse_aliases.setdefault(canonical, set()).add(alias)

    found: list[str] = []
    seen: set[str] = set()
    for raw in profile_skills:
        canonical = canonicalize(raw)
        if not canonical or canonical in seen:
            continue
        candidates = {raw.strip().casefold(), canonical, *inverse_aliases.get(canonical, set())}
        mentioned = False
        for candidate in candidates:
            # One/two-character language names are too ambiguous in prose;
            # structured LLM extraction can still represent them accurately.
            if len(re.sub(r"[^a-z0-9+#]", "", candidate)) < 3:
                continue
            pattern = rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])"
            if re.search(pattern, haystack):
                mentioned = True
                break
        if mentioned:
            found.append(raw.strip())
            seen.add(canonical)
    return found[:20]
