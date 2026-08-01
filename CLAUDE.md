# CLAUDE.md — fare-policy-assistant (Transit Fare Policy Assistant + Eval Harness)

> Root instruction file for the `fare-policy-assistant` repo. Read fully before writing code.
> The eval harness is the headline deliverable. The chatbot exists so the harness has something
> to evaluate.

## What this is

A narrow retrieval-augmented assistant that answers rider questions about fare and reduced-fare
policies for a small set of California transit agencies ("Am I eligible for a senior discount on
SBMTD?", "What proof do I need for the veteran fare on MST?", "¿Cuánto cuesta el pasaje reducido
en Yolobus?") — wrapped in a rigorous, public evaluation framework that measures groundedness,
refusal behavior, eligibility-edge-case handling, and multilingual quality.

Pilot corpus: published fare pages and reduced-fare policy documents from 4–6 agencies. Start
with Monterey-Salinas Transit (MST), Santa Barbara MTD, Yolobus, and Unitrans; SacRT or LA Metro
as stretch. MST and SBMTD are chosen deliberately — they are the live agencies on Cal-ITP
Benefits, so the corpus overlaps with a real public-benefit eligibility verification domain.

## Why it exists

Portfolio project by Chelsea Kelly-Reif. The thesis it demonstrates: government clients are
asking every vendor "what's your AI story," and almost no vendor can answer "here's how we test
it." The builder previously architected a production civic RAG system (AI career coach on AWS
Bedrock, RAG over 3,800+ training programs, 105-question evaluation suite). This project
distills that practice into an open, inspectable artifact in the civic-tech domain.

Consequences for every design decision:
- **The eval report is the demo.** The artifact this project leads with is a generated
  EVALS.md / HTML report with pass rates, failure examples, and the safety case — not the chat UI.
- **Responsible-AI posture is explicit.** The README states what the assistant will not do
  (no eligibility determinations, no legal advice, no PII collection) and how that is enforced
  and tested. A transit rider's benefits eligibility touches age, disability, income, and
  veteran status — treat the domain with the gravity it deserves.
- This is a reference implementation, not a product. No accounts, no persistence of user
  queries beyond anonymous eval logging in dev.

## Hard rules

- **Every answer must cite its source** (agency, document title, URL, retrieved passage). If
  retrieval confidence is low or the corpus lacks an answer, the assistant says so and points to
  the agency's contact info. An unsupported answer is a critical eval failure, full stop.
- **The assistant never determines eligibility.** It explains published criteria and processes.
  System prompt + output checks + evals all enforce the distinction ("you may qualify if…" /
  "the published criteria are…" vs. "you qualify").
- **Corpus is versioned and dated.** Every document snapshot carries fetch date and source URL;
  the UI displays "based on policies published as of <date>." Fare policy goes stale; say so.
- No scraping beyond polite fetching of public pages; respect robots.txt; cache aggressively.

## Architecture

```
fare-policy-assistant/
├── CLAUDE.md
├── README.md                      # leads with the safety/eval story, then quick start
├── corpus/
│   ├── manifest.yaml              # per-doc: agency, title, url, fetch_date, license note
│   ├── raw/                       # fetched HTML/PDF snapshots (committed; they're small)
│   └── processed/                 # cleaned markdown chunks with metadata headers
├── src/assistant/
│   ├── ingest.py                  # fetch → clean → chunk (by policy section) → embed → index
│   ├── retrieve.py                # hybrid: BM25 (rank_bm25) + embeddings; agency filter
│   ├── answer.py                  # prompt assembly, model call, citation extraction
│   ├── guards.py                  # input/output checks: PII, determination language, scope
│   └── config.py                  # models, thresholds, prompts as versioned files
├── prompts/                       # system + answer prompts, plain text, versioned, reviewed
├── evals/
│   ├── suites/
│   │   ├── groundedness.yaml      # claims must trace to retrieved passages
│   │   ├── refusal.yaml           # out-of-scope, adversarial, determination-seeking
│   │   ├── edge_cases.yaml        # eligibility boundaries: ages, expired programs, stacking
│   │   ├── multilingual.yaml      # Spanish parity required; tag stretch languages clearly
│   │   └── freshness.yaml         # stale-corpus behavior, "as of" disclosure present
│   ├── runner.py                  # runs suites, LLM-as-judge + deterministic checks
│   ├── judges.py                  # judge prompts; judge model ≠ answer model
│   └── report.py                  # emits EVALS.md + HTML report with examples
├── web/                           # minimal accessible chat UI (static + one API route)
├── infra/                         # serverless deploy (Lambda + API GW or equivalent)
└── docs/
    ├── model-card.md              # scope, limits, data, eval results, escalation guidance
    └── decisions/                 # ADRs
```

