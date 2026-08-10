"""Lightweight AI-text-detection ("humanization") metric suite for resumes.

A pure-Python port of the heavy `metrics_advanced.py` research suite, trimmed to the
families that need **no** torch / spaCy / sentence-transformers: readability, lexical
richness, character patterns, sentence structure, function-vs-content words,
repetition/diversity, AI-buzzword density, and a composite AI-risk rollup. Each family
is deterministic and side-effect free so the whole module is trivially unit-testable.

Optional deps degrade one family, never the module: `textstat` adds graded readability
scores (Flesch/FK/…); `nltk` stopwords sharpen the stopword ratio. When absent, those
metrics return ``None`` (readability) or fall back to a built-in function-word list.

Public surface:
    compute_metrics(text)          -> full bundle incl. ``composite``
    ai_risk(bundle)                -> {ai_risk_score, humanization_score, band, drivers, ...}
    delta(before, after)           -> flat before/after rows with a better/worse direction
"""

from __future__ import annotations

import contextlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from statistics import pstdev
from typing import Any

# ── Optional deps (graceful) ──────────────────────────────────────────────────
_textstat: Any
try:  # graded readability formulas
    import textstat as _textstat  # type: ignore[no-redef]
except Exception:  # pragma: no cover - environment dependent
    _textstat = None

try:  # richer stopword list
    from nltk.corpus import stopwords as _nltk_stopwords

    _STOPWORDS: set[str] = set(_nltk_stopwords.words("english"))
except Exception:  # pragma: no cover - environment dependent
    _STOPWORDS = set()


# ── Small pure-Python numeric helpers (avoid a numpy/scipy dependency) ─────────

def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    """Population standard deviation (matches numpy's default ``ddof=0``)."""
    return pstdev(values) if len(values) > 1 else 0.0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _entropy_bits(counts: list[int]) -> float:
    """Shannon entropy (bits) of a discrete distribution given raw counts."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    ent = 0.0
    for c in counts:
        if c:
            p = c / total
            ent -= p * math.log2(p)
    return ent


def _tokenize(text: str) -> list[str]:
    """Lowercase alpha word tokens."""
    return re.findall(r"\b[a-z]+\b", text.lower())


def _sentences(text: str) -> list[str]:
    """Split into sentences with >2 words (regex; no NLTK punkt dependency)."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 2]


# ── 1. Readability & surface stats ────────────────────────────────────────────

def readability_metrics(text: str) -> dict[str, Any]:
    """Surface stats always; graded readability scores when ``textstat`` is present."""
    words = _tokenize(text)
    sents = _sentences(text)
    n_tok = len(words)
    n_sent = max(len(sents), 1)
    wlens = [len(w) for w in words]
    slens = [len(s.split()) for s in sents]

    out: dict[str, Any] = {
        "avg_sentence_length": round(_safe_div(n_tok, n_sent), 2),
        "avg_word_length": round(_mean(wlens), 2),
        "sentence_count": len(sents),
        "word_count": n_tok,
        "char_count": sum(1 for c in text if c.isalpha()),
        "sent_len_std": round(_std(slens), 2),
        "type_token_ratio": round(_safe_div(len(set(words)), max(n_tok, 1)), 4),
        # textstat-only; None when the optional dep is missing.
        "flesch_reading_ease": None,
        "flesch_kincaid_grade": None,
        "gunning_fog": None,
        "smog_index": None,
        "coleman_liau": None,
        "dale_chall": None,
    }
    if _textstat is not None and text.strip():
        # textstat can raise on pathological tiny inputs; a failure just leaves the
        # graded scores at None rather than losing the whole family.
        with contextlib.suppress(Exception):  # pragma: no cover - dep edge cases
            out.update({
                "flesch_reading_ease": round(_textstat.flesch_reading_ease(text), 2),
                "flesch_kincaid_grade": round(_textstat.flesch_kincaid_grade(text), 2),
                "gunning_fog": round(_textstat.gunning_fog(text), 2),
                "smog_index": round(_textstat.smog_index(text), 2),
                "coleman_liau": round(_textstat.coleman_liau_index(text), 2),
                "dale_chall": round(_textstat.dale_chall_readability_score(text), 2),
            })
    return out


