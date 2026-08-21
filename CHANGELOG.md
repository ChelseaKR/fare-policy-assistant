# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
does not yet cut tagged releases (see the "Standards conformance" table in
`README.md` for the release-and-versioning declaration), so entries are dated
rather than tied to a published tag.

## [Unreleased]

### Security
- Update the optional dense-retrieval toolchain to Torch 2.13.0 and setuptools
  83.0.0, clearing the setuptools path-traversal advisory while preserving the
  existing Python and sentence-transformers compatibility range.
- Update the locked Pillow dependency from 12.2.0 to 12.3.0, clearing the
  image-decoder advisory cluster reported against PDF/OCR support.
- Update the locked pypdf dependency from 6.13.3 to 6.14.2, clearing four
  malformed-document denial-of-service advisories in optional PDF ingestion.
- Guard the current question and client-held prior questions before history
  parsing or cache access; cache only successful answers under process-local
  keyed digests, never plaintext rider text or refused/guarded payloads.
- Recognize compact, spaced, and hyphenated SSN, phone, and Medicare identifier
  formats before retrieval/model use. Only successful supported answers enter
  browser follow-up history, and signed turns are bound to the corpus version
  and disabled-source state so stale policy context cannot be replayed.
- Fail closed when a citation tag is malformed or names a document outside the
  exact retrieved evidence set; single and combined citation tags now share one
  grammar through enforcement, structured extraction, and public rendering.
  Production contains the expired `yolobus-fares` snapshot through an
  operator-visible source kill switch.

