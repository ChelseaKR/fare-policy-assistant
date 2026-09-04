# Judge calibration: what it measures, where it stands, how to finish it

Two of this project's nine suites are scored by an LLM judge rather than by a
deterministic check. Calibration is the answer to the obvious question about
that: how do we know the judge is right? A person labels a sample of the same
(case, judge) pairs the judge scored, without seeing its verdicts, and the
report prints how often the two agreed and what Cohen's κ was.

`STANDARDS/AI-EVALUATION-STANDARD.md` §3 makes three of those numbers
merge-blocking:

| Gate | Target |
|---|---|
| AIEV-18 judge-to-human raw agreement | at least 0.80, over a set of 50–100 labeled traces |
| AIEV-19 Cohen's κ | at least 0.60 |
| AIEV-20 calibration freshness | the set is relabeled within 30 days; a stale set fails |

## Where it stands, as of 2026-09-04

Verified against the promoted 2026-07-12 run, the one `EVALS.md` reports.

- `evals/calibration/judge_labels.jsonl` holds **16 human labels**. Twelve are
  stale: the answers they were written against changed under a prompt bump, and
  a label bound by `answer_sha256` to an answer that moved is skipped rather
  than scored. **Four** are live.
- All four are **helpfulness** rows — refuse-003, refuse-007, refuse-011,
  refuse-013. Every groundedness label in the set went stale, so the judge that
  actually fails cases — and the one whose prompt has moved since (v2 to v3 on
  2026-08-16) — has **no live human label at all**. The single agreement figure
  on the report covers one of the two judges.
- Raw agreement over those four is **100%**, against a floor of 37 labels — the
  10% of the run's 367 judged pairs `CLAUDE.md` asks for — and against the
  standard's own 50-label set size. Four labels is not a measurement of
  agreement; it is four labels.
- Cohen's κ is **undefined**. All four surviving labels agreed with the judge,
  so expected agreement is 1.0 and the formula is 0/0. The two labels that had
  recorded a human/judge *disagreement* — ml-004 and ground-024 — are among the
  twelve that went stale. What is left is the agreeing half of the set.
- The verdicts were authored **2026-06-16** (commit 62b8a2f), which is 80 days
  ago. The `answer_sha256` bindings were added later, on 2026-07-05, without
  re-reading the answers. AIEV-20's 30-day window closed in July.

So AIEV-19 and AIEV-20 fail outright, and AIEV-18 is not failed so much as
unmeasured: an agreement rate over four labels cannot pass a gate whose stated
sample size is fifty.

## Why 0 of 37 rows had been filled

`evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl` has held 37
unlabeled rows since 2026-08-05, and the honest reading of that was always
"nobody has found the hour." That was wrong. On 2026-09-04, `make relabel` on
that worksheet exited 2:

    no results.jsonl in evals/runs/20260712T050117Z

The worksheet is bound to a run directory. `evals/runs/` is in `.gitignore`. The
2026-07-12 run had been pruned from the only machine that ever held it, and it
was never in the repository, so no clone of this project could have shown a
single row. The hour of work was not declined; it was not available.

That is fixed by giving a worksheet its own evidence.

## The evidence packet

Beside every worksheet there is now a committed packet —
`judge_relabel_worksheet_<date>.jsonl` pairs with
`judge_relabel_evidence_<date>.jsonl` — holding, per case, the question, any
prior turns, the expected behavior and rationale, the retrieved passages with
their source URL and fetch date, the answer, and the criterion text each judge
was given. `--review` reads the run directory when it is there and the packet
when it is not, so the fallback needs no flag and no knowledge.

The packet holds **no judge verdict and no judge reasoning**. That is enforced,
not merely intended: `load_evidence_packet` raises on a row carrying any judge
field, and a test asserts it. Moving evidence into the repository must not be
the way the judge's call ends up in front of a reviewer before they answer.

Two things the packet for the 2026-07-12 run cannot restore, and renders as
absent rather than as values:

- **Retrieval scores** were never committed for that run. Passages read "score
  not recorded", not `score None`.
- **The judge's reasoning** lived in the pruned run. The worksheet still
  records each row's verdict, so `--review` can reveal it after you answer, and
  says the reasoning is gone.

One asymmetry is deliberate and stated on the screen: the passages carry their
source URL and fetch date, and that run's groundedness judge did not see them
(`evals/judges.py` gained the provenance line on 2026-08-16). Withholding them
from the reviewer as well would reproduce the exact blind spot that made the
judge wrong about `fresh-001` — asked to check a dated claim against evidence
the dates had been cut out of. Four of the 37 rows are freshness cases. If the
provenance changes your verdict, say so in your reason.

