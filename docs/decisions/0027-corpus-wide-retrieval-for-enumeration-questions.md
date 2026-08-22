# 0027 — One passage per agency for corpus-wide enumeration questions

Date: 2026-08-22. Status: accepted, with the measured result stated plainly:
it did not move the scoreboard.

## Context

`xagency-010` asks "Which agencies in your corpus take Clipper?". The question
names no agency, so `detect_agencies` returns nothing and `search()` falls
through to the plain global top_k: 8 chunks out of a 301-chunk, 18-agency
corpus, taken by whichever agency happens to mention Clipper densest.

Issue #150 filed this as retrieval work rather than prompt work, and left the
design open: "a question whose honest answer requires corpus-wide enumeration
may need either a different retrieval strategy for enumerative questions, or a
decision that the assistant should decline to answer them."

Measured on the 2026-08-22 nightly (main, no enumeration branch), the plain
top_k retrieved chunks from seven agencies and the answer enumerated four:
Marin Transit, WestCAT, SolTrans, SamTrans. It said "the passages do not state
whether the other agencies in the corpus accept Clipper." That sentence was
true of the passages and false of the corpus. The corpus carries six documents
whose titles name Clipper outright — `soltrans-clipper-card`,
`cccta-clipper-card`, `westcat-clipper`, `samtrans-clipper`, `marin-clipper`,
and AC Transit's `actransit-discounts` ("Clipper START") — plus VTA and VINE
passages, and SCMTD's flat statement that "Clipper Cards are not honored on
METRO buses". Depth-first retrieval could not see most of that, so the answer
under-enumerated and the one documented *non*-participant never surfaced at
all.

## Decision

On an enumeration-form question with no agency named, take the single
best-ranked positive-scoring chunk **per agency**, in global score order,
instead of the global top_k. Breadth is bought with depth deliberately: an
enumeration answer needs one passage per agency, not eight passages about one
agency.

Two details carry most of the weight:

**Rank the per-agency pick on the question minus its enumeration
scaffolding.** The scaffolding ("which", "agencies", "in your corpus") matches
fare tables and boilerplate more densely than the actual topic does. Measured
with the scaffolding left in, CCCTA's representative chunk for the Clipper
question was its RTC-discount page rather than its dedicated Clipper page, and
SCMTD's was an accessibility page rather than the "not honored" chunk.
Generic question verbs were worse than the nouns, because BM25's IDF makes a
rare verb decisive: WestCAT's pick became a pass-purchasing page scoring 7.1
on "take" alone, ahead of every dedicated WestCAT Clipper chunk (max 4.9). The
verb is redundant with its object for retrieval — "Clipper" alone finds
acceptance passages.

**Report the original-question score, not the topic score.** The pick is
ranked on the topic, but `confidence_signals` compares a result's score
against the full-corpus background distribution for the original question.
Handing it a topic-scale score would compare two different scales.

## What it measured

Retrieval did exactly what it was designed to do. On the 2026-08-22 full live
run (385 cases, cold, answer `claude-haiku-4-5`, judge `claude-sonnet-4-6` via
Bedrock), `xagency-010` went from four enumerated agencies to eight, each
carrying a resolvable citation to that agency's own Clipper document, plus the
documented negative for SCMTD quoted exactly, plus a correctly scoped
statement about the agencies whose passages were not retrieved. Every one of
the nine deterministic checks passed, `citation_present_and_resolvable`
included.

**The case still failed, and the suite did not improve.** cross_agency was
9/21 (42.9%). `xagency-010` failed before this change and failed after it.
Containment was measured rather than assumed: across all 349 questions in the
nine suites, exactly two retrieve differently — `xagency-010` and
`refuse-014` — and every other question retrieves byte-identical chunks at
identical scores. `refuse-014` passed before and after. So this change's
measured effect on the scoreboard is **zero cases**, in either direction.

It failed for two reasons, neither of them retrieval:

1. **The case's ground truth is stale.** `xagency-010`'s rationale states
   "SolTrans is the only Clipper participant documented in this corpus" and
   names MST, SBMTD, Yolobus, SacRT and HTA as the others — the six-agency
   corpus, written before the expansion to eighteen. `judge_helpfulness` v3
   is threaded that rationale by design, and on this run it used it to call
   the answer's real, resolvable citations "fabricated" and scored it 2. No
   retrieval strategy can pass a case whose ground truth contradicts the
   corpus it is scored against.

