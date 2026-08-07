# Sprint 5G.2 — Intent Classification Runtime Fix

## Status: Implementation under review / not yet merged. Not committed, not
pushed. See §9 for exact validation evidence.

## 1. Runtime defect

Confirmed Telegram reproduction:

```
"Jelaskan dalam 3 kalimat apa fungsi Executive Control Tower di NOVA."
```

classified as:

```
workflow: TECHNICAL
roles:    ['TECHNICAL_ARCHITECT', 'EXECUTION_WORKER']
risk:     LOW
```

which routed `/ask` into the review provider chain
(`nova-v1-review` → upstream review combo) and failed with HTTP 429, because
no second evidenced review-provider upstream route exists (Sprint 5G.1
scope). Direct classifier reproduction (`classify_intent(...)`, no
Telegram, no network) confirmed the same misclassification in isolation —
this is a pure classification defect, not a provider or transport issue.

## 2. Root cause

`app/router/classifier.py`'s `_TECHNICAL_KEYWORDS` set included `"fungsi"`
("function") as an undifferentiated technical keyword. `classify_intent()`
used first-match-wins precedence with Technical evaluated before Strategy
and the GENERAL default. Any message containing the single token `"fungsi"`
— including a purely explanatory question *about* a concept — was
classified TECHNICAL with MEDIUM confidence, regardless of whether any
technical execution was actually requested.

The flat keyword set had no way to distinguish:

- "Debug fungsi generate_text." (an execution request), from
- "Jelaskan fungsi database." (an information request).

## 3. Classification design

Technical vocabulary is split into two tiers:

**A. ACTION signals** (`_TECHNICAL_ACTION_KEYWORDS`) — explicit execution
verbs: `fix`, `perbaiki`, `debug`, `implement`, `refactor`, `deploy`,
`compile`, `test`, `tes`, `uji`, `unittest`, `pytest`, `migrate`, `commit`,
`merge`, `build`, `code`, `kode`, `kodekan`, `develop`. Presence of any
action verb routes to TECHNICAL unconditionally — nobody types "debug the
function" or "perbaiki fungsi ini" without meaning it, and this holds even
inside an explanatory sentence (`"Jelaskan cara debug fungsi
generate_text."` → TECHNICAL, verified by test).

**B. DOMAIN terms** (`_TECHNICAL_DOMAIN_KEYWORDS`) — technical nouns:
`function`/`fungsi`, `database`, `schema`, `api`, `endpoint`, `class`,
`module`, `architecture`/`arsitektur`, `error`, `bug`, `git`, `docker`,
`server`, `backend`, `frontend`, language/format names (`python`, `sql`,
`html`, …), etc. A domain noun is a **weak** signal on its own — it still
routes to TECHNICAL by default (preserving all prior behaviour, e.g.
`"Review the architecture of the backend"` stays TECHNICAL) — *unless* the
message also matches an explanatory/informational pattern, in which case
the domain-only match is suppressed and classification continues through
the remaining categories.

**Explanatory/informational markers** (`_EXPLANATORY_PATTERNS`, regex,
word-boundary anchored): `jelaskan`, `apa itu`, `apa fungsi`, `bagaimana
cara kerja`, `explain`, `what is`, `what does`, `describe`. These do not
create a new top-level category; they only gate the two *weak,
single-keyword-or-more* domain paths described above and in §4. They have
no effect on ACTION-keyword matches, and no effect on Google Workspace,
Presentation, or Academic matches — those remain concrete phrase/domain
signals regardless of explanatory phrasing (e.g. `"Jelaskan cara mengirim
email lewat Gmail"` stays GOOGLE_WORKSPACE; `"Jelaskan slide ini"` stays
PRESENTATION; `"Jelaskan metodologi penelitian"` stays ACADEMIC — all
verified by test).

No keyword was removed from technical vocabulary. `"fungsi"`/`"function"`
remain full technical-domain signals; only their *unconditional* strength
changed from "always TECHNICAL" to "TECHNICAL unless the sentence is
itself asking what the thing is."

## 4. Precedence — exact behaviour

Fast → Technical (action, unconditional) → Technical (domain, explanatory
-guarded) → Google Workspace → Presentation → Academic → Strategy
(keyword-count/explanatory-guarded) → General.

This is the same top-level ordering as before (Fast, Technical, Google
Workspace, Presentation, Academic, Strategy, General); nothing was
reordered. Two of the seven stages gained an internal guard clause:

- **Technical domain stage**: a domain-noun match is honoured immediately
  (`TECHNICAL`) unless the message is explanatory, in which case it falls
  through instead of returning.
- **Strategy stage**: unchanged for ≥2 keyword hits or non-explanatory
  phrasing (`"Buat strategi NOVA 2027."` → STRATEGY, single hit, no
  explanatory marker). A *single* weak strategy keyword combined with an
  explanatory marker falls through instead of returning — this is what
  fixes the "Executive Control Tower" reproduction: `"executive"` is a
  single-keyword `_STRATEGY_KEYWORDS` match, and without this guard the
  message would fall through Technical (correctly suppressed) only to be
  wrongly caught by Strategy on the way to General.

If either guard fires, the eventual GENERAL fallback carries **MEDIUM**
confidence and a `matched_rule` of `informational_override:<keywords>`,
distinguishing it from the pre-existing plain default fallback
(`default_fallback`, LOW confidence, used when no signal matched at all —
unchanged, verified by
`test_plain_fallback_still_has_low_confidence`).

### Strategy explanatory-phrasing policy (required decision point)

Per the sprint brief, `"Jelaskan strategi NOVA 2027."` may deterministically
resolve to either STRATEGY or GENERAL — this implementation chooses
**GENERAL**. Rationale: the same explanatory-marker guard that fixes the
confirmed technical-noun defect applies symmetrically to the
single-weak-strategy-keyword case, for the same reason — "explain the
strategy" is asking *about* a concept, not requesting strategic work be
done. `"Buat strategi NOVA 2027."` (no explanatory marker, action verb
`"buat"`) is unaffected and still resolves STRATEGY. `"What is our vision
and mission statement?"` (existing test, 2 keyword hits) is also
unaffected — the guard only applies to a *single* weak keyword hit,
consistent with the technical-domain guard.

## 5. Files changed

- `app/router/classifier.py` — `_TECHNICAL_KEYWORDS` split into
  `_TECHNICAL_ACTION_KEYWORDS` / `_TECHNICAL_DOMAIN_KEYWORDS` (union is a
  superset of the original set: 6 words added — `perbaiki`, `implement`,
  `uji`, `migrate`, `kodekan`, `develop` — nothing removed); new
  `_EXPLANATORY_PATTERNS` + `_is_explanatory()`; `classify_intent()`
  rewritten to action-first / domain-with-guard for Technical, and
  guarded for Strategy; `matched_rule` labels updated
  (`technical_action:...`, `technical_domain:...`,
  `informational_override:...`) for traceability. Google Workspace,
  Presentation, and Academic blocks are byte-for-byte unchanged.
- `tests/test_router_classifier.py` — new table-driven regression suite
  covering all 14 required acceptance cases plus English-phrasing and
  category-guard coverage (§6); 24 new tests.
- `tests/test_router_planner.py` — `generate_plan()` regression verified
  against the real workflow/role registry (`get_workflow("GENERAL")`,
  not hard-coded role IDs); 5 new tests.
- `docs/SPRINT_5G2.md` (this file) — new.
- `docs/CURRENT_SPRINT.md` — new "Wave 5.2 — Sprint 5G.2" section, status
  marked implementation under review / not yet merged.

**Not touched**: `app/providers/`, `app/dispatch/`, `app/nightshift/`,
`app/control_tower/`, `app/google_workspace/`, `app/dissertation/`,
`app/router/planner.py`, `app/router/roles.py`, `app/router/workflows.py`,
`app/router/risk.py`. No provider fallback, 9Router mapping, Night Shift,
or approval-semantics change. No network call, no subprocess, no shell
execution, no secrets touched — this is a pure in-process keyword/regex
change to one module.

## 6. Acceptance-case matrix