## How to do the labeling

    make relabel                 # all 37, resumable
    uv run python -m evals.calibration --review \
        evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl --limit 9

The `--limit 9` form is the one to start with. The worksheet is ordered
failures-first, so the first nine rows are every pair the judge failed — the
region where a human is most likely to differ, and where differing matters
most. A sample drawn from where the judge never objects cannot disagree with
it, which is precisely how the last one ended up reporting 100% and an
undefined κ.

What the tool will and will not do:

- It **never proposes a verdict.** No default, no suggestion, nothing inferred
  from the judge. Pressing Enter re-asks. Every outcome, including leaving a row
  blank, has to be typed.
- It **withholds the judge's call** until after you have given yours, then shows
  it. Confirming a row the judge got right costs the same as one it got wrong.
  That is the intended trade.
- It **refuses a row whose answer moved.** Every row is bound to an
  `answer_sha256`; if the evidence no longer hashes to it, the row is reported
  and skipped rather than labeled.
- It **writes each verdict to disk as you give it**, atomically. Type `quit`
  after any row. Reopening asks only what is still blank.

Budget a few minutes for a groundedness row — you are checking each claim in
the answer against up to nine passages — and under a minute for a helpfulness
one, which is graded against the case's expected behavior rather than the
corpus. Twenty-one of the 37 rows are groundedness.

When rows are filled, move them into `evals/calibration/judge_labels.jsonl`,
update its `# labeled_on:` directive to the date you labeled, and regenerate the
report. κ and its n replace "undefined".

## The recurrence problem

Labels are bound to the sha256 of the answer they graded. That binding is
correct and should stay: it is what stops a prompt bump from silently scoring
the judge against a human verdict on a different answer, and it is why the
report can say twelve labels are stale instead of quietly reporting a κ built
from them.

### The half the binding was missing

A verdict is a judgment about an answer *under a criterion*, and only the answer
was bound. PR #179 is that gap with a date on it. It moves
`prompts/judge_groundedness.txt` from v3 to v4 and changes which "as of" claims
count as supported — tightening the rule so that a headline date newer than the
oldest cited passage is now explicitly unsupported.

Not one of the sixteen committed labels goes stale under that change, because
none of the answers move. All sixteen would have gone on being scored, against
judge verdicts produced by a rubric the person who wrote them never read. The
mechanism that exists to stop exactly this would not have fired, and the number
on the report would have been wrong rather than absent.

So a label now carries `judge_prompt_sha256` too, and `calibrate` reports a
`criterion_stale` label the same way it reports a stale one: skipped, listed,
and relabelable. Labels written before this binding existed are reported as
`criterion_unbound` and still scored — the same treatment `answer_sha256` gave
its own legacy — so the blind spot is on the page rather than implied. All
sixteen committed labels are `criterion_unbound` today. `--review` stamps the
criterion it actually put on screen, so verdicts recorded from here are bound
to both halves.

### The half still to design

Today a stale label is discarded, and because the answer it graded lived only in
a gitignored run directory, it is discarded *irrecoverably* — there is no way to
see what changed or to ask whether the change mattered. Twelve labels became
twelve absences with no diagnosis. Every prompt bump does this again, which is
why 2026-06-16's work is gone and 2026-08-05's had not started.

Committing the evidence is the first half of the fix, and it is what this page
describes. The second half is to let a stale label **degrade rather than
vanish**:

1. A label should carry, or point into a packet at, the answer text it graded —
   not only its hash. Then staleness can be shown as a diff.
2. When an answer or a criterion changes, the reviewer should be offered the
   diff alongside their own previous verdict and reason, and asked whether it
   still holds. Most prompt bumps reword; a groundedness verdict on a reworded
   answer usually survives, and #179's criterion change touches only dated
   claims, so it should cost a handful of labels rather than all of them. That
   turns "37 rows destroyed" into "37 re-confirmations, most of them quick".
3. A re-confirmation must still be typed, never carried over, and must record
   the hash it supersedes. It is a new human verdict on a new answer, and the
   lineage has to be auditable as one.
4. The report should show label age and how many labels the last prompt bump
   cost, so the decay is a trend rather than a cliff discovered at gate time.

Steps 1 and 4 are in place. Steps 2 and 3 are a design an owner should sign off
on before it is built, because the difference between "re-confirmed" and
"carried over" is the whole integrity of the artifact.
