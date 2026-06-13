# web/

The demo UI: one static page (`index.html`) and one Lambda handler
(`handler.py`) that serves it and answers `POST /api/ask`. No framework, no
build step; the page is hand-written HTML/CSS/JS targeting WCAG 2.1 AA
(labels, visible focus, `aria-live` results, works on a phone).

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
{"question": "What proof do I need for the veteran fare on MST?"}

200 → {"answer": "...", "kind": "answered", "language": "en",
       "as_of_date": "2026-06-12", "citations": [{"agency": "MST", ...}]}
```

`kind` is the same trace field the eval harness records: `answered`,
`refused_input`, `refused_no_support`, or `answered_guarded`.
