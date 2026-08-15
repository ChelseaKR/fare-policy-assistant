# web/

The demo UI: one static page (`index.html`) and one Lambda handler
(`handler.py`) that serves it and answers `POST /api/ask`. No framework, no
build step; the page is hand-written HTML/CSS/JS targeting WCAG 2.2 AA (labels,
visible focus, `aria-live` results, 24px-minimum targets, works on a phone).

Public entrypoints are deliberately separate:

- evaluation evidence hub: <https://evals.chelseakr.com/>
- live AWS assistant: <https://fare.chelseakr.com/>

`a11y.py` is a pure-Python structural accessibility gate (page language,
labeled controls, heading order, link text, zoom not disabled, target size). It
runs in CI and as `make a11y`; a blocking pa11y/axe pass cross-checks the
parts a static analysis cannot (computed contrast, ARIA semantics). Neither
replaces a manual screen-reader walkthrough, recorded in the model card.

The page carries the three disclosures CLAUDE.md requires: the "reference
implementation" banner, the will-not-do list, and a prominent warning that
answers come from dated snapshots rather than live agency pages (with the
source-specific fetch date on every answer).

Questions and history are processed transiently. Their raw text is not logged
or used as a cache key. Only successful answer payloads may be cached, in a
bounded in-memory cache that disappears when the serverless container is
recycled; refused, guarded, and personal-information-like inputs are not
cached. The client also excludes an input-refusal turn from follow-up history.

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
       "as_of_date": "2026-06-12", "citations": [{"agency": "MST", ...}],
       "structured": {...}}
```

`history` is optional (omit it for a one-shot question). The client holds the
conversation; the server stores nothing. Up to the last three turns are used to
resolve follow-up references, and answers are deterministically cached per
container keyed on the question and its history.

`kind` is the same trace field the eval harness records: `answered`,
`refused_input`, `refused_no_support`, or `answered_guarded`.

`structured` is an additive, experimental integration contract. The public
beta UI deliberately renders the reviewed prose `answer` only; API consumers
may inspect `structured` without changing the rider presentation.
