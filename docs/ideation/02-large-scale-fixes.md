# Large-scale fixes — FIX-01 … FIX-12

Drafted 2026-07-01. Net-new structural work only; existing roadmap items are
referenced by ID, never restated. Effort tiers: S ≈ hours, M ≈ 1–2 days,
L ≈ a week, XL ≈ multi-week.

---

## FIX-01 — Provenance gate: published claims must match measured code

**Status: DONE.** Machine-readable provenance blocks now live in `EVALS.md`
(HTML comment), `evals/baseline.json` (top-level `provenance`), and
`evals/govchat/golden.jsonl` (`# provenance:` header), each carrying the
prompt versions + corpus version they were generated against.
`evals/govchat_export.py` emits the golden header on every export. The
`checks` job in `.github/workflows/ci.yml` runs `python -m evals.provenance`
(also `make provenance`), which fails on any drift from HEAD unless a loud,
reasoned waiver is recorded in `evals/stale_acknowledged.json`. Covered by
`tests/test_provenance.py`.

**Pitch:** make it mechanically impossible for `EVALS.md`, the baseline, or
the audit dataset to silently describe a different system than HEAD.

**Why it matters:** the repo's thesis is "here is how I test it, honestly."
Right now `EVALS.md` records prompt versions in prose, `evals/baseline.json`
records only a date and model, and `evals/govchat/golden.jsonl` has a
hard-coded `ADDED = "2026-06-16"` in `evals/govchat_export.py` — none of them
are checked against the committed `prompts/*.txt` headers or
`assistant.corpus.corpus_version()`. The live instance: the unmerged
`research-panel-and-roadmap` branch bumps prompts to v7/v4, and merging it
will strand a v6/v3 `EVALS.md`, a 2026-06-17 baseline, and a 2026-06-16 audit
dataset with no alarm. For a journalist or procurement reviewer, a stale
report that *looks* current is worse than no report.

**Shape of the work:** (1) emit a small machine-readable block (prompt
versions + corpus_version + run id) into `EVALS.md` from `evals/report.py`;
(2) a CI check (extend the `checks` job in `.github/workflows/ci.yml`) that
parses that block and fails when it disagrees with `config.prompt_version()`
for the four prompts or with `corpus_version()` — with an explicit,
documented `stale-evals-acknowledged` escape label so honesty about staleness
is possible without lying; (3) same treatment for `evals/baseline.json`
(store prompt versions at `--update-baseline` time in `runner.py`) and for
the GovChat export (record prompt+corpus versions per dataset, verified by
`make audit`).

**Effort:** M. **Risks/dependencies:** the gate will (correctly) be red
immediately after any prompt merge until someone with AWS credentials runs
`make eval`; the escape label must be loud, not quiet. Pairs with the
existing deferred item "live `make eval` regen after v7/v4" — it does not
replace it.

**Excellent looks like:** no commit on `main` can change a prompt, suite, or
corpus without either regenerating the affected artifacts or carrying a
visible staleness acknowledgment; a reviewer can verify the match in one CI
line.

---

## FIX-02 — Judge context fidelity: give judges what the model saw

**Pitch:** stop grading multi-turn answers with a single-turn judge.

**Why it matters:** `evals/judges.py` builds the helpfulness prompt from
`result.question` and `expected_behavior` only. The committed conv-004
failure in `EVALS.md` shows the consequence: the judge speculates about
"likely prior context" it was never shown and fails an answer that correctly
refused to determine eligibility. At 6 conversation cases, one artifact
failure is 16.7 points of a suite. This contaminates the headline number and
the calibration set.

**Shape of the work:** thread `case.get("turns")` and the replayed history
from `runner.py` into both judges; add the case `rationale` to the
helpfulness judge input (it already exists in every YAML case) so "expected
behavior: answer" cannot be misread as "should have adjudicated"; version-bump
`prompts/judge_helpfulness.txt` (v3) and `prompts/judge_groundedness.txt`
(v2) with the change note discipline the headers already use; re-label the
affected calibration rows (see FIX-03).

