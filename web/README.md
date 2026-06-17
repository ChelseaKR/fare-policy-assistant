# web/

The demo UI: one static page (`index.html`) and one Lambda handler
(`handler.py`) that serves it and answers `POST /api/ask`. No framework, no
build step; the page is hand-written HTML/CSS/JS targeting WCAG 2.2 AA (labels,
visible focus, `aria-live` results, 24px-minimum targets, works on a phone).

`a11y.py` is a pure-Python structural accessibility gate (page language,
labeled controls, heading order, link text, zoom not disabled, target size). It
runs in CI and as `make a11y`; an advisory pa11y/axe pass cross-checks the
parts a static analysis cannot (computed contrast, ARIA semantics). Neither
replaces a manual screen-reader walkthrough, recorded in the model card.

The page carries the three disclosures CLAUDE.md requires: the "reference
implementation" banner, the will-not-do list, and the "based on policies
published as of <date>" line (static in the header, per-answer from the API).
The footer states that questions are never stored.

Deploy with `./infra/deploy.sh` (see ADR 0004 for the architecture and the
abuse/cost guards). Try it locally without deploying:

```sh
uv run python -m assistant.cli "How much is the senior fare on SBMTD?"
```

API shape:

```
POST /api/ask
{"question": "Does it cover my spouse too?",
 "history": [{"q": "<prior question>", "a": "<prior answer>"}]}

200 → {"answer": "...", "kind": "answered", "language": "en",
       "as_of_date": "2026-06-12", "citations": [{"agency": "MST", ...}]}
```

`history` is optional (omit it for a one-shot question). The client holds the
conversation; the server stores nothing. Up to the last three turns are used to
resolve follow-up references, and answers are deterministically cached per
container keyed on the question and its history.

`kind` is the same trace field the eval harness records: `answered`,
`refused_input`, `refused_no_support`, or `answered_guarded`.