# ── 2. Lexical richness (stylometry) ──────────────────────────────────────────

def _mtld(tokens: list[str], threshold: float = 0.720) -> float:
    """Measure of Textual Lexical Diversity (bidirectional mean)."""
    def _forward(toks: list[str]) -> float:
        factors, n, types = 0.0, 0, set()
        for t in toks:
            types.add(t)
            n += 1
            if len(types) / n <= threshold:
                factors += 1
                types.clear()
                n = 0
        if n > 0:
            factors += (1 - len(types) / n) / (1 - threshold)
        return len(toks) / factors if factors else len(toks)

    if len(tokens) < 10:
        return 0.0
    return round((_forward(tokens) + _forward(list(reversed(tokens)))) / 2, 2)


def _yule_k(tokens: list[str]) -> float:
    """Yule's K — repetition-weighted vocabulary concentration."""
    freq = Counter(tokens)
    ff = Counter(freq.values())
    n = len(tokens)
    if n == 0:
        return 0.0
    sigma = sum(r * r * vr for r, vr in ff.items())
    return round(10000 * (sigma - n) / (n * n), 4)


def _maas_a2(tokens: list[str]) -> float:
    """Maas's a^2 (log type-token measure; stable across lengths)."""
    n, v = len(tokens), len(set(tokens))
    if n <= 1 or v <= 1:
        return 0.0
    ln = math.log(n)
    return round((ln - math.log(v)) / (ln ** 2), 6) if ln else 0.0


def lexical_richness_metrics(text: str) -> dict[str, Any]:
    """Type-token ratio, MTLD, Yule's K, Maas, hapax/dis-legomena, Herdan's C."""
    toks = _tokenize(text)
    n, v = len(toks), len(set(toks))
    freq = Counter(toks)
    hapax = sum(1 for c in freq.values() if c == 1)
    return {
        "token_count": n,
        "type_count": v,
        "ttr": round(_safe_div(v, n), 4),
        "mtld": _mtld(toks),
        "yule_k": _yule_k(toks),
        "maas_a2": _maas_a2(toks),
        "hapax_rate": round(_safe_div(hapax, v), 4),
        "dis_legomena_rate": round(_safe_div(sum(1 for c in freq.values() if c == 2), v), 4),
        "richness_index": round(_safe_div(v, math.sqrt(n)) if n > 0 else 0, 4),
    }


# ── 3. Character-level patterns ───────────────────────────────────────────────

def _char_ngram_entropy(text: str, n: int = 3) -> float:
    """Shannon entropy (bits) of character n-grams."""
    ngrams = [text[i:i + n] for i in range(len(text) - n + 1)]
    if not ngrams:
        return 0.0
    return round(_entropy_bits(list(Counter(ngrams).values())), 4)


def character_metrics(text: str) -> dict[str, Any]:
    """Digit/symbol/cap/whitespace ratios, char n-gram entropy, long-word rate."""
    total = len(text)
    letters = [c for c in text if c.isalpha()]
    upper = [c for c in letters if c.isupper()]
    digits = [c for c in text if c.isdigit()]
    punct = [c for c in text if c in r""".,;:!?'"()-–—"""]
    spaces = [c for c in text if c.isspace()]
    wlens = [len(w.strip(".,;:!?\"'()-")) for w in text.split() if w.strip(".,;:!?\"'()-")]
    return {
        "digit_ratio": round(_safe_div(len(digits), total), 4),
        "symbol_ratio": round(_safe_div(len(punct), total), 4),
        "capitalization_ratio": round(_safe_div(len(upper), max(len(letters), 1)), 4),
        "whitespace_ratio": round(_safe_div(len(spaces), total), 4),
        "avg_word_len_chars": round(_mean(wlens), 2),
        "char_entropy_3gram": _char_ngram_entropy(text.lower(), 3),
        "char_entropy_4gram": _char_ngram_entropy(text.lower(), 4),
        "long_word_rate": round(_safe_div(sum(1 for w in wlens if w > 8), max(len(wlens), 1)), 4),
    }