### Changed
- **Widened passage provenance in `results.jsonl`, the human relabeling
  worksheet, and the report's failure traces (#142).** Until now the judge
  saw a passage's `(source: …, fetched …)` header (fixed 2026-08-16 for
  `fresh-001`) but two other renderers of the same retrieved passages did
  not: `evals/calibration.py::_passages_block` (what `make relabel` shows a
  human reviewer) and `evals/report.py`'s "Retrieved passages" block in
  `EVALS.md`/the HTML report. Both read from the `passages` array persisted
  in `results.jsonl` by `evals/runner.py`, which only ever carried
  `chunk_id`/`section`/`score`/`text` — the dates were never recorded, so no
  amount of template editing in the two renderers could have shown them.
  `results.jsonl` now also records `doc_id`, `agency`, `doc_title`, `url`,
  and `fetch_date` per passage; both renderers show them. Without this, a
  reviewer working the judge-calibration worksheet (#143) would be asked to
  judge a dated freshness claim against undated passages — the exact
  condition that produced the wrong judge verdict on `fresh-001` — and a
  human agreeing with a wrong verdict for the same missing-provenance reason
  would not calibrate anything. Landed ahead of the labeling pass, per #142's
  own note ("do this before the 37 rows are labeled, not after").
- **Fixed a retrieval bug behind two of #138's nine named cross_agency
  failures (#150): an agency's own age/eligibility-criterion passage could
  be crowded out of retrieval by that same agency's payment-method chunks.**
  Reproduced offline against the real corpus: for a plain single-agency
  question ("I'm 70 years old. What senior discount do I get on AC Transit,
  and how do I pay for it?"), `actransit-discounts#1` ("Riders aged 65 and
  older ... eligible for Senior/Disabled fares") ranked outside AC Transit's
  own `top_k=8` entirely — six chunks about passes, Clipper START, and "Ways
  to Pay on Tempo" outrank the short criterion sentence on BM25 because they
  repeat fare/discount/payment vocabulary more densely. On a two-agency
  comparison (`xagency-actransit-001`) the per-agency retrieval quota
  compounds the same crowding. `Retriever._ensure_eligibility_passage`
  (`src/assistant/retrieve.py`) mirrors the existing `_close_the_loop`
  companion-passage pattern: on a reduced-fare/eligibility query, append the
  best-ranked age/eligibility-criterion passage per named agency if it fell
  out of the retrieved set, the same way `_close_the_loop` already does for
  the where-to-apply passage. Verified two ways: seven new `tests/test_retrieve.py`
  cases pin the exact reproduction, and the project's own offline retrieval-recall
  method (`evals/retrieval_ablation.py`'s approach, run manually across all 333
  eval cases with `required_facts`) went from 309/333 to 313/333 with zero new
  misses — a strictly non-regressive improvement (the extra 4 beyond the two
  targeted cases came from a regex fix needed along the way: `\d{2}\s*\+\b`
  never matches "65+" followed by whitespace or a newline, since neither side
  of that position is a word character, so `\b` cannot assert there).
  `xagency-010` (the corpus-wide Clipper-participant enumeration question,
  also in #150) is untouched — its question names no agency and matches none
  of the reduced-fare trigger vocabulary, so this companion step never runs
  for it; #150 already flags it as a design decision (retrieval strategy for
  enumerative questions vs. a documented decline) rather than a retrieval bug,
  and this change does not resolve or presume that decision.
- **CQ-05: turned on ruff's C90 (mccabe complexity) at `max-complexity = 10`,
  the standard's own gate, instead of leaving it off.** Draft PR #89 tried to
  land this with an in-place refactor in July; by the time issue #137 measured
  it in August the branch was 54 commits behind main, its own six-file
  refactor no longer cleared the gate against the current tree (11 residual
  violations in files it never touched), and the corpus/prompt surface it
  restructured had grown functions the branch predated. Closed as stale
  rather than force a rebase that would have re-derived the refactor blind.
  This lands the gate itself first, per #137's phased plan: `C90` is selected
  and enforced repo-wide today, with a per-file `[tool.ruff.lint.per-file-ignores]`
  ceiling holding the 44 functions currently over 10 (one file, one entry
  each) so the rule blocks *new* complexity immediately without a large,
  risky single-PR refactor. Retiring the ignore list is follow-up work,
  biggest offender first (`evals/runner.py::_run_resolved` at 49, a third of
  the debt by itself and the function the eval harness's correctness rests
  on — worth doing carefully and alone, not blocked on this PR).
- **Corrected a false license assertion. Every one of the 195 rows in
  `evals/govchat/golden.jsonl` stamped `"license": "public record — California
  transit agency fare policy pages"` over quoted agency text.** "Public record"
  is a disclosure status under the California Public Records Act, not a
  copyright license and not a public-domain dedication, and it contradicted the
  "all rights reserved" notices inside the very pages being quoted. It was this
  project's only affirmative grant over third-party text, it was
  machine-readable, and it sat in the dataset most likely to be reused
  downstream. The field now states the non-grant: the text remains the
  respective agency's copyright, reproduced as short excerpts for evaluation and
  analysis, not licensed for redistribution. Fixed at the generator
  (`evals/govchat_export.LICENSE_NOTE`) and applied with a new
  `make audit-restamp-license`, which rewrites that field and nothing else, so
  the 195 recorded answers and the dataset's provenance header stay exactly as
  they were recorded. A test now fails if the committed dataset drifts from the
  generator's note.
- Widened the `NOTICE` carve-out from `corpus/raw/` alone to every location
  third-party agency text actually lives: the processed markdown and chunks, the
  schema-2 snapshots, retained version chunk sets, the golden dataset's
  `sources[]`, the judge label packet, and the excerpts inside `EVALS.md` and
  `docs/eval-report.*`. As written, MIT purported to sublicense roughly 1.5 MB
  the project cannot sublicense.
- Restated the second-harness audit accurately in the README, the model card,
  the procurement brief, and `docs/audits/methodology.md`. GovChat-Eval is a real
  and genuinely separate harness, blind to this system's internals, and it is
  also written by the same author, private, and unrunnable from outside
  (`make audit` needs a local clone; the CI job is scheduled-only). "Independent
  audit" was a claim a skeptical reader would test and find wanting; it now reads
  as a second-harness replay of committed answers.
- Marked every link to the private `portfolio-standards` and `govchat-eval`
  repositories as private, so a reader who gets a 404 knows the page is
  access-controlled rather than missing.
- **Corrected a published claim: the counterfactual sensitivity line said "13/15
  boundary pairs correctly distinguished" and now says "13/15 boundary pairs
  passed", with a second number beside it.** A pair passes when both of its
  variants pass their own checks. Whether the two answers came out *different* —
  which is what "distinguished" asserts and what the whole suite exists to show —
  was never measured. `evals/runner.py::pair_discrimination` measures it by
  replaying each variant's recorded answer through its sibling's required facts
  and forbidden content: a pair whose answers are mutually interchangeable
  demonstrates nothing about its boundary, however green it scored. On the
  promoted run **11 of 15 pairs produce answers the per-variant checks can tell
  apart**; `sens-003`, `sens-011`, `sens-013`, and `sens-015` do not, and the
  report now names them. Both numbers come from the same recorded answers: 13/15
  did not change and nothing was re-scored, but it now reads as what it is.
  Reported and not yet gated — gating it today would fail the committed report
  with no credentialed run available to regenerate it, and a gap that is
  published is not a gap that is hidden.
- `EVALS.md` and `docs/eval-report.html` were regenerated from the same promoted
  2026-07-12 run to carry that line. No re-scoring; the scoreboard, parity
  table, calibration section, and failure traces are byte-identical.
- The structural accessibility gate covers every public page instead of the
  chat page alone. `/embed` is what an agency puts on its own fare page, and
  `/offline` and `/guide` exist for riders with no signal at the stop or who
  would rather browse than type — the audience least able to route around a
  problem, and the pages nothing was watching. All three passed on the day the
  gate was widened, so this fixes no present defect; it stops a future one from
  shipping unnoticed. All three render from the committed corpus with no
  network and no credentials, which is why they can be checked on every pull
  request.
- The "Sources" caption under an answer is a heading now (`<h3>` on the chat
  page, `<h2>` on `/embed`, whose only other heading is its `<h1>`), styled to
  keep the inline bold caption's appearance. It was a `<strong>`, which is not
  a screen-reader heading-navigation target — and where an answer came from is
  the thing a screen-reader user goes looking for. The recorded transcript the
  independent audit grades already used `<h3>Sources</h3>`; the page riders
  actually use was the less accessible of the two.
- Re-verified every code claim in the `a11y-walkthrough.md` pre-audit against
  HEAD and corrected one drift (the focus ring is 4px with a 3px offset, not
  3px). **The manual screen-reader pass is still not done**, on any of the four
  pages, and none of the above is a substitute for it.
- **Corrected a published number: judge-vs-human Cohen's κ was 1.000 in
  `EVALS.md` and is now reported as undefined.** Cohen's κ is 0/0 when both
  raters give the same verdict every time; returning 1.0 there is a convention
  that reads as perfect agreement measured. It was not measured. Both labels in
  the sample that recorded a human/judge *disagreement* (`ml-004`,
  `ground-024`) had gone stale when a prompt bump changed their answers, so the
  four labels left were the agreeing half of the set and could not have
  produced any other number. `EVALS.md` was regenerated from the same promoted
  run — no re-scoring, only corrected presentation — and now says so.
- The calibration section publishes the shortfall as a number rather than as
  the adjective "small": 4 scored labels against a floor of 37, which is
  `CLAUDE.md`'s 10% sample over the 367 (case, judge) pairs the promoted run
  judged. It also states plainly when a sample contains no disagreement at all,
  because a reader cannot tell that apart from a clean 100%.
- `EVALS.md`'s header distinguishes a live run that called the provider from
  one served entirely from cache. The promoted 2026-07-12 baseline was
  published as `(full, live)` while all 553 of its answer and judge calls were
  cache hits: the answers are real completions recorded under byte-identical
  prompts, which is what makes the run valid regression evidence (ADR 0022),
  but no call was made and the $3.16 on the cost line is a list-price estimate
  over reused tokens, not money spent that day.
- Closed a circularity hole in the calibration tooling. `--emit` pre-filled
  each template's `human_passed` with the judge's own verdict "as a
  placeholder", so a relabeling pass that accepted the defaults would have
  graded the judge against itself and reported perfect agreement while
  measuring nothing — and nothing detected it. Templates now emit a null
  verdict, and `load_labels` refuses any row still carrying the TEMPLATE marker
  or a non-boolean verdict.
- Added `python -m evals.calibration --worksheet <run_dir>`: a floor-sized,
  deterministic relabeling worksheet that takes every pair the judge failed
  first, then fills round-robin across suites. The committed sample had 14 of
  its 16 labels on pairs the judge had passed, which is how it ended up unable
  to disagree. The generated worksheet is committed at
  `evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl` — 37 rows, 9 of
  them judge-failures, all nine suites represented, **every verdict blank
  because only a person can fill them in.** This is queued work, not evidence.
  It also replaces a README pointer to
  `judge_relabel_worksheet_2026-07-11.jsonl`, a file that never existed.
- Every `mirror_of` declaration is now gate-checked before a run makes its
  first model call: a mirror must name a real case, in a different language,
  scoped to the same agency, expecting the same behavior, and carrying at least
  as many `required_facts` as the case it mirrors. The bilingual parity gate
  publishes a points delta between mirrored cases, and that delta is only an
  equity measurement if the pairs are pairs. Three of the 22 pairs in the
  promoted baseline were not: `ml-008` pointed at a case that was already
  `ml-004`'s mirror, asked a different question, and declared no required facts
  at all while its mirror had to produce "DD Form 214"; `ml-011` had dropped
  its mirror's `65` fact, so the Spanish answer never had to state the age
  criterion the English answer did; `ml-022` is scoped to MST but pointed at a
  Yolobus case, so the pair measured two corpora rather than two languages. The
  gate reported a 0.0-point gap across all three.
- Corrected the three pairs (`ml-008` now mirrors `edge-048`, `ml-022` mirrors
  `edge-045`, `ml-011` regained the `65` fact) and gave
  `parity-scope-medical-es` the `agency_scope` its declared mirror carries.
  Replaying the promoted run's recorded answers through the repaired map leaves
  every verdict unchanged and the parity delta at 0.0 points over 22 pairs.
  **The published number did not improve; what it certifies did.** Before the
  repair, 0.0 points was computed over 19 real pairs and 3 mismatched ones, and
  a weaker Spanish case passing more easily read on the scoreboard as equity.
- Corrected the README's Status section, which had said since 2026-07-05 that
  the EN/ES answer-quality gap exceeded the project's own ≤5-point target. The
  mirrored-case gap has been 0.0 points since the 2026-07-12 run. What is
  actually outstanding is the `docs/I18N.md` §7 native-Spanish benchmark, which
  has never been measured — an unmeasured property, not a failing one, and not
  a passing one either.
- Known-stale artifact, stated rather than fixed: `evals/govchat/golden.jsonl`
  still carries the pre-repair `pair_id` values for `ml-008`, `ml-011`, and
  `ml-022`, so the independent audit's 0.581 multilingual anchor-fidelity score
  was computed over those three mispairings too. Regenerating it requires a
  credentialed `make audit` recording run; the provenance gate does not flag it
  because the export's prompt and corpus versions are unchanged.
- CI persists the content-keyed answer/judge cache between runs, so a pull
  request that cannot change an answer no longer re-buys the smoke suite's
  model calls, and a merge to `main` no longer re-scores the tree its own pull
  request scored minutes earlier. Six of seven nightlies are served from that
  cache; the seventh runs cold under the new `--refresh-cache` flag, which
  re-measures the provider and rewrites the stored answers so the next cached
  night cannot republish results the cold run contradicted. The generated
  report names how many calls a run reused, so a near-zero cost line reads as a
  reused result rather than a broken meter. Suite composition, the regression
  gate, the parity gate, and the deterministic checks are unchanged; what is
  traded is provider-drift detection on six nights out of seven. See
  `docs/decisions/0022-persisted-eval-cache-and-weekly-cold-run.md`.

### Added
- **Nine agencies in one coordinated expansion (2026-08-13/14).** County
  Connection (CCCTA), San Joaquin RTD, AC Transit, WestCAT, SLO RTA, VTA, Napa
  Valley Vine Transit (VINE), SamTrans, and Marin Transit take the corpus from
  nine agencies to eighteen: 52 documents, 301 chunks, `corpus_version`
  `3dd8b7bd757e`, and the eval suites from 258 cases to 385. The nine branches
  were written in parallel against the same base, so each merge re-derived what
  the previous one moved — the system prompt one version per agency (v11 to
  v20, each bump landing with its own corpus PR because
  `tests/test_prompt_agencies.py` fails any corpus agency the prompt does not
  name), `corpus_version` from a fresh offline ingest, the release-identity
  goldens from the test's own fixture, and the doc counts and coverage matrix.
  Two batches had allocated colliding numeric case ids; the later branch
  renumbered on merge, the FAX precedent, and nothing but ids moved.
  **Every one of these agencies is marked NOT YET LIVE-VALIDATED.** No live
  eval has scored any of the 127 new cases, the published scores predate all
  of them, and `evals/stale_acknowledged.json` records that per artifact and
  per field rather than letting it pass quietly.
- **Two candidate agencies recorded as NO-GO, with dated evidence, instead of
  quietly skipped.** RABA (Redding) fails at `robots.txt` itself: its host
  302s to a Revize CMS file whose allowlist admits four commercial crawlers
  and ends `User-agent: * / Disallow: /`, and the City of Redding, its
  administering publisher, serves the same shape. This project's fetcher falls
  under `*`, so no content page was requested from either host. Golden Gate
  Transit fails for the opposite reason: fetching is permitted and every page
  returned HTTP 200, but the site wraps its entire body in a single ASP.NET
  `<form>` that the cleaner strips as page furniture, so every page cleans to
  zero sections. Un-stripping page-wrapper forms would change the ingested
  text of agencies already in the corpus, which is a cleaner change needing
  its own PR and eval justification, not a side effect of an agency addition.
- **Cross-agency findings the expansion surfaced, attributed rather than
  harmonized.** Three agencies publish three different windows for what a
  rider experiences as the same inter-agency Clipper transfer: SolTrans says
  60 minutes, WestCAT says 120, and Golden Gate's own transfers page (read
  during the NO-GO check, never ingested) says three hours. `xagency-014`
  fails any answer that quotes one agency's window as another's. The
  Next-Generation Clipper interagency credit has the same shape: AC Transit's
  dated fares page says "up to $3" where VTA's and Marin Transit's live pages
  say "$2.85". `edge-actransit-003` forbids the $2.85 on AC Transit answers,
  and the manifest's license notes mark each agency's description of another
  operator's arrangement as that agency's characterization, not the other's
  policy. The corpus reports the disagreement; it does not resolve it.
- `corpus/LICENSE-NOTE.md`: a plain-English statement of what is in the corpus
  directory, whose copyright it is, why it is committed (re-running a dated
  evaluation), what a downstream reader may and may not assume, and each
  agency's own site and terms of use. Linked from `README.md` and `NOTICE`.
- `corpus/manifest.yaml` now records redistribution terms beside the existing
  robots.txt/Content-Signal review, with the date checked. robots.txt governs
  fetching; it says nothing about republishing. SBMTD publishes site terms of
  use; MST, Yolobus, SacRT, and HTA had none we could locate, and that is
  recorded as such rather than left blank.
- `evals/spanish_quality.py` and a committed, entirely blank rating census at
  `evals/spanish/native_es_rubric_2026-08-05.jsonl`: the half of `docs/I18N.md`
  §7 the bilingual parity gate cannot reach. That gate compares a Spanish
  answer's pass/fail against its English mirror's, and every check behind those
  two verdicts asks whether a citation resolves, a required fact appears, the
  classifier says `es`, and no determination phrase is present. Spanish that is
  stilted, wrongly registered, or full of anglicisms satisfies all of them. **The
  0.0-point parity delta would not move if every Spanish answer read like a
  machine translation, because nothing in it is looking.**
  The module publishes the rubric (fluent / register / terminology, each stated
  as the question a rater answers) and emits a **census** rather than a sample:
  all 28 Spanish answers from the promoted run, 7 marked `fixed_string` because
  they render the committed gettext refusals that `docs/I18N.md` records as
  pre-existing human translation, so the catalog is never counted as model
  output. `make spanish-quality` walks the sheet offline, one answer at a time,
  showing the Spanish on its own — no English mirror, since parity already
  compares those, and no default rating. `EVALS.md` now carries a
  **Native-Spanish answer quality** section reporting **not measured**, with the
  shortfall as a number; `summarize` returns `None` per dimension below the
  census floor rather than a percentage over answers nobody read, and a test
  asserts no zero is rendered.
  Two things are deliberately absent. No rating was authored: a model rating its
  own Spanish is the same circularity closed on the calibration templates. And
  no question set was authored: §7 asks for **externally sourced** native-Spanish
  questions, and machine-written Spanish questions are exactly what that
  excludes. Every census row therefore reads `question_source: repo_mirror`, so
  even a fully rated sheet describes the Spanish this repo writes rather than
  Spanish as riders write it — a field in the data instead of a caveat in prose.
- The harness self-test (`make eval-selftest`) plants a defect against every
  check the grader emits, not 8 of 13. The five with no planted defect were
  `language_match`, `refused`, `redirect_present`, `verification_handoff_present`
  and `structured_contract_schema_valid` — among them the only thing that makes
  the multilingual suite a language test rather than a second English suite, and
  the entirety of the refusal suite's deterministic scoring. Those two suites
  score 22/22 and 34/34; a check that has never failed and was never shown *able*
  to fail is indistinguishable from one that cannot. All five now flip from pass
  to fail on a planted defect (a Spanish case answered in English, a refusal case
  that answers, a decline that points nowhere, a criterion stated with no next
  step, a response whose kind falls outside the typed contract), and a new test
  reads the check names out of `evals/checks.py` so a future check without a
  scenario fails CI instead of quietly widening the gap again.
- The i18n catalog-parity gate (G5/G6) checks its own denominator. Every check
  in it iterates over the template's msgid set, so an empty `messages.pot` made
  all of them vacuous: the gate printed "catalog parity OK: 0 msgids" and exited
  0 while nothing rider-facing was translated. G2-lite catches a template that
  *drifts* from the sources, but a commit emptying the sources and the template
  together drifts from nothing. An empty template now fails. The committed one
  carries 7 msgids.
- The below-macro escape hatch expires on its own. `expected_below_macro.json`
  has always instructed "delete the entry the moment the suite recovers", with
  nothing behind the instruction: an annotation left over a suite that climbed
  back above the floor stayed a live waiver, sitting exactly where the next real
  regression would land and absorbing it silently. `runner.stale_annotations`
  now fails the gate on an annotation whose suite ran and is at or above the
  floor. Suites that did not run (a `--suite` subset) are out of view rather than
  stale. The one committed annotation, `conversation`, is still doing work: 80.0%
  against a floor of 89.0%.
- The committed-report regression gate catches two more ways a report can
  describe a worse system than the baseline. A baseline suite **absent** from
  `EVALS.md` used to be skipped as "a missing-provenance problem for
  `evals/provenance.py`" — but provenance compares prompt and corpus versions and
  has never looked at suite composition, so the responsibility sat with a module
  that does not implement it and a report regenerated from a `--suite` subset
  passed everything. And a suite that **shrank** was invisible: `suite_regressed`
  requires both a pass-rate drop and a pass-count drop, so deleting the two cases
  a suite fails takes it from 46/48 to 46/46 and trips neither condition.
  Deleting the failing test is the oldest way to turn a board green. Both now
  fail the gate; both share the existing escape (`--update-baseline` with a
  written rationale), so a deliberate retirement is a line in the diff.
- Every minimal pair's variants are now gate-checked before a run makes its
  first model call (`evals/runner.py::pair_problems` / `check_pairs`), the same
  shape as the mirror gate: two variants of a counterfactual pair may not demand
  identical `required_facts` and `forbidden_content`, and no variant may demand
  neither. `pair_verdicts` scores a pair as distinguished only when every
  variant passes, on the stated grounds that "the per-variant required_facts /
  forbidden_content prove the answer actually changed across the boundary" —
  which holds only if the two sides ask for different things. Two of the 15
  pairs in the promoted baseline did not: `sens-011`'s variants both required
  only "Stored Value", though its boundary is that a *monthly* pass is excluded
  from the reduced fare; `sens-014`'s both required only the 3-17 youth range,
  though its boundary is that an 18-year-old falls outside it. Both were
  reported as boundaries correctly distinguished.
- Both pairs were repaired against the corpus rather than around the gate:
  `sens-011b` must now state the exclusion, not just name the eligible product,
  and must not assert that a monthly pass is reduced-fare eligible; `sens-014b`
  must place the 18-year-old outside the range, not merely recite it. Replaying
  the promoted run's recorded answers through the repaired cases leaves every
  verdict unchanged; the suite's pass rate does not move.
- `python -m evals.calibration --review <worksheet>` (also `make relabel`): a
  labeling surface for the judge-calibration worksheet. Labeling a row means
  reading an answer, its retrieved passages, and the case's expected behavior,
  and none of that was on screen — it had to be dug out of `results.jsonl` by
  hand, 37 times, which is a fair share of why the sample has sat at 4 labels
  against a floor of 37. The command walks the unlabeled rows offline from the
  committed run, prints the judge's committed criterion, the question, the
  prior turns, the passages, and the answer, then records the reviewer's
  verdict and required one-line reason. It **never proposes a verdict**: there
  is no default, and pressing Enter re-asks rather than resolving — the same
  circularity closed on `--emit`, which used to pre-fill `human_passed` with
  the judge's own call. It **withholds `judge_said` until after the reviewer
  answers**, because a reviewer shown the verdict first confirms it, and this
  worksheet exists precisely because the previous sample could only confirm.
  It **refuses to record a verdict when the recorded answer no longer matches
  the row's `answer_sha256`**, reporting the row instead: a label pinned to an
  answer the reviewer did not read is worse than a blank. Each completed row is
  written back through an atomic replace, so an interrupted session keeps every
  verdict already given and a partly-labeled worksheet reopens where it
  stopped. The committed worksheet is still 37 blank rows, and the test that
  keeps it blank is unchanged; this removes the friction, not the human.
- Layered corpus identity and source-complete archives: a full
  `content_version` now covers every behavior-relevant stored chunk field and
  order while excluding observation date; `snapshot_version` adds verified
  URL/status/format/date/raw-digest evidence. Schema-2 archives retain exact
  raw bytes and receipts, validate every artifact in a hidden stage, and publish
  atomically before the live chunk index can change. Processing and archival
  share one validated in-memory source capture, and legacy compatibility
  archives now use the same staged, validated, immutable publication discipline.
  Git attributes preserve the exact evidence bytes across add/checkout instead
  of applying line-ending conversion. The existing `corpus_version` and
  deployment pin remain compatible during the additive rollout.
- Production smoke coverage for the separate evidence and assistant origins,
  every public GET route, security headers, PII refusal, corpus pin/source
  containment (including active Yolobus refusal and static-page removal), and a
  paid-path dated/cited answer.
- A phased improvement and expansion plan, including a fail-soft advisory
  integration contract for independently verified GTFS Scorecard artifacts.
- Privacy-safe production observability with Lambda-owned request correlation,
  canonical GenAI model/token/duration fields, token-derived estimated cost,
  explicit unpriced-call alarms, real request/model latency metrics, and an
  updated CloudWatch dashboard. A paid cache-bypassing check now captures the
  numbered candidate's actual JSON log tail, rejects content/request metadata,
  and proves the installed metric-filter grammar before `live` can move.
- Bilingual parity gate (2026-07-17, roadmap M-1; audit P1-1; AIEV-10/11,
  I18N-22). A live run now fails when the Spanish-vs-mirrored-English pass
  delta exceeds 5 points on 2 or more cases (`evals/runner.py::check_parity`),
  and `evals/check_report_regression.py` re-applies the same gate to the
  committed `EVALS.md` on every PR, reading the machine-readable `parity`
  payload or, for reports generated before this change, the rendered Spanish
  parity table. The general per-suite form (no gated suite more than 5 points
  below the macro pass rate) gates alongside it, with one loud escape hatch:
  `evals/expected_below_macro.json`, a committed suite-to-rationale map whose
  annotations render in the report (the `conversation` suite carries the first
  entry, citing its two documented forged-history failures). Stretch-language
  suites stay outside the gate per the existing P3-3 promise. `EVALS.md` will
  render the parity delta line from the next live run onward; the committed
  report is unchanged here because reports are only ever regenerated from real
  runs.

### Changed
- Relicensed MIT → Apache-2.0 (explicit patent grant; prior released snapshots
  remain MIT): `LICENSE` replaced with the canonical Apache License 2.0 text;
  `NOTICE`, `pyproject.toml`, `CITATION.cff`, and README updated to match
- Public surfaces now distinguish the evaluation evidence hub from the AWS
  assistant, describe dated snapshots and bounded transient processing
  precisely, render prose rather than experimental structured cards, and give
  programmatically focused answer regions a visible focus treatment.
- Iterative deploys now fail closed when existing Lambda configuration cannot be
  read, preserve unrelated operator variables and the history-signing key,
  validate disabled source IDs, capture the current code/configuration as a
  private rollback artifact, apply and verify containment before code, and count
  guarded Bedrock calls in the spend alarm.
- Nightly evaluation evidence uploads even when the evaluator fails, preserving
  the partial report and traces needed to diagnose a red release gate.
- Release authorization now runs from reviewed `main` through the immutable
  portfolio authorizer, verifies and builds the exact selected commit, and
  hands only distributions, SBOM, and notes to a checkout-free publisher that
  rechecks the tag object.
- Hash-pinned rider deploy bundle (roadmap M-7 / audit P1-6, 2026-07-17):
  `infra/deploy.sh` now installs only from `infra/requirements-deploy.txt`
  (a `uv export` of the locked runtime set) with `--require-hashes`, so the
  deployed artifact carries exactly the dependency versions the test suite
  ran against. The loose ranges it used before really did drift: the locked
  numpy publishes no manylinux2014 wheels, so the old install silently
  deployed an older numpy; the bundle now targets `aarch64-manylinux_2_28`,
  which the python3.12 Lambda runtime (Amazon Linux 2023, glibc 2.34)
  supports. Regenerate with `make deploy-reqs`;
  `tests/test_deploy_requirements.py` holds the pin file, the script, and
  `uv.lock` in lockstep. The operator console bundle
  (`infra/deploy-console.sh`) is not covered and still installs from loose
  ranges.
- Hosted completions now expose the SDK's actual served model while retaining
  the requested model for pricing, and eval cache keys use collision-proof
  canonical JSON framing even when prompts contain U+0000.
- Replaced the absolute BM25 `min_confidence` decline threshold with
  normalized, corpus-size-independent retrieval signals
  (`assistant.retrieve.ConfidenceSignals`: a z-score against the full-corpus
  score distribution and query-term coverage), calibrated by the new
  `evals/decline_calibration.py` against a labeled should-answer/
  should-decline question set. See `docs/decisions/0013` (FIX-07).
- Roadmap P1 item 4, "a true rate limit": `infra/deploy.sh` now derives the
  API Gateway stage throttle's rate and burst from the same
  `RESERVED_CONCURRENCY` value used for the Lambda concurrency ceiling, so
  the gateway's cross-container rate limit is documented, tuned, and cannot
  silently drift out of sync with concurrency. `web/handler.py`'s comments
  and docstrings now correctly describe the gateway throttle, not the
  per-container in-memory budget, as the guard that holds across containers.
  New test: `tests/test_deploy_rate_limit.py`. See the 2026-07-08 amendment
  in `docs/decisions/0004-demo-deploy.md`.

### Fixed
- Make the rider Lambda ZIP byte-reproducible across rebuilds by sorting archive
  paths and normalizing timestamps, file modes, and ZIP metadata. An unchanged
  reviewed revision now reuses its exact numbered Lambda version instead of
  publishing a duplicate because dependency-install mtimes changed. Unused
  dependency console entry points are omitted because their generated shebangs
  embed the builder's absolute virtual-environment path.
- Hosted-model usage and eval cost accounting now follow the reviewed
  portfolio GenAI telemetry contract. Anthropic and Bedrock cache-write/read
  buckets are normalized into canonical input totals once, priced at their
  distinct rates, propagated through answer/judge traces, cache records, run
  summaries, and reports, and exposed as PII-free structured fields. Invalid
  provider counts fail closed; unknown models remain visibly unpriced; an eval
  cache hit now correctly spends zero provider tokens for the current run.
- Restored the `checks`, i18n, and advisory browser-accessibility jobs on pull
  requests after a CI-minutes optimization accidentally made them push-only.
  The committed-report regression check runs on every pull request again.
- Reformatted the codebase with `ruff format` and made it a blocking `make
  verify` / CI gate (was check-only).
- `docs/ROADMAP.md` P2 item 5 (a11y wiring) was stale: it still listed feeding
  transcripts to GovChat-Eval's a11y suite (`transcript_html`) as remaining
  work, but that landed in the same session two commits later
  (`evals.govchat_export.render_transcript`, documented in
  `docs/audits/methodology.md` as "a11y now runs"). Corrected the roadmap to
  reflect that only the manual screen-reader/keyboard walkthrough
  (`docs/audits/a11y-walkthrough.md`, still an unfilled result table) remains
  — that step needs a human at a real assistive-tech session and is not
  something this pass fabricates.

### Added
- NIST AI RMF crosswalk in `docs/procurement-brief.md` (roadmap F-12 /
  research item RR10, 2026-07-17): maps the existing artifacts (guards, eval
  suites, calibration, audits, model card, risk register, freshness loop)
  onto Govern/Map/Measure/Manage with file pointers. Explicitly a
  self-assessment, not a certification; the pending manual accessibility
  walkthrough stays flagged as not covered.
- Tag-triggered release workflow (`.github/workflows/release.yml`, STANDARDS
  conformance REL-14): on a `v*` tag it checks the tag matches
  `pyproject.toml`'s version, re-runs `make verify` at the tagged commit,
  builds sdist+wheel, generates a CycloneDX SBOM, attests SLSA build
  provenance, and creates a GitHub Release with the matching CHANGELOG
  section as notes. No tag has been pushed yet, so the note above ("does not
  yet cut tagged releases") still holds until the first one is.
- **Provenance gate promoted to blocking** (FIX-01/M-2, 2026-07-09): `make
  verify` and CI's `checks` job now run `evals/provenance.py`; the three
  published artifacts declare their **true** generation-time prompt/corpus
  versions (baseline: v5/v2 from the 2026-06-17 run; golden dataset: v4/v2
  from commit `3901855`) and their staleness vs HEAD is acknowledged loudly in
  `evals/stale_acknowledged.json` with written reasons — declared, not
  stamped current. `evals/govchat_export.py` now emits a dataset-level
  `# provenance:` header on every regeneration.
- Root-caused and fixed the multilingual eval regression flagged in the
  2026-06-30 report (18/21 vs the committed 20/21 baseline). The 2026-07-11
  full live run recovered to 20/21 without changing the baseline or gate.
  See `docs/audits/eval-regression-2026-06-30.md`.
- System prompt **v7, live-validated 2026-07-11**: never
  state a fee/payment consequence beyond what the passage supports — targets
  the `ml-015` cross-lingual assertiveness gap. A full live run passed
  `ml-015` and restored multilingual to 20/21; no baseline or threshold was
  changed.
- Documented the **MST Spanish-content parity ceiling** behind `ml-012`
  (2026-07-09): `mst.org/es/fares/benefits/` exists but is an untranslated
  English shell, verified once via the polite fetch pipeline and deliberately
  not ingested. See `docs/I18N.md` and the addendum in
  `docs/audits/eval-regression-2026-06-30.md`.
- Re-recorded the 122-item independent GovChat-Eval dataset under the validated
  v7 prompt and regenerated its report. The advisory deterministic audit still
  reports its known groundedness and multilingual gaps; they remain visible.
- `src/assistant/gtfs.py`: GTFS(-Fares) cross-validation channel (EXP-06).
  `make gtfs-fetch` snapshots MST's and SBMTD's live GTFS static feeds
  (surveyed and confirmed 2026-07-08 — the other three pilot agencies did
  not resolve to a discoverable feed this pass) and `make gtfs-check`
  cross-checks feed fares against the prose corpus, flagging disagreement as
  `feed_agrees: yes|no|no_feed` in `corpus/processed/gtfs_cross_check.json`.
   Never overrides an answer; see `docs/decisions/0011-gtfs-cross-validation.md`
   for the design, the live survey, and the real coverage gap the first run
   found (SBMTD's Downtown-Waterfront Shuttle fare has no citable prose page).
- Structured fare-fact layer (EXP-01, `docs/ideation/03-expansions.md`):
  `src/assistant/facts.py` extracts a typed `FareFact` row (agency, program,
  rider_class, price, age_min/max, source chunk) per price/age figure found
  at ingest, committed as `corpus/processed/facts.jsonl`; a new
  `fare_facts_consistent` deterministic check in `evals/checks.py` verifies
  every `$`-amount and age claim in an answer against a fact row scoped to
  the cited document, instead of relying only on the LLM judge for
  groundedness of numbers.
- Standards conformance declaration table in `README.md`.
- Blocking dependency-vulnerability scan (`pip-audit`) in `security.yml`.
- `CODEOWNERS`, `.python-version`, `.standards-version`, this `CHANGELOG.md`.
- Judge-label staleness binding (`answer_sha256`) in `evals/calibration.py` so
  a prompt bump can't silently score the judge against a stale human label.
- `evals/provenance.py`: a provenance-drift check comparing the prompt/corpus
  versions declared in `EVALS.md`, `evals/baseline.json`, and
  `evals/govchat/golden.jsonl` against `HEAD` (offline tool; not yet wired as
  a blocking CI gate — see the execution log in
  `../audit-2026-07-05/fare-assistant-REMEDIATION.md`).
- `evals/check_report_regression.py`: a merge-blocking check that the
  committed `EVALS.md` scoreboard has not regressed against the committed
  `evals/baseline.json` (closes the gap where a locally-regenerated, gate-failing
  report could be committed without CI ever seeing the failure).
- Agency operator console (`web/console.py`, EXP-09): a small, separately
  authenticated surface (fails closed without `FPA_CONSOLE_TOKEN`) where an
  agency owner can pin a corpus version, review the git-backed changelog/diff
  (`assistant.corpus.version_history`, `make history`), configure the embed
  widget's allowed origins, and read the latest eval report — actions that
  previously meant editing the rider Lambda's environment variables by hand.
  Deployed separately from the rider demo via `infra/deploy-console.sh`, with
  an IAM role scoped to only that one rider function's configuration.

## [0.1.0] - 2026-06-30

Initial reference-implementation milestone referenced by `CITATION.cff`:
five-agency EN/ES corpus, 118-case eval harness (groundedness, refusal,
edge_cases, multilingual, freshness, conversation), gettext-based i18n
catalogs, SHA-pinned CI with blocking SAST/secret-scan, branch-coverage gate,
and the independent GovChat-Eval audit. See `EVALS.md` and `docs/audits/` for
the measured state at this point, and `git log` for the full history (no
tag was created for this milestone; see the Standards conformance table's
RELEASE-AND-VERSIONING row).
