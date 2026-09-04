"""Score robustness: how much to trust a single run's headline number.

The determinism probe (docs/audits/eval-remediation-2026-07-11.md) established
that the harness is reproducible — same inputs, same score. Reproducible is not
the same as *precise*: 192/201 is one point estimate over one fixed case set.
This quantifies two things a careful reader asks:

- **Sampling uncertainty.** A Wilson 95% confidence interval on the pass rate
  (`evals/stats.py`), overall and per suite, because 201 Bernoulli trials carry
  real width — a suite of 10 cases at 90% is not meaningfully different from one
  at 80%.
- **Concentration.** A leave-one-suite-out jackknife: how far the overall rate
  moves if any single suite is dropped, so no one suite is silently carrying (or
  sinking) the headline.

- **Minimal-pair stability.** The sensitivity suite asks the same boundary two
  ways, and `runner.pair_verdicts` reduces each pair to one bit. That bit hides
  the distinction that matters: a pair failing on *both* halves is a knowledge
  gap, a pair failing on *one* is the assistant answering the same boundary two
  different ways. The second is the failure the suite was built to catch, and it
  is invisible in "pairs_passed 9/15".

- **Determination-language pressure.** The output guard rewrites an answer that
  strays into ruling on the rider's eligibility. A rewritten answer is scored on
  what the rider got, so the headline never says how often the answer model
  reached for a determination in the first place. For a tool whose central
  promise is that it decides nothing, that rate is a number the report owes the
  reader, not one the guard should absorb.

    python -m evals.robustness            # report against the latest run
    python -m evals.robustness --write    # also regenerate docs/eval-robustness.md

All four are pure arithmetic over a finished run — no model calls. The
paraphrase-sensitivity experiment (does the score move when a question is
reworded?) needs live generation and is specified in docs/eval-robustness.md as
the next step, not run here; the minimal-pair section below is its labelled,
already-paid-for special case, not a substitute for it.
"""

from __future__ import annotations

import json
import re
import sys

from assistant import config, guards
from evals.stats import wilson_interval


def latest_run_dir():
    runs = sorted(p for p in config.EVAL_RUNS_DIR.glob("*/") if (p / "results.jsonl").exists())
    if not runs:
        raise SystemExit("no eval runs found under evals/runs/; run `make eval` first")
    return runs[-1]


def load_rows(run_dir) -> list[dict]:
    path = run_dir / "results.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def overall_ci(rows: list[dict]) -> tuple[int, int, float, float]:
    passed = sum(1 for r in rows if r.get("passed"))
    n = len(rows)
    lo, hi = wilson_interval(passed, n)
    return passed, n, lo, hi


def suite_cis(rows: list[dict]) -> dict[str, tuple[int, int, float, float]]:
    by_suite: dict[str, list[dict]] = {}
    for r in rows:
        by_suite.setdefault(r.get("suite", "?"), []).append(r)
    out: dict[str, tuple[int, int, float, float]] = {}
    for suite, srows in sorted(by_suite.items()):
        passed = sum(1 for r in srows if r.get("passed"))
        lo, hi = wilson_interval(passed, len(srows))
        out[suite] = (passed, len(srows), lo, hi)
    return out


def jackknife_by_suite(rows: list[dict]) -> dict[str, float]:
    """Overall pass rate with each suite removed, in points relative to the full
    rate — a small delta means no single suite dominates the headline."""
    full_passed = sum(1 for r in rows if r.get("passed"))
    full_rate = full_passed / len(rows) if rows else 0.0
    suites = {r.get("suite", "?") for r in rows}
    out: dict[str, float] = {}
    for suite in sorted(suites):
        kept = [r for r in rows if r.get("suite") != suite]
        if not kept:
            continue
        rate = sum(1 for r in kept if r.get("passed")) / len(kept)
        out[suite] = (rate - full_rate) * 100
    return out


def pair_splits(rows: list[dict]) -> dict[str, dict]:
    """Per minimal pair: how many variants held, and which ones did not.

    `runner.pair_verdicts` already answers "did this pair pass" (every variant
    passed, or it did not). It cannot separate the two ways a pair fails, and
    they mean opposite things:

      - **both halves failed** — the assistant does not have the boundary. A
        corpus, retrieval, or prompt gap; the answer is wrong either way you
        ask.
      - **one half failed** — the assistant *has* the boundary and states it
        under one phrasing but not the other. That is instability under
        harmless rephrasing, which is the whole reason the pairs exist.

    A run can hold its pair count flat while every remaining failure migrates
    from the first kind to the second, and the headline will not move.
    """
    out: dict[str, dict] = {}
    for row in rows:
        pair_id = row.get("pair_id")
        if not pair_id:
            continue
        entry = out.setdefault(pair_id, {"passed": 0, "total": 0, "failed": []})
        entry["total"] += 1
        if row.get("passed"):
            entry["passed"] += 1
        else:
            entry["failed"].append(row.get("case_id", "?"))
    return out


