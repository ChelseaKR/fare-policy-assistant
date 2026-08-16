# Second-harness audit methodology

This directory holds a **second-harness** audit of the deployed assistant:
recorded answers, replayed and re-scored by
[Plumbline](https://github.com/ChelseaKR/plumbline) — a separate, public,
Apache-2.0 evaluation harness. It complements, and does not replace, this repo's
own evaluation in [`../../EVALS.md`](../../EVALS.md).

The heading says "second-harness" rather than "independent" on purpose. Plumbline
is separate code with its own suites and its own judge, and it is blind to this
system's internals, which is where its value is. It is not a third party: it was
written by the same author. What changed on 2026-08-16 is that it is now
**reproducible by a stranger**, which the previous arrangement was not.

## What was wrong before, and what replaced it

Until 2026-08-16 the audit ran on `govchat-eval`, and that repository went
private and archived. The consequences were quiet and bad:

- `make audit` began with `EVAL_HARNESS ?= ../govchat-eval` and exited 2 with
  "govchat-eval not found" for anyone without an archived clone. In a build log
  that reads as a configuration problem, not as an unaudited release.
- The CI job cloned the same archived repository, so it ran only on a schedule,
  only when a repository variable was set, and with `continue-on-error: true`.
  It could not fail a build.
- The README's claim that a second harness audits this system was, from outside,
  unverifiable.

Plumbline is public and **replay-only**: it consumes a sealed evidence bundle
and calls nothing. So the audit now runs on every pull request, with no secrets,
no model calls, and no cost, and anyone with this checkout can reproduce the
verdict byte for byte.

The harness is **not a dependency of this project**. `plumbline.pin` names the
repository and one exact commit; `plumbline-gate.sh` resolves that commit into
`.plumbline-cache/` at run time and refuses to proceed unless the checkout is at
the pinned commit. A laptop and CI read the same pin and run the same command.
If the harness cannot be resolved, the gate **fails** — a gate that could not run
is not a gate that passed.

> **Pinned-commit caveat.** The pinned Plumbline commit hashes an evidence bundle
> with `iterdir()`, so only top-level files are covered. This bundle is flat and
> `evals/plumbline_export.py` seals it with a recursive walk of its own, so the
> hole is not open here. Advance the pin once the upstream fix lands; the bundle
> hash and run id should not move when you do.

## Why two evaluations

This repo's harness (`evals/`) is **white-box**. Its checks know the internals:
that a citation must resolve to a doc-id in this corpus, that `guards.py`
forbids determination language, that the cited agency must match the question's
scope. That is the right tool for tuning the system against its own
requirements.

Plumbline is **black-box**. It sees only the question, the recorded answer, the
sources, and declared ground truth, and applies its own suites and judge with no
knowledge of how the assistant works. A system graded only by the harness tuned
against it is a weaker claim than one a second, blind harness also grades.

It earned that on day one. Two of its findings are in this repo's own defect
list now: the snapshot-date disclosure scoring as an unsupported number (the same
defect fixed in `evals/judges._passages_block` the same day), and a phone number
the corpus cleaner broke into `805. 963.3364`, which no in-repo check was
looking for.

## How the audit runs

    make audit

Three steps, and the third is the merge gate.

1. `python -m evals.plumbline_export --check` — the committed bundle must be what
   the recording produces. A suite edit or a re-recording that was never exported
   fails here rather than being audited against stale evidence.
2. `./plumbline-gate.sh` — resolves the pinned harness, scores the bundle, writes
   `docs/audits/plumbline/<run-id>/`. **Its exit code is deliberately not the
   gate**; see "Floors, findings, and the guard" below.
3. `python -m evals.plumbline_guard` — the gate. It fails on any suite below the
   committed baseline, any hard failure nobody has acknowledged, and any
   acknowledgement that has stopped firing.

Nothing in that calls a model. The evidence is the recording already committed at
`evals/govchat/golden.jsonl` — 195 questions and the answers the deployed
pipeline produced for them on 2026-06-16. Two harnesses, one recording, one bill.

## The bundle, and the one shape difference

`evals/plumbline_export.py` reshapes the recording into Plumbline's bundle
format (`items.jsonl`, `responses.jsonl`, `sources.jsonl`, `manifest.json`,
sealed by `checksums.json`, plus the rider page as `interface.html` and the
recorded turns as `transcripts.html`).

Passages are attributed to their documents against the corpus **as it stood at
recording time** — the recording names its `corpus_version` and
`corpus/versions/<version>/chunks.jsonl` is committed. This matters: 108 of the
756 recorded passages no longer appear verbatim in today's corpus, and matching
against today's would have dropped or misattributed them. Against the declared
snapshot, all 756 match exactly.

**A bundle source is a document, not a passage.** Plumbline scores grounding per
source and a source is whatever the response cites by id; this assistant cites
documents (`[doc:mst-fares]`). So the external grounding check is *looser* than
this repo's own judge, which scores against the exact top-k passages. Two
different questions — "did the answer stay inside the evidence it was handed"
(ours) and "did it stay inside the documents it pointed the rider at" (theirs) —
and recording the difference is the point of having both. Rewriting the recorded
citations into passage ids would have closed the gap by falsifying the evidence.

Two other adapter decisions worth knowing:

- **Source text carries its provenance line.** Every source begins
  `(source: <url>, fetched <date>)`, exactly as `assistant.answer._format_passages`
  gives it to the answer model. `prompts/system.txt` rule 4 *requires* every
  answer to disclose that date, so a grounding checker shown only passage text
  reads 2026, 06 and 12 as invented. It did: that was the single largest cause of
  unsupported-number failures, and fixing it moved groundedness 0.6251 → 0.7308
  and citation accuracy 0.6010 → 0.7136 without touching a single answer.
- **`forbidden_content` rides only on the attack probes.** This repo's field
  means "must not be *asserted*" — `evals.checks.phrase_asserted` deliberately
  allows an answer that denies or quotes-to-reject the phrase. Plumbline's
  `forbidden` means "must not *appear*". Mapping one onto the other failed four
  items for answering well ("transfers are **not** valid for return trips"). On
  an attack probe the two predicates agree, so the screen is exported there and
  nowhere else.

## Suite mapping

| This repo | Plumbline | Ground truth used |
|---|---|---|
| all suites | smoke | a non-empty recorded response per item |
| edge_cases | accuracy | `required_facts` joined; the case rationale where none is declared |
| groundedness | groundedness, citation_validity, citation_accuracy | the retrieved documents as `sources` |
| refusal | refusal, adversarial | `refuse_redirect` → `behavior: refuse`; tagged jailbreaks → `adversarial` |
| multilingual | multilingual, cross_language | answer language; `mirror_of` → `fact_id` |
| guards / PII | representational_harms, privacy | shipped screens plus attack-probe `forbidden` |
| web/a11y.py | accessibility | `web/index.html`, hashed into the bundle |

`load_bearing` is set only where the reference came from a case's actual
`required_facts` and carries a number. That turns on Plumbline's rule that any
missing reference number fails the whole suite regardless of the pooled mean —
the fabrication check worth having — without letting a rationale's incidental
digits become facts the answer owes anybody.

## Floors, findings, and the guard

`evals/plumbline/target.toml` sets a floor per suite. **Every floor is measured,
not aspirational**, and every floor that differs from the harness's default says
why, in both directions. A floor above what the system does is a gate that is red
on the day it lands.

That leaves two holes, and the guard closes both:

- **A floor is a minimum.** With accuracy floored at 0.04, a score can decay a
  long way and stay green. `evals/plumbline_guard.py` fails on any suite below
  the committed `evals/plumbline/baseline.json`.
- **A hard failure is a real finding.** The audit found 76 of them across five
  suites on the day it landed. Leaving the gate permanently red teaches everyone
  to ignore it; lowering something until it goes green is worse. Each one is
  listed in `evals/plumbline/acknowledged_findings.json` with a reason and an
  owner. A hard failure that is not on that list fails the build — and so does a
  listed one that has stopped firing, because a waiver for a fixed problem is a
  lie that accumulates.

Two suites are **disabled**, each with a written `gap` and `fix_belongs_in`:
`fairness` (no item declares a rider-class group, so no disparity across senior,
veteran, disabled or youth riders can be measured) and `passage_attribution`
(sources are documents, so there is no answering passage to declare). The second
is the one this repo would most benefit from: `refuse-025` on 2026-08-16 was
exactly a wrong-passage answer, grounded and correctly cited and composed from
the veterans' sentence of a document retrieved for a senior's question.

## What the numbers do and do not mean

Plumbline's default judge is **deterministic and lexical** (token overlap, with
number and negation handling). That makes the audit reproducible and CI-able,
but it is a floor, not a benchmark. Three specific cautions, all of them visible
in the committed report:

- **The accuracy score is close to meaningless as a quality signal.** Measured
  0.0591. The reference is a bag of required facts, the answer is a paragraph,
  and token-F1 between those two shapes measures the mismatch. The load-bearing
  number rule is the part that carries weight.
- **The refusal and adversarial scores are measuring the judge's vocabulary.**
  `plumbline.lexicons.REFUSAL_MARKERS` looks for "I can't help" and similar, and
  every decline here reads "I can only answer questions about published transit
  fare policies". The markers are not settable from target configuration, so
  three correctly-refused jailbreak probes score 0.0000. The check that matters
  on an attack probe — did it emit the forbidden string — is clean and hard-fails
  at any floor.
- **Cross-language disagreements are mostly phone numbers.** The suite compares
  the sets of numbers two answers report, and the English answer ends with
  1-888-MST-BUS1 while the Spanish one does not. Two pairs are genuinely broken
  (`ml-008`, `ml-022` were not mirrors of the cases they named), which this repo's
  own `mirror_problems` gate found and fixed on 2026-08-05 — after this recording
  was frozen.

## Gaps, stated plainly

- **Freshness** has no native Plumbline suite. Those cases ride accuracy and
  refusal; the behavior this repo cares about — disclosing the snapshot date and
  declining to speculate about the future — is only fully exercised by this
  repo's own freshness suite.
- **Accessibility** scores 0.8000: four of five structural checks pass on
  `web/index.html`, and `contrast_declarations` fails because the page ships no
  `<script type="application/json" id="plumbline-contrast">` declaring its colour
  pairs. Plumbline computes contrast rather than believing a claim. Adding that
  block is the named next step; `make a11y` covers the same page meanwhile.
- **Accuracy overlap.** Plumbline's accuracy suite is lexical fact-containment,
  close in spirit to this repo's `required_facts` check, so it is not a strongly
  independent signal. The independence lives in the grounding, cross-language,
  privacy and conduct suites.
- **The recording is from 2026-06-16.** It predates thirteen corpus agencies, the
  prompt versions HEAD ships, and the `mirror_of` corrections. Everything above
  describes the assistant as it was that day. Re-recording is a live step and a
  real bill; `evals/stale_acknowledged.json` carries the same caveat for the
  in-repo artifacts.

## Licensing of quoted text

The bundle quotes agency fare text in `sources.jsonl`, and the manifest states
that this project grants no rights over it; see
[`../../corpus/LICENSE-NOTE.md`](../../corpus/LICENSE-NOTE.md). The recording's
own per-row `license` field says the same thing, and correcting it is
`make audit-restamp-license`, which rewrites the note and nothing else.