# ── 4. Sentence / paragraph structure ─────────────────────────────────────────

def sentence_structure_metrics(text: str) -> dict[str, Any]:
    """Sentence-length distribution + Goh-Barabasi structural burstiness."""
    sents = _sentences(text)
    slens = [len(s.split()) for s in sents] or [0]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    plens = [len(p.split()) for p in paragraphs] or [0]

    mean_s, std_s = _mean(slens), _std(slens)
    n = len(slens)
    pct_short = _safe_div(sum(1 for x in slens if x < 8), n)
    pct_long = _safe_div(sum(1 for x in slens if x > 25), n)
    burst = (std_s - mean_s) / (std_s + mean_s) if (std_s + mean_s) > 0 else 0.0

    return {
        "sentence_count": len(sents),
        "avg_sent_len": round(mean_s, 2),
        "sent_len_std": round(std_s, 2),
        "sent_len_min": int(min(slens)),
        "sent_len_max": int(max(slens)),
        "sent_len_cv": round(_safe_div(std_s, mean_s), 4),
        "pct_short_sents": round(pct_short, 4),
        "pct_medium_sents": round(1 - pct_short - pct_long, 4),
        "pct_long_sents": round(pct_long, 4),
        "structural_burstiness": round(burst, 4),
        "paragraph_count": len(paragraphs),
        "avg_para_len": round(_mean(plens), 2),
        "para_len_std": round(_std(plens), 2),
    }


# ── 5. Function vs content words ──────────────────────────────────────────────

_FUNCTION_WORDS = {
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours",
    "yourself", "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its",
    "itself", "they", "them", "their", "theirs", "themselves", "what", "which", "who", "whom",
    "this", "that", "these", "those", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may",
    "might", "must", "can", "could", "the", "a", "an", "and", "but", "if", "or", "because",
    "as", "until", "while", "of", "at", "by", "for", "with", "about", "against", "between",
    "through", "during", "before", "after", "above", "below", "to", "from", "in", "out",
    "on", "off", "over", "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "not", "only", "own", "same", "so", "than", "too", "very",
}


def function_content_word_metrics(text: str) -> dict[str, Any]:
    """Function/content/stopword ratios + the top closed-class words."""
    toks = _tokenize(text)
    n = max(len(toks), 1)
    fn = sum(1 for t in toks if t in _FUNCTION_WORDS)
    sw = sum(1 for t in toks if t in _STOPWORDS) if _STOPWORDS else fn
    content = len(toks) - fn
    top_fn = dict(Counter(t for t in toks if t in _FUNCTION_WORDS).most_common(5))
    return {
        "function_word_ratio": round(fn / n, 4),
        "content_word_ratio": round(content / n, 4),
        "stopword_ratio": round(sw / n, 4),
        "fn_content_balance": round(_safe_div(fn, content), 4),
        "top_function_words": top_fn,
    }


# ── 6. Repetition & diversity ─────────────────────────────────────────────────

def _ngrams(toks: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]


def repetition_diversity_metrics(text: str) -> dict[str, Any]:
    """Distinct-n, n-gram repetition rates, unigram entropy, cross-sentence overlap."""
    toks = _tokenize(text)
    bi, tri = _ngrams(toks, 2), _ngrams(toks, 3)
    d1 = _safe_div(len(set(toks)), max(len(toks), 1))
    d2 = _safe_div(len(set(bi)), max(len(bi), 1))
    d3 = _safe_div(len(set(tri)), max(len(tri), 1))
    bi_rep = _safe_div(len(bi) - len(set(bi)), max(len(bi), 1))
    tri_rep = _safe_div(len(tri) - len(set(tri)), max(len(tri), 1))
    nt_ent = round(_entropy_bits(list(Counter(toks).values())), 4) if toks else 0.0

    sents = _sentences(text)
    sent_4gs = [set(_ngrams(_tokenize(s), 4)) for s in sents]
    overlap = sum(
        1 for i, a in enumerate(sent_4gs)
        for j, b in enumerate(sent_4gs)
        if i != j and a & b
    )
    overlap_rate = _safe_div(overlap, max(len(sents) * (len(sents) - 1), 1))

    return {
        "distinct_1": round(d1, 4),
        "distinct_2": round(d2, 4),
        "distinct_3": round(d3, 4),
        "bigram_rep_rate": round(bi_rep, 4),
        "trigram_rep_rate": round(tri_rep, 4),
        "next_token_entropy": nt_ent,
        "sent_4gram_overlap": round(overlap_rate, 4),
    }