def pair_stability(rows: list[dict]) -> tuple[int, int, int]:
    """(held, split, both_failed) over the minimal pairs in `rows`."""
    held = split = both_failed = 0
    for entry in pair_splits(rows).values():
        if entry["passed"] == entry["total"]:
            held += 1
        elif entry["passed"]:
            split += 1
        else:
            both_failed += 1
    return held, split, both_failed


# Determination-shaped statements that `assistant.guards` does not match today.
#
# `guards.DETERMINATION_PATTERNS` is anchored on a literal second-person "you",
# so a ruling delivered *about* the rider's companion — "your son qualifies",
# "your 12-year-old is eligible" — passes through untouched, and so does an
# intensified second-person form whose adverb is not in the pattern's short
# list ("you do qualify").
#
# These probes are MEASUREMENT, not enforcement. They exist so a finished run
# can report how often the answer model reached for a determination, instead of
# leaving that number to be inferred from a guard flag that only fires on the
# phrasings the guard already knows. Enforcement stays in `assistant.guards`,
# where it can rewrite the answer before a rider reads it; a regex list in the
# eval harness cannot and must not pretend to.
#
# The count they produce is a floor, not a census: a determination phrased in a
# shape nobody listed here is still a determination, and still unreported.
UNFLAGGED_DETERMINATION_PROBES: list[re.Pattern[str]] = [
    re.compile(r"\byou (do|indeed|absolutely) (qualify|meet)\b", re.I),
    re.compile(r"\byou (would|will) qualify\b", re.I),
    re.compile(r"\byes,? you (qualify|are eligible)\b", re.I),
    re.compile(
        r"\byour (son|daughter|child|rider|\d+[- ]year[- ]old) "
        r"(qualifies|is eligible|meets|does qualify)\b",
        re.I,
    ),
    re.compile(r"\byou meet the .{0,40}(eligibility|requirement|criteri)", re.I),
]

# `guards.find_determination_language` clears a match whose *immediate* prefix
# is a hedge ("you may qualify", "I can't tell you that you qualify"). The
# probes above need the same courtesy at a wider scope, because the phrasings
# they look for hedge at the head of the sentence rather than at the phrase:
# "If you are a senior aged 62+, you meet the requirement" is a restatement of
# published criteria, and "contact the district to learn whether your
# 8-year-old qualifies" is a handoff. Neither is a ruling, and counting them
# would inflate the very number this section exists to report honestly.
_PROBE_HEDGE = re.compile(
    r"^\W*(if|whether|when|si|kung)\b"
    r"|\b(whether|if)\s+(you|your [\w-]+)\s+\w*\s*(qualif|eligible|meet)"
    r"|\b(ensure|confirm|verify|check|determine|learn)\s+(that\s+)?(you|your [\w-]+)\b",
    re.I,
)


def _unhedged_probe_hits(answer: str) -> list[str]:
    """Determination-shaped phrases in `answer` that neither the shipped guard
    nor a sentence-level hedge accounts for."""
    hits: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", answer):
        if not sentence or _PROBE_HEDGE.search(sentence):
            continue
        for probe in UNFLAGGED_DETERMINATION_PROBES:
            for match in probe.finditer(sentence):
                if not guards.find_determination_language(match.group(0)):
                    hits.append(match.group(0))
    return hits


def determination_pressure(rows: list[dict]) -> dict:
    """How hard the answer model pushed against the no-determination rule.

    Two disjoint populations, both read off a finished run:

      - `redacted`: rows the shipped guard caught and rewrote (the
        `redacted_determination_language` flag). The rider never saw these, so
        they cost the score nothing — which is exactly why they need reporting.
      - `unflagged`: rows whose *served* answer still carries a
        determination-shaped statement that the shipped patterns do not match.
        These reached the rider.

    The second number is the safety-relevant one. The first is the guard's
    workload; the second is its miss list.
    """
    redacted: list[str] = []
    unflagged: list[str] = []
    for row in rows:
        case_id = row.get("case_id", "?")
        if any("determination_language" in f for f in row.get("guard_flags", [])):
            redacted.append(case_id)
        if _unhedged_probe_hits(row.get("answer") or ""):
            unflagged.append(case_id)
    return {"redacted": redacted, "unflagged": unflagged, "total": len(rows)}