2. **A prose/structured `as_of` mismatch.** The answer's prose says
   "published as of 2026-08-14" while the structured contract's `as_of` is
   2026-08-13, which is also the oldest cited fetch date.
   `as_of_matches_oldest_citation` reads the structured field, so it passes;
   `judge_groundedness` read the prose and failed it as unsupported. A
   deterministic check that validates a field while the rider-visible sentence
   says something else is this project's dominant defect class appearing in a
   new place.

Both are tracked separately. #150 stays open, naming these two as the gap,
because the enumeration question is still not answered correctly end to end.

## Why this landed anyway

By the letter of CLAUDE.md — "no reranker unless evals show retrieval is the
bottleneck; if added, the eval deltas justify it in an ADR" — a change with a
zero-case delta has not earned its place, and that argument was taken
seriously. It landed on three grounds:

- The eval *did* show retrieval was the bottleneck, which is what #150 filed.
  The before/after answers are the evidence: the same prompt and the same
  model, given a corpus-wide cross-section instead of a depth-first slice,
  enumerated twice as many agencies and surfaced the one documented
  non-participant.
- The rider-facing improvement is verifiable without reference to the judge.
  Eight agencies with resolvable citations to their own Clipper documents is
  a better answer to "which agencies take Clipper" than four, and "SCMTD does
  not accept Clipper" is information a rider can act on that the previous
  answer could not produce at any k.
- It regressed nothing. Containment is proven across all 349 questions, and
  `tests/test_retrieve.py::test_enumeration_branch_changes_nothing_it_does_not_claim_to`
  asserts both halves — including that the check is able to fail, so it cannot
  quietly go inert the way the smoke suite's Yolobus containment assertion
  did.

The honest summary is that this is a quality improvement the current harness
cannot score, in a case whose ground truth needs a human's decision before the
harness can score it. That is written here rather than implied by a green
number.

## Alternatives considered

**Raise `top_k` for enumeration questions instead.** Rejected on the
arithmetic: covering eighteen agencies at the observed per-agency chunk
density needs a k large enough that the answer prompt fills with the densest
agency's passages long before the sparsest agency appears. The problem is the
selection shape, not the budget.

**Have the assistant decline enumeration questions outright**, the second
option #150 names. Rejected as a rider-facing regression: the corpus can
genuinely answer this question for nine agencies, and declining would convert
"we can tell you this" into silence to protect a score. A grounded-or-silent
design should be silent where it is ungrounded, and this question is not.

**Fix the stale `xagency-010` rationale in the same pass so the case can
pass.** Rejected deliberately. Editing a case's ground truth in the same
change that is measured against it is how a harness stops being evidence,
and the corpus claim it would have to assert is a factual judgment about
eighteen agencies' fare media that deserves the same human review #143 is
waiting on. Filed instead, with the manifest evidence attached.

## Consequences

- `search()` gains a third branch, reached only when the question is
  enumeration-form *and* no agency is detected. `_ENUMERATION_QUERY`,
  `_ENUMERATION_SCAFFOLD_TOKENS` and `_enumeration_topic` are new module-level
  definitions in `retrieve.py`.
- The branch returns up to one chunk per agency, so a result list can now
  exceed `top_k` (18 today). Nothing downstream assumed `len(results) <=
  top_k`; `confidence_signals` normalizes against the corpus background and is
  unaffected in scale, though a broader, flatter result set lowers
  `term_coverage`. Both affected questions stay above the decline floors
  (`z >= 1.50`, `term_coverage >= 0.10`): `xagency-010` at z=2.60/0.167 and
  `refuse-014` at z=3.60/0.125, so neither tips into a decline it should not
  make.
- `_ENUMERATION_SCAFFOLD_TOKENS` is a hand-maintained stop list. It is scoped
  to this one branch and cannot affect ordinary retrieval, but it will need
  extending if the suites grow enumeration questions in other phrasings; the
  parametrized trigger tests are where that shows up.
