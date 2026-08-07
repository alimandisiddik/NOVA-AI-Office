"""Deterministic intent-to-workflow classifier for the NOVA model router.

Classification is rule-based using keyword matching and regex.
No external AI provider is called.  The classifier returns a workflow_id
from the registry and a confidence label (HIGH / MEDIUM / LOW).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationResult:
    """The outcome of a deterministic intent classification."""

    workflow_id: str
    confidence: str          # HIGH | MEDIUM | LOW
    matched_rule: str        # human-readable label for traceability


# ---------------------------------------------------------------------------
# Keyword sets  (all lowercase)
# ---------------------------------------------------------------------------
#
# Technical vocabulary is split into two tiers (Sprint 5G.2):
#
# A. ACTION signals  — explicit technical execution verbs.  Their presence
#    always means TECHNICAL: nobody says "debug the function" or "perbaiki
#    fungsi ini" without meaning it.
#
# B. DOMAIN terms    — technical nouns (function/fungsi, database, api, ...).
#    A domain noun on its own is a weak signal: "Jelaskan fungsi database"
#    is an informational question, not a request to touch a database.  A
#    domain term still routes to TECHNICAL by default (preserves prior
#    behaviour for messages like "review the architecture of the backend"),
#    *unless* the message also carries an explanatory/informational pattern
#    (see _EXPLANATORY_PATTERNS below), in which case the domain-only match
#    is suppressed and classification falls through to the remaining
#    categories / GENERAL.

_TECHNICAL_ACTION_KEYWORDS: frozenset[str] = frozenset(
    {
        "fix", "perbaiki", "debug", "implement", "refactor", "deploy",
        "compile", "test", "tes", "uji", "unittest", "pytest", "migrate",
        "commit", "merge", "build", "code", "kode", "kodekan", "develop",
    }
)

_TECHNICAL_DOMAIN_KEYWORDS: frozenset[str] = frozenset(
    {
        "error", "bug", "function", "fungsi", "class", "module", "api",
        "endpoint", "database", "schema", "migration", "script", "python",
        "javascript", "typescript", "sql", "architecture", "arsitektur",
        "technical", "teknis", "repository", "git", "branch", "docker",
        "server", "backend", "frontend", "html", "css", "json", "yaml",
        "toml",
    }
)

_STRATEGY_KEYWORDS: frozenset[str] = frozenset(
    {
        "strategy", "strategi", "plan", "rencana", "goal", "tujuan", "objective",
        "okr", "kpi", "priority", "prioritas", "roadmap", "vision", "visi",
        "mission", "misi", "decision", "keputusan", "executive", "eksekutif",
        "leadership", "quarterly", "annual", "tahunan", "growth", "pertumbuhan",
    }
)

_GOOGLE_WORKSPACE_KEYWORDS: frozenset[str] = frozenset(
    {
        "google drive", "gdrive", "drive", "google docs", "docs", "sheets",
        "google sheets", "gmail", "email", "calendar", "google calendar",
        "slides", "google slides", "forms", "meet", "google meet",
        "google workspace", "dokumen", "spreadsheet", "kalender",
    }
)

_PRESENTATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "presentation", "presentasi", "slide", "deck", "powerpoint", "keynote",
        "speaker notes", "catatan pembicara", "visual brief", "infographic",
        "infografis", "pitch", "pitchdeck",
    }
)

_ACADEMIC_KEYWORDS: frozenset[str] = frozenset(
    {
        "research", "riset", "penelitian", "academic", "akademik", "paper",
        "jurnal", "journal", "literature", "literatur", "citation", "sitasi",
        "reference", "referensi", "thesis", "tesis", "dissertation",
        "hypothesis", "hipotesis", "methodology", "metodologi", "abstract",
        "abstrak", "review", "synthesis", "sintesis",
    }
)

_FAST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*(?:yes|no|ya|tidak|true|false|ok|oke)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:what(?:'s| is)?|apa)\s+(?:the\s+)?(?:time|date|waktu|tanggal)\s*\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:ping|status|check)\s*\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*\S+\s*\??\s*$"),   # single-word messages only
]

# Informational/explanatory intent markers.  When one of these is present
# alongside only a *weak* single-keyword domain/topic match (technical
# domain noun, or a lone strategy noun), the weak match is not strong
# enough evidence of an execution/strategy request and is suppressed in
# favour of GENERAL.  Concrete multi-word operations (Google Workspace,
# Presentation, Academic phrase/keyword matches) are unaffected — those
# remain genuine domain signals even in an explanatory sentence.
_EXPLANATORY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bjelaskan\b", re.IGNORECASE),
    re.compile(r"\bapa itu\b", re.IGNORECASE),
    re.compile(r"\bapa fungsi\b", re.IGNORECASE),
    re.compile(r"\bbagaimana cara kerja\b", re.IGNORECASE),
    re.compile(r"\bexplain\b", re.IGNORECASE),
    re.compile(r"\bwhat is\b", re.IGNORECASE),
    re.compile(r"\bwhat does\b", re.IGNORECASE),
    re.compile(r"\bdescribe\b", re.IGNORECASE),
]


def _is_explanatory(text: str) -> bool:
    return any(pattern.search(text) for pattern in _EXPLANATORY_PATTERNS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_WORKFLOW = "GENERAL"


def classify_intent(message: str) -> ClassificationResult:
    """Classify a user message into a workflow ID deterministically.

    Priority order (first match wins):
      1. Fast patterns (very short / trivial messages)
      2. Technical action keywords (explicit execution verbs) → TECHNICAL
      3. Technical domain keywords → TECHNICAL, unless the message is
         explanatory/informational AND only a single weak domain keyword
         matched (see _EXPLANATORY_PATTERNS) — that combination falls
         through instead.
      4. Google Workspace keywords
      5. Presentation keywords
      6. Academic keywords
      7. Strategy keywords → STRATEGY, with the same single-weak-keyword +
         explanatory-pattern fallthrough as step 3.
      8. Default → GENERAL (informational fallback carries MEDIUM
         confidence if step 3/7 suppressed a weak match; LOW otherwise).
    """
    if not message or not message.strip():
        return ClassificationResult(
            workflow_id="FAST",
            confidence="HIGH",
            matched_rule="empty_message",
        )

    text = message.lower().strip()

    # 1. Fast patterns
    for pattern in _FAST_PATTERNS:
        if pattern.match(message):
            return ClassificationResult(
                workflow_id="FAST",
                confidence="HIGH",
                matched_rule=f"fast_pattern:{pattern.pattern[:40]}",
            )

    tokens = set(re.findall(r"[a-z][a-z0-9_\-]*", text))
    explanatory = _is_explanatory(text)
    suppressed_hits: set[str] = set()

    # 2. Technical — explicit action verbs always win.
    action_hits = tokens & _TECHNICAL_ACTION_KEYWORDS
    if action_hits:
        combined = action_hits | (tokens & _TECHNICAL_DOMAIN_KEYWORDS)
        confidence = "HIGH" if len(combined) >= 2 else "MEDIUM"
        return ClassificationResult(
            workflow_id="TECHNICAL",
            confidence=confidence,
            matched_rule=f"technical_action:{','.join(sorted(combined)[:3])}",
        )

    # 3. Technical — domain nouns, guarded against informational phrasing.
    domain_hits = tokens & _TECHNICAL_DOMAIN_KEYWORDS
    if domain_hits:
        if not explanatory:
            confidence = "HIGH" if len(domain_hits) >= 2 else "MEDIUM"
            return ClassificationResult(
                workflow_id="TECHNICAL",
                confidence=confidence,
                matched_rule=f"technical_domain:{','.join(sorted(domain_hits)[:3])}",
            )
        suppressed_hits |= domain_hits

    # 4. Google Workspace (multi-word first)
    gws_hits: list[str] = []
    for phrase in sorted(_GOOGLE_WORKSPACE_KEYWORDS, key=len, reverse=True):
        if phrase in text:
            gws_hits.append(phrase)
            break
    if not gws_hits:
        gws_hits = list(tokens & _GOOGLE_WORKSPACE_KEYWORDS)
    if gws_hits:
        return ClassificationResult(
            workflow_id="GOOGLE_WORKSPACE",
            confidence="HIGH",
            matched_rule=f"google_workspace_keywords:{','.join(gws_hits[:3])}",
        )

    # 5. Presentation (check before Academic so "slide deck for review" → PRESENTATION not ACADEMIC)
    pres_hits: list[str] = []
    for phrase in sorted(_PRESENTATION_KEYWORDS, key=len, reverse=True):
        if phrase in text:
            pres_hits.append(phrase)
            break
    if not pres_hits:
        pres_hits = list(tokens & _PRESENTATION_KEYWORDS)
    if pres_hits:
        return ClassificationResult(
            workflow_id="PRESENTATION",
            confidence="HIGH",
            matched_rule=f"presentation_keywords:{','.join(pres_hits[:3])}",
        )

    # 6. Academic
    acad_hits = tokens & _ACADEMIC_KEYWORDS
    if acad_hits:
        confidence = "HIGH" if len(acad_hits) >= 2 else "MEDIUM"
        return ClassificationResult(
            workflow_id="ACADEMIC",
            confidence=confidence,
            matched_rule=f"academic_keywords:{','.join(sorted(acad_hits)[:3])}",
        )

    # 7. Strategy — same weak-single-keyword + explanatory guard as step 3.
    strat_hits = tokens & _STRATEGY_KEYWORDS
    if strat_hits:
        if len(strat_hits) >= 2 or not explanatory:
            confidence = "HIGH" if len(strat_hits) >= 2 else "MEDIUM"
            return ClassificationResult(
                workflow_id="STRATEGY",
                confidence=confidence,
                matched_rule=f"strategy_keywords:{','.join(sorted(strat_hits)[:3])}",
            )
        suppressed_hits |= strat_hits

    # 8. Default
    if suppressed_hits:
        return ClassificationResult(
            workflow_id=_DEFAULT_WORKFLOW,
            confidence="MEDIUM",
            matched_rule=(
                "informational_override:"
                f"{','.join(sorted(suppressed_hits)[:3])}"
            ),
        )

    return ClassificationResult(
        workflow_id=_DEFAULT_WORKFLOW,
        confidence="LOW",
        matched_rule="default_fallback",
    )