Model strategy: provider-portable via a thin adapter. Default to Claude on Amazon Bedrock,
since the builder's production experience is Bedrock and gov clients often require it; the
direct Anthropic API remains available behind a config switch. Embeddings: any solid hosted or
local model — record the choice in an ADR. Judge model must differ from answer model.

Retrieval: keep it honest and simple. Chunk by policy section with agency/program metadata,
hybrid BM25 + dense retrieval, top-k with an agency filter when the question names one. No
agentic loops, no reranker unless evals show retrieval is the bottleneck — if added, the eval
deltas justify it in an ADR.

## The eval harness (the actual product)

Target: **100+ cases** across five suites, every case carrying: id, question, agency scope,
expected behavior (answer / partial / refuse-and-redirect), required citations or facts,
language tag, and rationale. Cases live in YAML so a non-engineer reviewer can read them.

Scoring per case combines:
- **Deterministic checks** wherever possible: citation present and resolvable to corpus;
  forbidden determination phrases absent; "as of" date shown; correct agency cited; response
  language matches query language.
- **LLM-as-judge** for groundedness (every factual claim supported by a retrieved passage) and
  helpfulness, with the judge prompt committed and versioned. Judge disagreement spot-checked by
  hand on a 10% sample; record human-judge agreement in the report.

Suite design notes:
- *Refusal suite* includes: medical/disability documentation advice, immigration-status
  questions, "just tell me I qualify," prompt-injection attempts embedded in questions, and
  questions about agencies outside the corpus (correct behavior: say so, link 511/agency site).
- *Edge cases* probe boundaries published in the actual policies: exact qualifying ages,
  Medicare-card vs. age pathways, CalFresh cardholder rules, what stacks with what. Build these
  from the real documents during ingest — every edge case cites its source passage.
- *Multilingual*: full Spanish parity is required (CA reality and the builder's site is already
  bilingual). Each Spanish case mirrors an English case so parity gaps are measurable.
- CI runs a 26-case smoke suite on every PR; full suite runs nightly and on release tags.
  Regressions >2 points on any suite fail the build. Both are served from the persisted
  content-keyed answer/judge cache, with one cold nightly a week to re-measure the
  provider (ADR 0022) — model calls are the project's largest AWS line, so an eval that
  cannot produce a different answer must not be paid for again.

The generated report leads with a scoreboard, then **representative failures with full traces**
(question → retrieved passages → answer → judge reasoning). Showing failures candidly is the
credibility move; do not cherry-pick.

## Build plan

### Phase 1 — Corpus + skeleton (week 1)
- Fetch and snapshot fare/reduced-fare pages for MST, SBMTD, Yolobus, Unitrans. Manifest with
  dates and URLs. Chunking that preserves program structure (one chunk ≈ one program/section).
- Retrieval working locally with a handful of smoke questions.
- guards.py v1: scope check, determination-language check on output.

### Phase 2 — Eval harness first (week 2)
- Runner, judges, report generator. Author the first 60 cases (groundedness, refusal, edge
  cases) directly from the corpus documents.
- Wire CI smoke suite. Get a baseline scoreboard — expect mediocre numbers; commit them. The
  improvement curve across commits is part of the story.

### Phase 3 — Iterate to quality + multilingual (week 3)
- Tune prompts/retrieval against eval failures only (no vibes-driven changes; every prompt
  change cites the failing cases it targets).
- Spanish suite to parity. Remaining 40+ cases. Model card written.
- Minimal accessible web UI (WCAG 2.2 AA, works on a phone) deployed behind a real URL with a
  visible "reference implementation" banner and the will-not-do list.

### Phase 4 — Publication polish
- EVALS.md regenerated from the latest full run, linked from the README's first screen.
- A short docs page: "How to adapt this harness to your domain" — the generalization another
  team could reuse for, say, a benefits-eligibility assistant.

## Quality bar

- pytest for all deterministic components (ingest, retrieve, guards, report); ruff + mypy.
- Reproducibility: pinned model versions in config; eval runs record model + prompt versions;
  `make eval` regenerates the report end to end.
- Cost discipline: full eval run under a few dollars; cache retrieval; document per-run cost.
- Privacy: no user query persistence in the deployed demo; state this in the UI footer.

## Writing style for README, model card, UI copy, and report prose

Plain and concrete. At most one em dash per document, prefer zero. No rule-of-three rhetorical
constructions, no flagged-sincerity phrases, no hype ("powerful," "seamless," "magic"). The
model card and refusal copy should read calm and specific. Vary paragraph openings; tolerate the
occasional flat sentence rather than over-polishing.

## Open questions to resolve early

1. Confirm current published fare/discount pages per pilot agency (policies change; snapshot
   what exists in June 2026).
2. Embeddings choice and whether local inference keeps the demo cost near zero.
3. Whether SBMTD/MST publish policy PDFs requiring OCR — affects ingest scope.
4. Judge model selection and the human-agreement sampling protocol.