**Effort:** S–M code, but validation is live-gated (a full `make eval` and a
calibration refresh). **Risks:** judge prompts are calibrated artifacts;
changing them invalidates the current κ and requires the RR6 recalibration
anyway — sequence them together.

**Excellent looks like:** zero failures attributable to judge blindness in
the failure traces; conversation-suite verdicts reference actual prior turns;
judge-prompt changelog names conv-004 the way answer-prompt headers name
their cases.

---

## FIX-03 — Bind calibration labels to the answers they graded

**Pitch:** a human label should stop applying the moment the answer it
judged changes.

**Why it matters:** `evals/calibration.py` matches labels to run records by
`case_id`/`judge` alone. After any prompt bump (v6→v7 is pending), `calibrate`
will happily score the judge against human labels that were written about
*different answers*, and report the result as agreement. On a 16-row sample,
a few stale rows can move κ arbitrarily. This is a quiet correctness hole in
the exact number the model card leans on.

**Shape of the work:** add an `answer_sha256` (hash of the graded answer
text) to each row of `evals/calibration/judge_labels.jsonl`; `calibrate()`
skips-and-reports rows whose hash no longer matches the run's answer
(`stale_labels` count in the report block `evals/report.py` renders); a small
helper to emit label templates from a run directory so relabeling is a
mechanical pass rather than archaeology.

**Effort:** S. **Risks:** low; purely additive. **Dependencies:** builds
toward RR6 (grow the sample) without duplicating it — RR6 is about *n* and
failure coverage; this is about label validity.

**Excellent looks like:** `EVALS.md` calibration block reports n, κ, *and*
stale/skipped label counts; a prompt change can never inflate agreement.

---

## FIX-04 — Statistical treatment of eval variance

**Pitch:** turn "the headline is a band (~113 of 118)" from a prose caveat
into a measured quantity.

**Why it matters:** `docs/model-card.md` admits run-to-run movement and the
regression gate (`suite_regressed` in `evals/runner.py`) uses a hand-tuned
two-case floor to absorb it. But nothing measures the variance: no repeated
runs, no confidence interval, no paired test when a prompt changes. The
ROADMAP records a prompt attempt that "regressed other cases and was
reverted" — without paired statistics, such judgments are one-sample reads
of a noisy instrument.

**Shape of the work:** add `--replicates N` to `evals/runner.py` (same
cases, N answer+judge passes, per-case pass fractions); report per-suite mean
± a binomial interval in `EVALS.md`; for prompt A/B decisions, a paired
per-case comparison (McNemar-style flip counts) between two run directories
as a small `evals/compare.py`; recompute the regression-gate floor from
measured flip rates instead of the current guess. Cost control comes from
FIX-12 (only judge-boundary cases actually need replication; deterministic
checks are stable).

**Effort:** M code, live-gated to run (each full replicate ≈ $1.70 per
`EVALS.md`). **Risks:** cost; mitigate by replicating only the ~15 cases
whose failures have historically flipped. **Dependencies:** FIX-12 caching
makes this affordable; FIX-02 should land first so variance is not measured
on a known-biased judge.

**Excellent looks like:** every headline score carries an interval; every
prompt merge cites a paired comparison, not a single run delta; the gate's
thresholds trace to measured flip rates.

---

## FIX-05 — Multilingual parity for the guards themselves

**Pitch:** the input guards should protect a Spanish-speaking rider exactly
as well as an English-speaking one.

**Why it matters:** the product promises "Spanish answers of the same
quality" and the output-side determination detector is genuinely bilingual.
But in `src/assistant/guards.py`, the PII `dob` pattern matches only English
lead-ins ("born on|date of birth|birthday is|dob") — "nací el 3 de mayo de
1961" sails through and gets echoed into retrieval; and in
`src/assistant/domain.py`, `legal_advice` has no Spanish forms at all. This
is a privacy-equity gap, not a quality gap: the population most likely to
include LEP riders gets weaker PII protection.

**Shape of the work:** mirror every input-guard pattern family (PII lead-ins,
scope topics) into Spanish, and into Tagalog when FIX-11 lands language
detection for it; add mirrored refusal-suite cases per pattern per language
(extending `evals/suites/refusal.yaml` with a `guard_parity` block) so the
parity is a counted number like the answer-side parity table; document the
guard-language matrix in the model card.