# ── 7. AI-buzzword density ────────────────────────────────────────────────────

_AI_BUZZWORDS = [
    "leverage", "leveraging", "leveraged", "utilize", "utilizing", "utilized",
    "spearhead", "spearheading", "spearheaded", "champion", "championing",
    "synergy", "synergize", "streamline", "streamlining", "robust", "cutting-edge",
    "state-of-the-art", "holistic", "seamless", "seamlessly", "transformative",
    "paradigm", "ecosystem", "bandwidth", "impactful", "actionable", "scalable",
    "best-in-class", "game-changing", "disruptive", "empower", "empowering",
    "facilitate", "overarching", "granular", "thought leadership", "orchestrate",
]
_BUZZWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _AI_BUZZWORDS) + r")\b", re.I
)


def buzzword_metrics(text: str) -> dict[str, Any]:
    """AI-buzzword count and density (hits per 100 words)."""
    count = len(_BUZZWORD_RE.findall(text))
    words = len(text.split())
    return {
        "buzzword_count": count,
        "ai_buzzword_density": round(_safe_div(count * 100, max(words, 1)), 4),
    }


# ── 8. Composite AI-detection risk ────────────────────────────────────────────

# Sub-score weights (renormalized over whichever signals are present). Higher
# sub-score = more AI-like. Mirrors the research suite's intent, restricted to the
# lightweight families (no LM/POS/embedding sub-scores).
_RISK_WEIGHTS = {
    "lexical_uniformity": 0.16,
    "low_diversity": 0.12,
    "repetition": 0.12,
    "complexity": 0.08,      # dropped when textstat is unavailable
    "low_burstiness": 0.16,
    "uniform_sentences": 0.16,
    "fn_word_deviation": 0.10,
    "buzzword": 0.14,
}


def ai_risk(bundle: dict[str, Any]) -> dict[str, Any]:
    """Roll a metric bundle into a 0–100 AI-risk score (higher = more AI-like).

    ``humanization_score`` is the inverse. ``band`` maps risk to a dataviz status
    tier (good/warning/serious); ``drivers`` are the three sub-scores pushing risk
    up the most. Any sub-score whose source family is missing is dropped and the
    remaining weights renormalized, so a missing optional dep never skews the score.
    """
    rd = bundle.get("readability", {})
    lx = bundle.get("lexical", {})
    rep = bundle.get("repetition", {})
    ss = bundle.get("structure", {})
    fc = bundle.get("function_content", {})
    bz = bundle.get("buzzword", {})

    scores: dict[str, float] = {}
    mtld = lx.get("mtld") or 0
    scores["lexical_uniformity"] = max(0.0, 100 - min(mtld / 2, 100))
    d2 = rep.get("distinct_2")
    if d2 is not None:
        scores["low_diversity"] = max(0.0, (1 - d2) * 100)
    scores["repetition"] = min(100.0, (rep.get("trigram_rep_rate") or 0) * 500)
    fk = rd.get("flesch_kincaid_grade")
    if fk is not None:  # only when textstat is present
        scores["complexity"] = min(100.0, max(0.0, (fk - 10) * 8))
    sb = ss.get("structural_burstiness")
    if sb is not None:
        scores["low_burstiness"] = max(0.0, 100 - (sb + 1) * 50)
    cv = ss.get("sent_len_cv")
    if cv is not None:
        scores["uniform_sentences"] = max(0.0, 100 - min(cv * 200, 100))
    fnr = fc.get("function_word_ratio")
    if fnr is not None:
        scores["fn_word_deviation"] = max(0.0, 100 - abs(fnr - 0.30) * 400)
    scores["buzzword"] = min(100.0, (bz.get("ai_buzzword_density") or 0) * 25)

    present = {k: v for k, v in scores.items() if k in _RISK_WEIGHTS}
    wsum = sum(_RISK_WEIGHTS[k] for k in present) or 1.0
    risk = sum(_RISK_WEIGHTS[k] * present[k] for k in present) / wsum
    drivers = [
        {"factor": k, "score": round(v, 2)}
        for k, v in sorted(present.items(), key=lambda kv: kv[1], reverse=True)[:3]
    ]
    band = "good" if risk < 35 else "warning" if risk < 60 else "serious"
    return {
        "ai_risk_score": round(risk, 2),
        "humanization_score": round(100 - risk, 2),
        "band": band,
        "drivers": drivers,
        "sub_scores": {k: round(v, 2) for k, v in scores.items()},
    }


