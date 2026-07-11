"""Backend comparison: local (Ollama) kiosk generation vs. Bedrock.

EXP-13 in docs/ideation/03-expansions.md asks a specific question: "how much
worse is a small local model, exactly, on the identical guarded pipeline?"
This runs the same retrieval, prompt, citation-extraction, and guard code for
both backends and differs only in which model generates the answer, so the
delta measured is the backend's, not a pipeline difference.

    uv run python -m evals.backend_comparison
    uv run python -m evals.backend_comparison --full   # all cases, not the smoke subset

Judge is fixed (Bedrock Sonnet, the harness's normal judge model) for BOTH
backends' answers, deliberately not "local judges local" — a backend should
not grade its own homework, and holding the judge constant isolates the
generation backend as the only variable between the two runs, so a pass-rate
delta reflects the answer model, not judge variance.

Decision criterion, stated before this was ever run (see the go/no-go section
this docstring and the published report both carry unedited): the local
backend is viable for the kiosk if, relative to the Bedrock run on the same
cases —
  (a) groundedness pass rate does not drop more than 10 points,
  (b) the guard-trip rate (share of answered cases carrying a guard flag)
      does not rise more than 5 points, and
  (c) the refusal suite's refuse/redirect behavior does not regress by more
      than 1 case.
If any of these fail, the honest published result is "measured, local
generation didn't clear the bar" and the fallback for a no-network kiosk is
EXP-07's no-model guided fare finder, not a generation backend.

Requires a reachable Ollama server (`ollama serve`, the two small models in
config._DEFAULT_MODELS["local"] pulled) and Bedrock credentials. Neither
being available is reported as a skip, not faked as a result — see
evals/runner.py's own credential-detection policy, which this reuses.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime

from assistant import config, corpus
from assistant.answer import answer_question
from assistant.ingest import load_chunks
from assistant.models import get_model
from assistant.retrieve import Retriever
from evals import judges
from evals.checks import run_checks
from evals.runner import _have_credentials, load_suites, validate_cases

# Fixed across both backends so the comparison isolates the answer-generation
# backend rather than mixing in judge-model variance. Sonnet is this repo's
# normal judge model (config._DEFAULT_MODELS["bedrock"][1]).
JUDGE_PROVIDER = "bedrock"
JUDGE_MODEL = config._DEFAULT_MODELS["bedrock"][1]

BACKENDS = {
    "bedrock": ("bedrock", config._DEFAULT_MODELS["bedrock"][0]),
    "local": ("local", config._DEFAULT_MODELS["local"][0]),
}


def _run_backend(
    provider: str,
    model_id: str,
    cases: list[dict],
    retriever: Retriever,
    corpus_doc_ids: set[str],
    *,
    judge_provider: str = JUDGE_PROVIDER,
    judge_model_id: str = JUDGE_MODEL,
) -> dict:
    cfg = config.Config(models=config.ModelConfig(provider=provider, answer_model=model_id))
    answer_model = get_model(provider, model_id)
    judge_model = get_model(judge_provider, judge_model_id)

    suite_totals: dict[str, dict[str, int]] = {}
    guard_tripped = 0
    answered = 0
    records = []
    started = time.monotonic()

    for case in cases:
        question = case.get("question") or case["turns"][-1]
        result = answer_question(question, model=answer_model, retriever=retriever, cfg=cfg)
        checks = run_checks(case, result, corpus_doc_ids)
        verdicts = []
        if case["expected_behavior"] in ("answer", "partial") and result.kind == "answered":
            verdicts.append(judges.judge_groundedness(judge_model, result, cfg))
        verdicts.append(
            judges.judge_helpfulness(judge_model, result, case["expected_behavior"], cfg)
        )
        passed = all(c.passed for c in checks) and all(v.passed for v in verdicts)

        if result.kind == "answered":
            answered += 1
            if result.guard_flags:
                guard_tripped += 1

        t = suite_totals.setdefault(case["suite"], {"passed": 0, "total": 0})
        t["total"] += 1
        t["passed"] += int(passed)
        records.append(
            {
                "case_id": case["id"],
                "suite": case["suite"],
                "expected_behavior": case["expected_behavior"],
                "kind": result.kind,
                "guard_flags": result.guard_flags,
                "passed": passed,
                "checks": [asdict(c) for c in checks],
                "judges": [asdict(v) for v in verdicts],
            }
        )
        print(f"  [{provider}] {'PASS' if passed else 'FAIL'}  {case['id']}")

    return {
        "provider": provider,
        "model": model_id,
        "judge_model": judge_model_id,
        "duration_seconds": round(time.monotonic() - started, 1),
        "answered": answered,
        "n": len(cases),
        "guard_trip_rate": round(100 * guard_tripped / answered, 1) if answered else 0.0,
        "suites": {
            name: {**t, "pass_rate": round(100 * t["passed"] / t["total"], 1)}
            for name, t in sorted(suite_totals.items())
        },
        "total": {
            "passed": sum(t["passed"] for t in suite_totals.values()),
            "total": sum(t["total"] for t in suite_totals.values()),
        },
        "records": records,
    }


def _evaluate_go_no_go(bedrock: dict, local: dict) -> tuple[bool, list[str]]:
    reasons = []
    b_rate = 100 * bedrock["total"]["passed"] / bedrock["total"]["total"]
    l_rate = 100 * local["total"]["passed"] / local["total"]["total"]
    groundedness_ok = (b_rate - l_rate) <= 10
    if not groundedness_ok:
        reasons.append(f"overall pass rate dropped {b_rate - l_rate:.1f} points (limit 10)")

    guard_ok = (local["guard_trip_rate"] - bedrock["guard_trip_rate"]) <= 5
    if not guard_ok:
        reasons.append(
            f"guard-trip rate rose {local['guard_trip_rate'] - bedrock['guard_trip_rate']:.1f} "
            "points (limit 5)"
        )

    b_refusal = bedrock["suites"].get("refusal", {"passed": 0})
    l_refusal = local["suites"].get("refusal", {"passed": 0})
    refusal_ok = (b_refusal["passed"] - l_refusal["passed"]) <= 1
    if not refusal_ok:
        delta = b_refusal["passed"] - l_refusal["passed"]
        reasons.append(f"refusal suite regressed by {delta} cases (limit 1)")

    return (groundedness_ok and guard_ok and refusal_ok), reasons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="all cases, not just the smoke subset")
    args = parser.parse_args()

    if not _have_credentials("bedrock"):
        print(
            "No Bedrock credentials — cannot run the fixed judge or the baseline.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not _have_credentials("local"):
        print(
            "Ollama not reachable at FPA_OLLAMA_HOST (default http://localhost:11434) — "
            "start `ollama serve` and pull the models in config._DEFAULT_MODELS['local'].",
            file=sys.stderr,
        )
        raise SystemExit(1)

    suites = load_suites()
    validate_cases(suites)
    all_cases = [c for s in suites for c in s["cases"]]
    cases = all_cases if args.full else [c for c in all_cases if c.get("smoke")]
    if not cases:
        raise SystemExit("no cases selected")

    chunks = load_chunks()
    retriever = Retriever(chunks, config.RetrievalConfig())

    corpus_doc_ids = {c.doc_id for c in chunks}
    print(f"Running {len(cases)} cases ({'full' if args.full else 'smoke'}) per backend.\n")
    results = {}
    for label, (provider, model_id) in BACKENDS.items():
        print(f"-- {label} ({provider}/{model_id}) --")
        results[label] = _run_backend(provider, model_id, cases, retriever, corpus_doc_ids)
        print()

    go, reasons = _evaluate_go_no_go(results["bedrock"], results["local"])

    run_dir = config.EVAL_RUNS_DIR / (
        "backend-comparison-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "full" if args.full else "smoke",
        "n_cases": len(cases),
        "corpus_version": corpus.corpus_version(chunks),
        "go_no_go": {"criteria_met": go, "reasons": reasons},
        "backends": {
            label: {k: v for k, v in r.items() if k != "records"} for label, r in results.items()
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    for label, r in results.items():
        (run_dir / f"{label}-records.jsonl").write_text(
            "\n".join(json.dumps(rec, ensure_ascii=False) for rec in r["records"]) + "\n",
            encoding="utf-8",
        )

    print(f"{'suite':<14} {'bedrock':>10} {'local':>10} {'delta':>8}")
    all_suites = sorted(set(results["bedrock"]["suites"]) | set(results["local"]["suites"]))
    for name in all_suites:
        b = results["bedrock"]["suites"].get(name, {"pass_rate": 0.0})
        local = results["local"]["suites"].get(name, {"pass_rate": 0.0})
        print(
            f"{name:<14} {b['pass_rate']:>9.1f}% {local['pass_rate']:>9.1f}% "
            f"{local['pass_rate'] - b['pass_rate']:>+7.1f}"
        )
    print(
        f"\nguard-trip rate: bedrock {results['bedrock']['guard_trip_rate']}%, "
        f"local {results['local']['guard_trip_rate']}%"
    )
    print(f"\ngo/no-go: {'GO' if go else 'NO-GO'}")
    for reason in reasons:
        print(f"  - {reason}")
    print(f"\nwritten to {run_dir}")


if __name__ == "__main__":
    main()
