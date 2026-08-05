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

_TECHNICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "code", "kode", "debug", "error", "bug", "fix", "refactor", "function",
        "fungsi", "class", "module", "api", "endpoint", "database", "schema",
        "migration", "test", "tes", "unittest", "pytest", "deploy", "build",
        "compile", "script", "python", "javascript", "typescript", "sql",
        "architecture", "arsitektur", "technical", "teknis", "repository",
        "git", "commit", "merge", "branch", "docker", "server", "backend",
        "frontend", "html", "css", "json", "yaml", "toml",
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_WORKFLOW = "GENERAL"


def classify_intent(message: str) -> ClassificationResult:
    """Classify a user message into a workflow ID deterministically.

    Priority order (first match wins):
      1. Fast patterns (very short / trivial messages)
      2. Technical keywords
      3. Google Workspace keywords
      4. Academic keywords
      5. Presentation keywords
      6. Strategy keywords
      7. Default → GENERAL
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

    # 2. Technical
    tech_hits = tokens & _TECHNICAL_KEYWORDS
    # also check multi-word technical phrases
    if tech_hits:
        confidence = "HIGH" if len(tech_hits) >= 2 else "MEDIUM"
        return ClassificationResult(
            workflow_id="TECHNICAL",
            confidence=confidence,
            matched_rule=f"technical_keywords:{','.join(sorted(tech_hits)[:3])}",
        )

    # 3. Google Workspace (multi-word first)
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

    # 4. Presentation (check before Academic so "slide deck for review" → PRESENTATION not ACADEMIC)
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

    # 5. Academic
    acad_hits = tokens & _ACADEMIC_KEYWORDS
    if acad_hits:
        confidence = "HIGH" if len(acad_hits) >= 2 else "MEDIUM"
        return ClassificationResult(
            workflow_id="ACADEMIC",
            confidence=confidence,
            matched_rule=f"academic_keywords:{','.join(sorted(acad_hits)[:3])}",
        )

    # 6. Strategy
    strat_hits = tokens & _STRATEGY_KEYWORDS
    if strat_hits:
        confidence = "HIGH" if len(strat_hits) >= 2 else "MEDIUM"
        return ClassificationResult(
            workflow_id="STRATEGY",
            confidence=confidence,
            matched_rule=f"strategy_keywords:{','.join(sorted(strat_hits)[:3])}",
        )

    # 7. Default
    return ClassificationResult(
        workflow_id=_DEFAULT_WORKFLOW,
        confidence="LOW",
        matched_rule="default_fallback",
    )