# ── Master compute + before/after delta ───────────────────────────────────────

def compute_metrics(text: str) -> dict[str, Any]:
    """Run every lightweight family and attach the composite AI-risk rollup."""
    if not text.strip():
        return {}
    bundle: dict[str, Any] = {
        "readability": readability_metrics(text),
        "lexical": lexical_richness_metrics(text),
        "character": character_metrics(text),
        "structure": sentence_structure_metrics(text),
        "function_content": function_content_word_metrics(text),
        "repetition": repetition_diversity_metrics(text),
        "buzzword": buzzword_metrics(text),
    }
    bundle["composite"] = ai_risk(bundle)
    return bundle


_FAMILY_LABELS = [
    ("composite", "Composite scores"),
    ("readability", "Readability & surface"),
    ("lexical", "Lexical richness"),
    ("character", "Character patterns"),
    ("structure", "Sentence structure"),
    ("function_content", "Function vs content words"),
    ("repetition", "Repetition & diversity"),
    ("buzzword", "AI-buzzword density"),
]
# Metrics where a higher value is more human-like (good).
_HIGHER_BETTER = {
    "flesch_reading_ease", "mtld", "ttr", "type_token_ratio", "distinct_1",
    "distinct_2", "distinct_3", "next_token_entropy", "richness_index",
    "structural_burstiness", "sent_len_cv", "humanization_score", "sent_len_std",
    "char_entropy_3gram", "char_entropy_4gram",
}
# Metrics where a lower value is more human-like (good).
_LOWER_BETTER = {
    "flesch_kincaid_grade", "gunning_fog", "smog_index", "coleman_liau",
    "dale_chall", "ai_risk_score", "hapax_rate", "bigram_rep_rate",
    "trigram_rep_rate", "sent_4gram_overlap", "yule_k", "ai_buzzword_density",
    "buzzword_count",
}
_SKIP_KEYS = {"top_function_words", "drivers", "sub_scores", "band"}


def _direction(key: str, b: Any, a: Any) -> str:
    if not isinstance(b, (int, float)) or not isinstance(a, (int, float)):
        return "neutral"
    if abs(a - b) < 1e-6:
        return "neutral"
    if key in _HIGHER_BETTER:
        return "better" if a > b else "worse"
    if key in _LOWER_BETTER:
        return "better" if a < b else "worse"
    return "neutral"


def delta(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten two bundles into before/after rows with a better/worse direction."""
    rows: list[dict[str, Any]] = []
    for fam_key, fam_label in _FAMILY_LABELS:
        bfam, afam = before.get(fam_key, {}), after.get(fam_key, {})
        for k in sorted(set(bfam) | set(afam)):
            if k in _SKIP_KEYS:
                continue
            bv, av = bfam.get(k), afam.get(k)
            if not isinstance(bv, (int, float)) and not isinstance(av, (int, float)):
                continue
            d = None
            if isinstance(bv, (int, float)) and isinstance(av, (int, float)):
                d = round(av - bv, 4)
            rows.append({
                "family": fam_label,
                "metric": k,
                "before": bv,
                "after": av,
                "delta": d,
                "direction": _direction(k, bv, av),
            })
    return rows