**Effort:** M. **Risks:** regex over-matching in Spanish (accented forms,
different date orders) — the existing test style in `tests/test_guards.py`
covers this well. Distinct from RE4 (obfuscated injection probes): RE4 is
adversarial encoding, this is plain-language equity.

**Excellent looks like:** a table in the model card: guard family × language
× tested; every row tested; no guard family EN-only.

---

## FIX-06 — Late-bind the domain profile

**Pitch:** make `FPA_DOMAIN` actually switch domains at runtime, as
`docs/adapting.md` implies it does.

**Why it matters:** `guards.py:36` (`OUT_OF_SCOPE_PATTERNS`),
`retrieve.py:22` (`AGENCY_ALIASES`), and `config.py:29` (`KNOWN_AGENCIES`,
`STATEWIDE_TRANSIT_INFO`) all call `domain.get_profile()` at import time and
freeze the result in module constants. A fork that sets `FPA_DOMAIN` after
any import of these modules (a notebook, a test, a Lambda that imports in a
different order) silently runs the transit profile with a housing corpus —
the worst failure mode being *scope guards from the wrong domain*. The
generalization story (R3-3, done) is real but one import-order accident away
from lying.

**Shape of the work:** replace the module-level constants with call-time
accessors (`domain.get_profile().aliases` at use sites, or cached with an
explicit `domain.set_profile()` that invalidates dependents); add a
regression test that imports the pipeline, switches profiles, and asserts
guard/alias behavior changed; note the pattern in `docs/adapting.md`.

**Effort:** S–M (mechanical, but touches guard code, so the full offline
gate and a live smoke matter). **Risks:** `lru_cache` on
`default_retriever()` also pins the chunk set — the fix should make that
cache profile-aware or documentedly explicit.

**Excellent looks like:** `test_a_new_domain_is_just_a_new_profile` passes
*after* a mid-process profile switch, and no module holds profile state at
import.

---

## FIX-07 — Calibrated decline threshold instead of an absolute BM25 score

**Pitch:** replace `min_confidence = 4.0` with a decision rule that is
actually about the question, and prove it with a should-decline ablation.

**Why it matters:** `config.RetrievalConfig.min_confidence` is an absolute
BM25 score, and the model card itself concedes "BM25 absolute scores do not
reliably separate out-of-corpus questions from in-corpus ones." Absolute
BM25 scores shift whenever the corpus grows (every new agency changes IDF),
so the current constant silently re-tunes itself with each corpus change —
the decline behavior riders and staff rely on (and the `confidence` band
shipped for F-16) rests on an untracked moving floor.

**Shape of the work:** compute per-query normalized signals — top-1 vs
background score distribution (a z-score against a sampled question set),
top-1/top-2 margin, and fraction of query terms matched — and calibrate a
decline rule against a purpose-built labeled set of should-answer /
should-decline questions (the refusal suite's out-of-corpus cases are the
seed; extend `evals/retrieval_ablation.py`, which already has the
compare-two-configs pattern, to sweep the rule). Keep the three-layer
defense (prompt + citation guard) unchanged.

**Effort:** L. **Risks:** over-fitting the rule to 5 agencies; the ablation
must be re-run on every corpus expansion (wire it into the FIX-09 freshness
loop). **Dependencies:** none hard; more valuable before EXP-12-scale corpus
growth than after.

