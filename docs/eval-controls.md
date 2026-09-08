# Negative controls: what each one rules out

`make controls` (`python -m evals.controls`, or `python -m evals.runner --controls`)

A pass rate does not say where the pass came from. A model that knows California
fares from pretraining, a retriever that surfaces the right passage, and a
harness whose checks cannot fail all produce the same number. The independent
Plumbline audit gives that number an outside floor. This gives it an inside one.

Three arms run beside the baseline. Each is a **retriever substitution and
nothing else**: same prompt, same guards, same answer pipeline, same
`evals.checks.run_checks`. That is what makes it a control rather than a second
implementation of the system.

| arm | what the assistant is given | what a *failure to move* would mean |
|---|---|---|
| `no_retrieval` | nothing | a citation that resolves to the corpus did not come from retrieval |
| `wrong_agency` | the next agency's passages, alphabetically | the agency binding is not coming from the evidence |
| `stale_corpus` | the oldest retained corpus version | the thirteen-agency expansion bought nothing measurable |

It runs offline against `assistant.models.MockModel`, which answers only from
the passages it is given. That property is what makes a retrieval control
possible without paying for a run: substitute the passages and the answer has
to move, or the harness is not measuring what it claims.

## What it measured (2026-09-06, committed corpus, 385 cases)

| check | baseline | `no_retrieval` | `wrong_agency` | `stale_corpus` |
|---|---|---|---|---|
| `citation_present_and_resolvable` | 99.7% | **0.0%** | 53.3% | **52.1%** |
| `correct_agency_cited` | 98.1% | — | **0.0%** | 96.3% |
| `required_facts_present` | 0.6% | 0.0% | 0.0% | 0.0% |
| cases passed | 21/385 | 36/385 | 22/385 | 22/385 |

## The overall pass rate is the wrong thing to assert on

Look at the last row. The `no_retrieval` control scores **36/385 against the
baseline's 21/385** — higher. Every refusal case passes when the assistant has
nothing to stand on, and offline the mock model produces no required facts for
anyone, so the arms differ mostly in whether they refuse.

A control suite that asserted "the control must score below the real run" would
have shipped green on this repository and measured nothing at all. That is the
same shape as a gate that cannot fail, which is the defect these controls exist
to catch, so it is worth stating plainly rather than quietly picking a different
metric.

The assertions are therefore **per check**, on the two checks retrieval is
supposed to be the cause of:

- `no_retrieval` → `citation_present_and_resolvable` must be **exactly 0%**.
  Not a threshold: a citation cannot resolve when no passage was retrieved.
- `wrong_agency` → `correct_agency_cited` must be **exactly 0%**. Same shape:
  the cited agency cannot be the right one when only another agency's passages
  were offered.
- `stale_corpus` → `citation_present_and_resolvable` must be at least **20
  points** below the baseline. This one is a threshold, set against a measured
  drop of 47.6 points, so it has real headroom and still fails if the corpus
  history stops mattering.

Two floors guard the other direction. If the baseline's own
`citation_present_and_resolvable` or `correct_agency_cited` falls below 90%, the
run fails as an instrument failure — a baseline that weak cannot be told apart
from a control, so the controls would prove nothing.

`required_facts_present` is reported and deliberately not asserted on: at 0.6%
offline it has no room to drop, because the mock model states no facts. It is in
the table so a reader can see that, rather than discovering it later.

## Failure modes this is built against

- **A control that silently no-ops.** A sabotage that does not apply reads as a
  pass. Each arm has a test asserting it actually changed what the assistant was
  given, and an arm that never emits the check it exists to move is reported as
  "the control was not actually applied", not as a pass.
- **An arm quietly degrading to the baseline.** A repository with no retained
  corpus version loses the `stale_corpus` arm entirely rather than getting a
  second copy of the baseline wearing its name.
- **An absence rendered as a measurement.** A check an arm never emitted reports
  as `--`, never as 0%.
- **A sample standing in for the population.** `--limit` reports the assertions
  and exits 0. The floors were measured over the whole suite, and a 40-case
  slice can miss them for reasons that have nothing to do with the instrument.
  `make controls` and CI pass no limit.

## What this does not cover