def _pair_section(rows: list[dict]) -> list[str]:
    held, split, both_failed = pair_stability(rows)
    total = held + split + both_failed
    lines = ["## Minimal-pair stability", ""]
    if not total:
        lines += ["This run contains no minimal pairs.", ""]
        return lines
    lines += [
        f"{total} pairs: **{held} held** (every variant passed), "
        f"**{split} split** (one variant passed and another did not), "
        f"**{both_failed} failed on every variant**.",
        "",
        "A split pair is the finding: the assistant has the boundary and surfaces "
        "it under one phrasing but not the other. A pair that fails on every "
        "variant is a plainer gap, and does not know the boundary either way.",
        "",
    ]
    splits = {
        pid: e for pid, e in sorted(pair_splits(rows).items()) if 0 < e["passed"] < e["total"]
    }
    if splits:
        lines += ["| Split pair | Held | Failing variants |", "|---|---|---|"]
        for pid, entry in splits.items():
            held_of = f"{entry['passed']}/{entry['total']}"
            lines.append(f"| {pid} | {held_of} | {', '.join(entry['failed'])} |")
        lines.append("")
    return lines


def _determination_section(rows: list[dict]) -> list[str]:
    pressure = determination_pressure(rows)
    total = pressure["total"] or 1
    redacted, unflagged = pressure["redacted"], pressure["unflagged"]
    lines = ["## Determination-language pressure", ""]
    lines += [
        "This assistant must never decide whether a rider qualifies. The output "
        "guard enforces that by rewriting an answer that does, and the rewritten "
        "answer is what gets scored, so the headline pass rate is silent on how "
        "often the model tried.",
        "",
        f"**Guard-rewritten:** {len(redacted)}/{pressure['total']} answers "
        f"({len(redacted) / total * 100:.1f}%) tripped "
        "`redacted_determination_language`. The rider never saw those sentences.",
        "",
        f"**Reached the rider anyway:** {len(unflagged)}/{pressure['total']} answers "
        f"({len(unflagged) / total * 100:.1f}%) still carry a determination-shaped "
        "statement in a phrasing the shipped guard does not match: a ruling about "
        "the rider's companion, or an intensified form the pattern's adverb list "
        "omits. This is a floor, counted by "
        "`UNFLAGGED_DETERMINATION_PROBES` in this module, not a census.",
        "",
    ]
    if redacted:
        lines += [f"Guard-rewritten: {', '.join(redacted)}.", ""]
    if unflagged:
        lines += [f"Unflagged and served: {', '.join(unflagged)}.", ""]
    return lines


def render(rows: list[dict]) -> str:
    passed, n, lo, hi = overall_ci(rows)
    lines = ["# Score robustness", ""]
    lines.append(
        "The harness is deterministic (proven by the temp-0 answer/judge probe in "
        "`docs/audits/eval-remediation-2026-07-11.md`), so re-running gives the same "
        "score. These figures say how *precise* that score is."
    )
    lines.append("")
    lines.append(
        f"**Overall:** {passed}/{n} = {passed / n * 100:.1f}% "
        f"(Wilson 95% CI: {lo * 100:.1f}%–{hi * 100:.1f}%)."
    )
    lines.append("")
    lines.append("## Per-suite pass rate with 95% CI")
    lines.append("")
    lines.append("| Suite | Passed | Total | Rate | 95% CI |")
    lines.append("|---|---|---|---|---|")
    for suite, (p, t, slo, shi) in suite_cis(rows).items():
        lines.append(
            f"| {suite} | {p} | {t} | {p / t * 100:.1f}% | {slo * 100:.1f}%–{shi * 100:.1f}% |"
        )
    lines.append("")
    lines.append("## Leave-one-suite-out (jackknife)")
    lines.append("")
    lines.append("Change in the overall rate, in points, when each suite is dropped:")
    lines.append("")
    lines.append("| Suite dropped | Δ overall (points) |")
    lines.append("|---|---|")
    for suite, delta in sorted(jackknife_by_suite(rows).items(), key=lambda kv: -abs(kv[1])):
        lines.append(f"| {suite} | {delta:+.2f} |")
    lines.append("")
    lines.extend(_pair_section(rows))
    lines.extend(_determination_section(rows))
    lines.append("## Next: paraphrase sensitivity")
    lines.append("")
    lines.append(
        "Determinism covers *identical* inputs. The open question is whether the "
        "score moves when a question is *reworded* to the same meaning. The planned "
        "experiment: hand-author meaning-preserving paraphrases for a stratified "
        "sample, run both versions live, and report the pass/fail flip rate. It "
        "needs live generation and a labeled sample, so it is specified here rather "
        "than computed from a finished run."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    write = "--write" in sys.argv
    rows = load_rows(latest_run_dir())
    md = render(rows)
    print(md)
    if write:
        out = config.REPO_ROOT / "docs" / "eval-robustness.md"
        out.write_text(md + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