**Excellent looks like:** a committed ablation table (like ADR 0007's) showing
decline precision/recall before vs after; `min_confidence` gone or derived;
adding an agency does not change decline behavior on the existing set.

---

## FIX-08 — Forged-history hardening

**Pitch:** treat client-supplied conversation history as the attack surface
it is, and test it.

**Why it matters:** `web/handler.py::_parse_history` accepts arbitrary
`{"q","a"}` pairs and `answer.py::_history_block` renders them as "You
answered: …" — the model is told it previously said whatever the client
claims. The code comment calls history "context, not a trust boundary,"
which is honest, but the research pass already identified leading-question
faithfulness drift as the real frontier (R3-1/RE4), and forged history is
the *strongest* leading question available: a fabricated prior answer
("You answered: veterans ride free on all five agencies") baits
over-claiming that the output guard cannot catch, because over-claims are
not determination phrases.

**Shape of the work:** (1) integrity option: the handler returns an HMAC
(keyed per deployment) over each `(q, a)` turn it serves, and
`_parse_history` drops turns whose tag fails — server-issued context only,
still zero server-side storage; (2) eval option regardless: a
`forged_history` block in `evals/suites/conversation.yaml` where history
contains false prior answers and the checks assert the new answer re-grounds
(cites current passages, contradicts the forgery or declines); (3) document
the boundary in `SECURITY.md`'s deployment checklist.

**Effort:** M (HMAC S, eval cases M since they are judge-scored).
**Risks:** HMAC adds a key-management footnote to an otherwise secretless
demo — keep it optional (`FPA_HISTORY_HMAC_KEY`), default off for the demo,
recommended in the hardening checklist. **Dependencies:** live judge for the
eval half; extends RE4 rather than duplicating it (RE4 is question-side,
this is transcript-side).

**Excellent looks like:** a documented, tested statement: "a client cannot
make the assistant inherit a claim it never made" — either cryptographically
(HMAC on) or behaviorally (forged-history suite green).

---

## FIX-09 — Close the freshness loop end to end

**Pitch:** make the weekly corpus automation produce the changelog, the
version bump, and the eval-staleness analysis it was designed around —
today it only opens a noisy PR.

**Why it matters:** `corpus-freshness.yml` triggers on `git diff -- corpus/`,
which includes `corpus/raw/*.html` — any nav-bar or tracking-pixel change in
agency page furniture opens a PR even when no policy text changed (the
ingest boilerplate stripping happens *inside* the diff, but raw snapshots
still churn). Meanwhile `assistant/corpus.py::diff_corpus` and the seeded
`corpus/CHANGELOG.md` exist precisely for this workflow and are never
called by it. And when policy *does* change, nothing points at the eval
cases that now assert stale facts — the PR body just asks a human to
"check whether any eval case asserts a stale fact."

**Shape of the work:** (1) key the workflow's changed/unchanged decision on
`corpus_version()` over processed chunks, not the raw tree; (2) run
`diff_corpus(old, new)` and write the changelog entry + put the
added/removed/changed doc list in the PR body; (3) an eval-case staleness
linter: for each case in `evals/suites/*.yaml`, check its `required_facts`
(and `forbidden_content`) still appear in (or stay absent from) the new
chunks of the case's `agency_scope`, and list violations in the PR —
deterministic, no model needed; (4) surface "policies fetched N days ago
against a staleness budget" in `/version` (the UI already shows age).

**Effort:** M. **Risks:** the linter needs the same regex/`re:` handling as
`evals/checks.py` — reuse that code, do not reimplement. **Dependencies:**
none; multiplies the value of P1-1 (done) and RE7 (rider-readable
changelog) without overlapping either.

**Excellent looks like:** a real agency fare change arrives as a PR whose
body contains the doc-level diff, the changelog entry, and the exact case
IDs to review; furniture-only churn opens nothing.

---

## FIX-10 — Retire `unsafe-inline` from the demo CSP

**Pitch:** the security headers are strict everywhere except the one place
that executes code.

**Why it matters:** `web/handler.py::_SECURITY_HEADERS` ships
`script-src 'unsafe-inline'; style-src 'unsafe-inline'` — necessary today
because `web/index.html` (440 lines) carries its JS and CSS inline. For a
demo this is defensible; for the `/embed` widget that agencies are invited
to put on their own fare pages, `unsafe-inline` script is exactly what a
deployment reviewer at a government IT shop will flag first, and
`SECURITY.md` markets a hardening checklist. XSS in an embedded civic widget
is a liability-candor issue, not just hygiene.

**Shape of the work:** move the inline script/style to hashed form
(`script-src 'sha256-…'`, computed at build/test time so `a11y.py`-style
static checks can assert the hash matches the file) or serve them as
same-origin files from the Lambda with SRI; do the same in `web/embed.py`;
add a header-assertion test beside the existing frame-header tests in
`tests/test_web.py`; document residual inline allowances (if any) in
`SECURITY.md`.

**Effort:** M. **Risks:** hash drift on every HTML edit — automate hash
computation in a tiny build step or test fixture, never by hand.
**Dependencies:** none.

**Excellent looks like:** CSP with no `unsafe-inline` on any route; a test
that fails if a script edit forgets the hash; SECURITY.md checklist row
flipped from "known allowance" to "enforced."

---

## FIX-11 — Robust, extensible language identification

**Pitch:** replace the two-regex word-count heuristic with a small
deterministic classifier that can say "Spanish", "English", "Tagalog", or
honestly "unsure".

**Why it matters:** `guards.detect_language` counts EN vs ES marker words
and returns `"es" if es > en else "en"`. It drives three load-bearing
behaviors: refusal language, the `language_match` eval check, and the
retrieval language boost. It has no confidence, mislabels short or
code-switched questions ("cuanto cuesta el day pass?"), and structurally
cannot support the Tagalog path — the research log records that the
stretch-language suite was *deliberately not added* because
`detect_language` knows only en/es. Language ID is currently the single
hard blocker on multilingual expansion (RE1) that is fixable offline.

**Shape of the work:** a dependency-free character-n-gram scorer trained
offline on a small committed sample (or hand-built n-gram profiles for
en/es/tl — this corpus's domain vocabulary is tiny), returning
`(lang, confidence)`; below-threshold → answer in English with a translated
"I wasn't sure of your language" line via the existing gettext catalogs
(`assistant/i18n.py` already has the fallback chain); mirrored tests with
short, mixed, and accented inputs; only then wire the TL refusal fallback
the research pass scoped.

**Effort:** M. **Risks:** never let uncertain detection *block* an answer —
fallback must be graceful; keep the function deterministic and offline.
**Dependencies:** unlocks the live-gated halves of R2-3/RE1; FIX-05 parity
patterns should key off the same language set.

**Excellent looks like:** documented precision on a committed mixed-language
test set; `language_match` failures caused by misdetection go to zero;
adding a language is a data file, not a code rewrite.

---

## FIX-12 — Case-level result caching and parallelism in the runner

**Pitch:** cut full-run wall time (850s) and cost ($1.70) enough that
replicates, A/Bs, and pre-merge full runs become routine instead of
precious.

**Why it matters:** `evals/runner.py` is a serial loop; every run re-pays
every answer and every judge call even when nothing that affects a case
changed. The consequence is cultural, not just financial: the ROADMAP's
"prompt changes need a live gate" discipline is expensive to obey, which is
exactly how the v7/v4 bump ended up merged-to-branch but unvalidated. Cheap
runs are what make the honesty policy sustainable.

**Shape of the work:** (1) a content-keyed answer/judge cache under
`evals/cache/` keyed on (provider, model id, prompt versions, corpus
version, question/turns) — the same determinism argument the handler's LRU
already relies on ("answers are deterministic (temperature 0)", noting the
model card's caveat that Bedrock is *not perfectly* deterministic, so cache
use must be labeled in the run summary and disabled for FIX-04 variance
runs); (2) bounded-concurrency execution (the pipeline is pure functions
over an immutable retriever; `ThreadPoolExecutor` at 4–8 fits Bedrock rate
limits); (3) `--only-failed` and `--since <run>` conveniences.

**Effort:** M–L. **Risks:** caching across a non-deterministic backend can
mask flakiness — mitigated by explicit cache labeling in `summary.json` and
by FIX-04 measuring the flakiness for real; concurrency must not interleave
the multi-turn history replay within a case. **Dependencies:** FIX-04
consumes this; the provenance keys come from FIX-01's machinery.

**Excellent looks like:** an incremental re-run after a one-prompt change
touches only affected cases and finishes in low tens of seconds; run
summaries state cache hit rates; the live-gate discipline stops being a tax.
