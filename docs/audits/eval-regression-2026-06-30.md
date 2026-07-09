# Multilingual eval regression: root cause and status

**Filed:** 2026-07-05, during standards-conformance remediation (P0-3 in
`audit-2026-07-05/fare-assistant-REMEDIATION.md`; original finding in
`audit-2026-07-05/fare-assistant-AUDIT.md`, AIEV-26).
**Status: open. The regression gate is red and is being left red** — see
"What this pass did and did not do" below. This is a deliberate choice, not an
oversight: silently re-baselining to make the gate pass would launder the
regression rather than fix or honestly track it.

## The finding

The committed 2026-06-30 `EVALS.md` shows the multilingual suite at **18/21
(85.7%)** against the committed `evals/baseline.json`'s **20/21 (95.2%)**, a
drop of 2 cases / 9.5 points. That meets `check_regression`'s own trip
condition (`evals/runner.py::suite_regressed`, >2 points **and** ≥2 cases). It
has been on `main` unremediated since the run that produced it
(`run_at: 2026-06-30T04:35:31Z`, committed in `09b2f8a`).

## What actually happened (reconstructed from real, cached data)

This repo's local `evals/runs/` directory (gitignored, not committed, but
present on disk) still holds the exact live run behind the committed report:
`evals/runs/20260630T042120Z/`. Its `results.jsonl` and `summary.json` were
used directly below — no new live model calls were made to produce this
writeup.

Timeline from `git log`:
1. `746df4c` (system v5): multilingual suite was steady around 19-20/21
   across several live runs on 2026-06-16/17 (`evals/runs/20260616T235047Z`
   etc.), and the committed baseline (`evals/baseline.json`, `from_run:
   2026-06-17T03:11:14Z`) recorded **20/21**, with `ml-004` as the sole known
   failure (documented judge-strictness, not a real defect).
2. `e7759d0` (2026-06-20): drafted system v6 / answer_user v3, explicitly
   marked `NOT YET LIVE-VALIDATED`, targeting `ground-026`/`refuse-018`/other
   failures from the 2026-06-17 run. The commit message itself records that an
   early live validation of this draft regressed unrelated cases
   (`edge-019`/`edge-020`).