| # | Message | Expected | Actual | Rule |
|---|---|---|---|---|
| 1 | Jelaskan dalam 3 kalimat apa fungsi Executive Control Tower di NOVA. | GENERAL | GENERAL | `informational_override:executive,fungsi` |
| 2 | Jelaskan fungsi database. | GENERAL | GENERAL | `informational_override:database,fungsi` |
| 3 | Apa fungsi NOVA Router? | GENERAL | GENERAL | `informational_override:fungsi` |
| 4 | Perbaiki fungsi database connection ini. | TECHNICAL | TECHNICAL | `technical_action:database,fungsi,perbaiki` |
| 5 | Debug fungsi generate_text. | TECHNICAL | TECHNICAL | `technical_action:debug,fungsi` |
| 6 | Review architecture NOVA. | TECHNICAL | TECHNICAL | `technical_domain:architecture` |
| 7 | Implement provider adapter baru. | TECHNICAL | TECHNICAL | `technical_action:implement` |
| 8 | Refactor class provider gateway. | TECHNICAL | TECHNICAL | `technical_action:class,refactor` |
| 9 | Buat strategi NOVA 2027. | STRATEGY | STRATEGY | `strategy_keywords:strategi` |
| 10 | Jelaskan strategi NOVA 2027. | GENERAL (chosen policy, §4) | GENERAL | `informational_override:strategi` |
| 11 | Cari dokumen di Google Drive. | GOOGLE_WORKSPACE | GOOGLE_WORKSPACE | `google_workspace_keywords:google drive` |
| 12 | Buat slide executive summary. | PRESENTATION | PRESENTATION | `presentation_keywords:slide` |
| 13 | Review literatur untuk disertasi. | ACADEMIC | ACADEMIC | `academic_keywords:literatur,review` |
| 14 | debug | FAST (frozen single-word precedence, unchanged) | FAST | `fast_pattern:^\s*\S+\s*\??\s*$` |

All 14 rows verified directly via `classify_intent()` and encoded as
regression tests in `tests/test_router_classifier.py`; case 1 additionally
verified end-to-end through `generate_plan()` in
`tests/test_router_planner.py::test_generate_plan_informational_technical_question_routes_to_general`.

## 7. Out of scope / not implemented

- No provider, dispatch, Night Shift, Control Tower orchestration, Google
  Workspace, or Dissertation code touched.
- No change to `app/router/planner.py`, `roles.py`, `workflows.py`, or
  `risk.py` — the fix is confined to `classify_intent()`'s input
  vocabulary and guard logic; the planner already resolves roles/risk from
  the registry, and the registry itself is unchanged.
- No Sprint 6B work.
- No change to `_HIGH_RISK_PATTERNS` / risk policy in `app/router/risk.py`
  — the reclassified GENERAL messages already resolve LOW/NONE via the
  existing `_LOW_RISK_WORKFLOWS` set (`GENERAL` was already a member).

## 8. Remaining ambiguities / technical debt

- The explanatory-marker guard only fires on a **single** weak
  technical-domain or strategy keyword hit for Technical, and only on
  Strategy's single-hit branch; it does not currently generalize to
  Academic/Presentation/Google Workspace, per the sprint brief's explicit
  guidance that those categories' concrete phrase matches should remain
  intent-agnostic. If a future defect report surfaces an analogous false
  positive in one of those categories, the same guard pattern
  (`_is_explanatory` + single-weak-keyword check) can be extended there
  without touching Technical/Strategy.
- `_EXPLANATORY_PATTERNS` is a fixed, small phrase list (Indonesian +
  English). It is not exhaustive of all informational phrasing (e.g.
  "tell me about", "how does X work" without "cara kerja") — extending it
  is a follow-up, not required by any of the 14 acceptance cases.
- Confidence semantics: a suppressed weak match now yields GENERAL/MEDIUM
  (`informational_override:...`) rather than GENERAL/LOW
  (`default_fallback`). This is intentional (§4) but is a new,
  observable confidence value that any downstream consumer keying off
  `matched_rule` prefixes should be aware of. No such consumer exists
  today outside tests.

## 9. Test evidence

Targeted:

```
python -m pytest tests/test_router_classifier.py tests/test_router_planner.py \
  tests/test_router_roles.py tests/test_router_workflows.py tests/test_router_risk.py -q
141 passed
```

Provider-policy / routing consumers:

```
python -m pytest tests/test_dispatch_provider_adapter.py tests/test_provider_gateway.py \
  tests/test_provider_policy.py tests/test_provider_selection.py \
  tests/test_provider_telegram.py tests/test_provider_upstream_routing.py -q
70 passed
```

Full canonical regression:

```
python -m pytest -ra --tb=short
622 passed
```

(Baseline before this sprint: 593 passed. 29 new tests added — 24 in
`test_router_classifier.py`, 5 in `test_router_planner.py` — zero removed,
zero modified pre-existing assertions. 593 + 29 = 622.)

Static checks:

```
python -m py_compile app/router/*.py   # OK, no output
git diff --check                       # OK, no output (no whitespace errors)
```