Adversarial and injection cases (RE4) are the refusal suite's job, not a
control's. Nothing here creates new eval cases or changes an existing one. And a
control is a statement about *retrieval*: it says nothing about whether the
judge's rubric is right, which is what `docs/judge-calibration.md` is for.

---

# Answer drift across corpus versions

`make drift FROM=<corpus version> [TO=<version|live>]`
(`python -m evals.drift --from <version> --to <version>`)

A control asks what a *perturbation* does to the instrument. This asks the same
question of a real change: when a fare page moves, what does the assistant now
tell a rider differently?

`tools/corpus_refresh_report.py` already says which documents changed and which
eval cases now assert a stale fact. It cannot say whether an answer moved,
which is the thing a fare-policy owner reviewing a refresh PR has to judge.
This runs the whole case set against both versions through the ordinary answer
pipeline and pairs the answers per case.

| outcome | meaning |
|---|---|
| `unchanged` | the two answers are byte-identical |
| `changed_expected` | the answer moved, and a document either side cites is in the corpus diff |
| `changed_unexpected` | the answer moved and **no cited document did** — retrieval or provider variance, the instrument drifting rather than the policy |

`newly_failing` is a **flag on every row, not a fourth bucket.** An answer can
be byte-identical and newly failing: if a document it cites was removed,
`citation_present_and_resolvable` fails on the new side over the same text. A
four-way partition would have filed that as `unchanged` and hidden the case a
reviewer most needs.

Scoring is the deterministic half of the harness, run per side **against that
side's own corpus and its own derived fact table.** The committed
`facts.jsonl` describes the live corpus only; scoring an old answer against it
would be two rulers. The judges are not run — every classification above is
decided by answer text and citations.

## Cost

Both sides share one model wrapper memoised on the **rendered prompt**
(`evals.cache.completion_key`), which already encodes the corpus version, the
prompt version and the question. So "the passages did not move" and "the key is
the same" are one statement, and reuse costs nothing in fidelity.

Measured on the committed corpus, 385 cases, offline mock model:

| comparison | model calls (from → to) | reused | changed | unexplained |
|---|---|---|---|---|
| `35ec70d6359d` → `74b05330cb39` (one Yolobus page) | 191 → **57** | 139 | 2, both citing `yolobus-fares` | **0** |
| `74b05330cb39` → itself | 191 → **0** | 196 | 0 | 0 |
| `a68e77ff4673` → `63d6718126f1` (2 added, 4 removed) | — | — | 42 | **7** |

Both counters are printed and go into the JSON report, so a cheap run explains
itself instead of reading as a thorough one.

The third row is the tool finding something real on an unmodified tree, which is
stronger evidence than any deliberate sabotage: seven answers moved with no
cited document moving. `cross_agency::xagency-007` is representative — it cited
`mst-fares` before and `mst-fares-benefits` after, though no MST document
changed. Retrieval re-ranked because other documents entered and left the
corpus around it.

## Where the gate lives, and why it is last

The weekly `corpus-freshness` workflow runs this against the pre-refresh
snapshot it already writes, and appends the report to the refresh PR's body, so
the evidence arrives attached to the diff it explains. That step always exits 0.

The gate is a **separate step placed after the PR is opened**. A report nobody
reads is worthless, so the evidence ships first and the run goes red second.
Swallowing the verdict entirely would be worse than not measuring at all: a run
that concludes `success` over a breached ceiling has removed a signal rather
than added one. The ordering is asserted in `tests/test_workflow_safety.py`,
because moving the gate above the PR step would silently trade the evidence for
the verdict.

The ceiling is `--max-unexpected 0`. That was measured, not guessed — see the
table above. If an ordinary weekly refresh turns out to sit above zero for
honest reasons, raise that one number with the measurement beside it rather
than dropping the step.

## What this does not cover

It is eval-only. EXP-05 forbids rider-facing time travel and this proposes
none: nothing here serves an old corpus to anybody, and no artifact it writes is
read by the answer path. It does not compare prompt versions (the eval-history
artifact covers that), does not edit eval cases, and does not run the judges, so
`newly_failing` is a deterministic-check verdict rather than a quality one. The
smoke evals on the refresh PR are what score quality.