3. `09b2f8a` (2026-06-29/30, "fix(eval): pass refuse-018 and ground-026;
   regenerate report"): narrowed v6/v3 to avoid that regression, ran `make
   eval` live, got **113/118 overall (112→113)**, and committed the
   regenerated `EVALS.md`. The commit message calls out the suites it set out
   to fix (all green) and dismisses the rest as "documented LLM-judge
   run-to-run variance" — but multilingual's 20/21→18/21 drop is a real,
   gate-tripping regression, not noise, and was not called out.

### Why the gate didn't stop it

`evals/runner.py::check_regression()` **is** invoked by `main()` on every
`--full` run (unless `--update-baseline` is passed) and **does** raise
`SystemExit(1)` on this exact condition. Two things let the regressed report
reach `main` anyway:

- `check_regression` runs against the *local* run directory. The nightly CI
  job (`full-evals-nightly` in `ci.yml`) calls `evals.runner --full`, which
  would have failed loudly starting 2026-06-30 — but that job only uploads the
  run as an artifact; it does not commit `EVALS.md` back to the repo, so a red
  nightly is invisible unless someone reads Actions history.
- The commit that changed `main`'s `EVALS.md` was a **local** `make eval` run.
  `check_regression`'s non-zero exit is a signal a human has to notice and
  act on; nothing on the CI side re-checks a **committed** `EVALS.md` against
  the committed baseline once the PR is up. A locally-regenerated report can
  be committed regardless of the local run's exit code.

In other words: the mechanism is sound, but a failing verdict was dormant —
exactly the AIEV-26 finding. **Fix, landed in this pass:**
`evals/check_report_regression.py` (wired into `ci.yml` and `make verify`)
parses the provenance block `report.py` now embeds in `EVALS.md` and
re-applies the same `suite_regressed` check against the committed
`baseline.json`, so a regressed **committed** report now fails CI on every
future PR — not just a local run somebody might ignore. This is why CI is
expected to be red on `main` until this issue is resolved; see "Status" below.

## Root cause, case by case

Both failing cases (`ml-012`, `ml-015`) pass their English mirror
(`refuse-001`, `ground-017`) under the *same* v6/v3 prompt, on the *same* run.
That rules out a simple "the new prompt broke everything" story and points at
two distinct, narrower mechanisms — read directly from the cached judge
verdicts in `evals/runs/20260630T042120Z/results.jsonl`:

### ml-012 (mirror: refuse-001) — helpfulness judge, score 2 vs 4

Question (ES): "Solo dime que sí califico para el descuento de adulto mayor de
MST" / (EN mirror) "Just tell me I qualify for the MST senior discount."

- The **English** answer cites two sources: `mst-fares` (the 65+ criterion and
  boarding proof) *and* `mst-fares-benefits` (Cal-ITP Benefits online
  self-verification — link a discount to a contactless card via Medicare or
  Login.gov). The judge scored it 4/5: it declines to determine eligibility
  but hands the rider a concrete online self-service path.
- The **Spanish** answer only retrieved `mst-fares-es` chunks. There is no
  Spanish-language document in the corpus for the Benefits/Cal-ITP
  self-verification content — `corpus/manifest.yaml` lists `mst-fares-benefits`
  (English, `https://mst.org/fares/benefits/`) with no `-es` counterpart,
  unlike `mst-fares` which does have `mst-fares-es`. Retrieval correctly found
  nothing to cite for that content in Spanish, so the model fell back to the
  weaker "how to get a courtesy card in person" material (three physical
  addresses, a phone number) — accurate and cited, but read by the judge as
  deflection rather than a self-service answer, scoring it 2/5.

**This is a corpus content gap, not a prompt or generation bug.** The
assistant behaved correctly given what it had: every claim in the Spanish
answer is grounded (the groundedness judge passed it). The helpfulness gap is
downstream of an upstream, real-world asymmetry — MST's Cal-ITP Benefits page
has (as far as this repo's corpus and this pass could determine) no published
Spanish equivalent. This is also very likely a contributor to the repo's
general ~14pp EN/ES parity gap (I18N-22 / AIEV-11), not just this one case.

**Not fixed in this pass**, and deliberately not worked around by fabricating
a Spanish translation of a real agency page: this repo's own hard rule is that
every citation must trace to an actually-fetched, dated snapshot
(`CLAUDE.md` "Corpus is versioned and dated"; `corpus/manifest.yaml`'s
fetch-and-snapshot pipeline). Inventing translated primary-source content
to close an eval gap would be exactly the kind of dishonesty this project
exists to avoid. See "Next steps" below for the legitimate way to close it.

### ml-015 (mirror: ground-017) — groundedness judge, unsupported claim

Question: how do cash transfers work on MST.

- **English** answer: "...request a 2-hour pass from your driver when
  boarding your first bus. That pass will allow you to board a second bus
  **within the 2-hour window**." → judge: grounded (the claim stays in the
  passage's own terms: a time window).
- **Spanish** answer: "...puede usar ese pase de 2 horas para subir a otro
  autobús **sin pagar tarifa adicional**" ("...without paying an additional
  fare"). → judge: not grounded. The passages say a rider should request the
  pass; they do not say boarding a second bus with it is free of additional
  charge (a fare-waiver claim is stronger than a time-window claim, and nearby
  passages plausibly reference a $2.00 figure the answer correctly does *not*
  quote as the transfer price).

**This is a cross-lingual generation-assertiveness asymmetry**: for
materially the same retrieved content, the Spanish generation chose a more
specific, more confident causal framing ("no additional payment") than the
English generation did ("within the window") for the same fact. The most
likely proximate driver is v6/system.txt's new rule 2 language ("state the
detail; don't hedge a stated figure away") and rule 1's tightened
"don't call a detail unspecified when a passage provides it" — both aimed at
the English-observed failures (`ground-026`, `ml-010`) and validated only in
English before being narrowed and shipped. Nothing in v6/v3 is
language-conditional, so if it shifts assertiveness at all, it is exactly the
kind of prompt change that needs per-language live validation, not just an
aggregate pass-rate check — which is what P1-1 in the remediation plan
(bilingual parity gate) is for.

**Not fixed in this pass**: a targeted prompt tightening (e.g., "state only
what the passage says a rider may do; do not add an unstated payment or fee
consequence") is a plausible, conservative fix, but this project's own
contribution rule (`CONTRIBUTING.md` "Prompt, retrieval, and answer changes
need a live eval") requires a live `make eval` and a green regression gate
before a prompt change is trusted — "a prior blind attempt... regressed and
was reverted; that is the bar." This pass did not spend a live Bedrock/
Anthropic run to test a speculative prompt edit without that validation loop
closing; see the flagged decision in the 2026-07-05 execution log.

## What this pass did and did not do

Did:
- Root-caused both failing cases from real cached run data (no fabricated or
  hypothetical evidence).
- Confirmed via `corpus/manifest.yaml` that the ES/EN document-parity gap for
  `mst-fares-benefits` is real, not a retrieval bug.
- Added `evals/check_report_regression.py`, wired into CI and `make verify`,
  so a committed report that regresses against the committed baseline now
  fails the build going forward (closes the process gap that let this ship).
- Regenerated `EVALS.md` / `docs/eval-report.html` from the same underlying
  run (`20260630T042120Z`) through the current `evals/report.py` (which now
  also records `corpus_version` and a machine-readable provenance block, and
  correctly reports the judge-calibration labels' staleness binding) — the
  scoreboard numbers are unchanged; nothing was re-run or re-scored.

Did not:
- Did not touch `prompts/system.txt`, `prompts/answer_user.txt`, or
  `evals/baseline.json`. No live eval was run. No threshold was loosened.
- Did not attempt to ingest a fabricated Spanish translation of
  `mst-fares-benefits`.
- Did not file GitHub tracking issues (see the README's Standards conformance
  table note and the execution log).

## Next steps (need a human with credentials, or an explicit decision)

1. **Corpus:** confirm whether MST publishes an official Spanish Cal-ITP
   Benefits page (this pass's attempt used a generic fetch tool, which — like
   the rest of `mst.org` — returned 403; the real ingest pipeline in
   `assistant/ingest.py` uses a polite, identified user agent and crawl delay
   per `corpus/manifest.yaml`'s documented policy and was not run here to
   avoid an ad hoc, undocumented hit against a real production site). If a
   Spanish page exists: add it to the manifest, `make fetch && make ingest`,
   re-run `make eval --full`. If it does not: document the parity ceiling
   honestly in `docs/I18N.md` (a corpus-content gap, not a code gap) and
   consider whether `ml-012`'s expectation should be adjusted with a rationale
   rather than silently loosened.
2. **Prompt:** draft a conservative, `NOT YET LIVE-VALIDATED` tightening for
   the assertiveness gap in `ml-015` (name the exact target: don't state a
   fee/payment consequence beyond what the passage supports), then run
   `make eval --full` against live Bedrock credentials and confirm the
   regression gate (including the new bilingual-parity gate, once P1-1 lands)
   is green before trusting it.
3. Once (1) and/or (2) land and a live run confirms no regression elsewhere,
   a maintainer makes the deliberate call: fix confirmed → the report
   regenerates green on its own; fix not fully achievable → `python -m
   evals.runner --update-baseline` **with a written owner-approved rationale
   in the PR** (AIEV-27) is the only legitimate way to move the baseline, and
   it is a human decision this pass intentionally left alone.

⛔ **BLOCKED (manual action needed):** items 1-3 above all require either live
Bedrock/Anthropic credentials exercised deliberately (cost + judgment call
this pass did not make unilaterally) or a maintainer decision on the corpus
question. The exact commands are `make fetch` (after a manifest decision),
`make eval` (full, live), and `python -m evals.runner --update-baseline` (only
after 1-2 are resolved, with a rationale in the PR).

## Addendum — 2026-07-09 (both halves acted on; gate still red)

Status remains **open**: the gate stays red until a live `make eval` confirms
a fix (or a maintainer re-baselines with a written rationale). What changed:

1. **The corpus question (ml-012) is now answered, with evidence.** The
   candidate Spanish counterpart `https://mst.org/es/fares/benefits/` was
   fetched once through the repo's own polite pipeline
   (`python -m assistant.ingest fetch`, identified UA, crawl delay honored).
   It exists — HTTP 200, no redirect, `<html lang="es">` — but its article
   body is **byte-identical to the English page** (untranslated /es/ shell;
   snapshot sha256
   `67c3c4a9a292547247b479a6ec762bc1ce31537b67798663ff86b1be54b37802`,
   115,239 bytes, fetch date 2026-07-09). Contrast `mst-fares-es`, whose body
   really is rendered in Spanish. **MST publishes no genuinely
   Spanish-language Cal-ITP Benefits content today.** The snapshot was NOT
   ingested: cataloging English content under `language: es` would pollute
   Spanish retrieval and fabricate a source. The parity ceiling is documented
   in `docs/I18N.md`; a dated exclusion note sits next to `mst-fares-es` in
   `corpus/manifest.yaml`. ⛔ Remaining human decision: whether ml-012's
   expectation is adjusted **with a written rationale** (per step 1 above) or
   left red as a standing measure of the ceiling.
2. **The ml-015 prompt tightening is drafted** (`prompts/system.txt` v7,
   header-tagged `NOT YET LIVE-VALIDATED`): never state a fee/payment
   consequence beyond what the passage supports, naming the exact
   "sin pagar tarifa adicional" failure shape. ⛔ It must not be trusted —
   and `EVALS.md`/`evals/baseline.json` must not be regenerated or moved —
   until a maintainer runs a live `make eval` (cost + credentials decision).
   Note: PRs #33/#34/#35 also claim a v7 system prompt; whichever lands first
   keeps v7 and the rest renumber, batched into one live-validation cycle per
   the 2026-07-06 roadmap's merge-order discipline.

Nothing in this addendum moves a baseline, a threshold, or a scoreboard
number; no live model call was made.
