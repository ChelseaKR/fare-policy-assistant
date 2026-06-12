"""Eval runner.

    python -m evals.runner --smoke              # 25-case CI subset
    python -m evals.runner --full               # everything, then regenerate reports
    python -m evals.runner --offline            # mock model, deterministic checks only
    python -m evals.runner --suite refusal      # one suite

Each run writes evals/runs/<timestamp>/ with results.jsonl (full traces) and
summary.json (scoreboard + versions). Judges run only when provider
credentials are available (AWS chain for bedrock, ANTHROPIC_API_KEY for
anthropic); otherwise judge verdicts are recorded as skipped, never as passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from assistant import config
from assistant.answer import answer_question
from assistant.ingest import load_chunks
from assistant.models import get_model
from assistant.retrieve import Retriever
from evals import judges
from evals.checks import run_checks


def load_suites(only: str | None = None) -> list[dict]:
    suites = []
    for path in sorted(config.EVAL_SUITES_DIR.glob("*.yaml")):
        if only and path.stem != only:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in data["cases"]:
            case["suite"] = path.stem
        suites.append(data)
    return suites


def validate_cases(suites: list[dict]) -> None:
    seen: set[str] = set()
    required = {"id", "question", "expected_behavior", "rationale"}
    for suite in suites:
        for case in suite["cases"]:
            missing = required - case.keys()
            if missing:
                raise SystemExit(f"case {case.get('id', '?')}: missing fields {sorted(missing)}")
            if case["id"] in seen:
                raise SystemExit(f"duplicate case id: {case['id']}")
            seen.add(case["id"])
            if case["expected_behavior"] not in ("answer", "partial", "refuse_redirect"):
                raise SystemExit(f"case {case['id']}: bad expected_behavior")


def _have_credentials(provider: str) -> bool:
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "bedrock":
        # Standard AWS credential chain, in the order we expect it here:
        # SSO profile (~/.aws/config after `aws sso login`), OIDC web
        # identity (GitHub Actions federation), env keys, or a shared
        # credentials file. An instance role is not detectable cheaply;
        # set FPA_ASSUME_AWS_CREDS=1 to force a live run.
        return bool(
            os.environ.get("AWS_PROFILE")
            or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("FPA_ASSUME_AWS_CREDS")
            or (Path.home() / ".aws" / "config").exists()
            or (Path.home() / ".aws" / "credentials").exists()
        )
    return provider == "mock"


def run(
    *,
    smoke: bool = False,
    offline: bool = False,
    suite: str | None = None,
) -> Path:
    cfg = config.Config()
    if offline:
        cfg = config.Config(
            models=config.ModelConfig(provider="mock", answer_model="mock", judge_model="mock")
        )
    if cfg.models.provider != "mock":
        assert cfg.models.judge_model != cfg.models.answer_model, (
            "judge model must differ from answer model"
        )

    have_key = not offline and _have_credentials(cfg.models.provider)
    if not offline and not have_key:
        print(
            f"No credentials for provider '{cfg.models.provider}': falling back to "
            "--offline (deterministic checks only).",
            file=sys.stderr,
        )
        return run(smoke=smoke, offline=True, suite=suite)

    suites = load_suites(suite)
    if not suites:
        raise SystemExit("no suites found")
    validate_cases(suites)

    chunks = load_chunks()
    corpus_doc_ids = {c.doc_id for c in chunks}
    retriever = Retriever(chunks, cfg.retrieval)
    answer_model = get_model(cfg.models.provider, cfg.models.answer_model)
    judge_model = get_model(cfg.models.provider, cfg.models.judge_model)
    run_judges = have_key and cfg.models.provider != "mock"

    run_dir = config.EVAL_RUNS_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"

    totals: dict[str, dict[str, int]] = {}
    records = []
    started = time.monotonic()

    for s in suites:
        for case in s["cases"]:
            if smoke and not case.get("smoke"):
                continue
            result = answer_question(
                case["question"], model=answer_model, retriever=retriever, cfg=cfg
            )
            checks = run_checks(case, result, corpus_doc_ids)
            verdicts = []
            if run_judges:
                if case["expected_behavior"] in ("answer", "partial") and result.kind == "answered":
                    verdicts.append(judges.judge_groundedness(judge_model, result, cfg))
                verdicts.append(
                    judges.judge_helpfulness(judge_model, result, case["expected_behavior"], cfg)
                )
            passed = all(c.passed for c in checks) and all(v.passed for v in verdicts)
            record = {
                "case_id": case["id"],
                "suite": case["suite"],
                "mirror_of": case.get("mirror_of"),
                "language": case.get("language", "en"),
                "expected_behavior": case["expected_behavior"],
                "question": case["question"],
                "rationale": case["rationale"],
                "answer": result.answer,
                "kind": result.kind,
                "guard_flags": result.guard_flags,
                "raw_model_answer": result.raw_model_answer,
                "citations": [asdict(c) for c in result.citations],
                "passages": [
                    {"chunk_id": sc.chunk.chunk_id, "section": sc.chunk.section,
                     "score": round(sc.score, 2), "text": sc.chunk.text[:600]}
                    for sc in result.passages
                ],
                "checks": [asdict(c) for c in checks],
                "judges": [asdict(v) for v in verdicts],
                "passed": passed,
            }
            records.append(record)
            t = totals.setdefault(case["suite"], {"passed": 0, "total": 0})
            t["total"] += 1
            t["passed"] += int(passed)
            status = "PASS" if passed else "FAIL"
            print(f"{status}  {case['id']}")

    with results_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "smoke" if smoke else ("suite:" + suite if suite else "full"),
        "offline": offline or not have_key,
        "judges_ran": run_judges,
        "answer_model": cfg.models.answer_model,
        "judge_model": cfg.models.judge_model,
        "prompt_versions": {
            name: config.prompt_version(name)
            for name in ("system", "answer_user", "judge_groundedness", "judge_helpfulness")
        },
        "duration_seconds": round(time.monotonic() - started, 1),
        "suites": {
            name: {**t, "pass_rate": round(100 * t["passed"] / t["total"], 1)}
            for name, t in sorted(totals.items())
        },
        "total": {
            "passed": sum(t["passed"] for t in totals.values()),
            "total": sum(t["total"] for t in totals.values()),
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    overall = summary["total"]
    print(f"\n{overall['passed']}/{overall['total']} passed → {run_dir}")
    return run_dir


def check_regression(run_dir: Path, threshold: float = 2.0) -> None:
    """Fail (exit 1) if any suite's pass rate dropped > threshold points vs.
    the committed baseline. Update the baseline deliberately with
    `python -m evals.runner --update-baseline`."""
    baseline_path = config.EVAL_RUNS_DIR.parent / "baseline.json"
    if not baseline_path.exists():
        print("no evals/baseline.json; skipping regression gate", file=sys.stderr)
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if summary.get("offline") and not baseline.get("offline"):
        print("offline run vs. live baseline; skipping regression gate", file=sys.stderr)
        return
    if summary.get("mode") != baseline.get("mode"):
        print(
            f"mode mismatch ({summary.get('mode')} vs baseline {baseline.get('mode')}); "
            "skipping regression gate",
            file=sys.stderr,
        )
        return
    regressions = []
    for suite, base in baseline["suites"].items():
        now = summary["suites"].get(suite)
        if now and now["pass_rate"] < base["pass_rate"] - threshold:
            regressions.append(f"{suite}: {base['pass_rate']}% → {now['pass_rate']}%")
    if regressions:
        print("REGRESSION (>2 points):\n  " + "\n  ".join(regressions), file=sys.stderr)
        raise SystemExit(1)


def update_baseline(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    baseline = {
        "from_run": summary["run_at"],
        "mode": summary["mode"],
        "offline": summary["offline"],
        "answer_model": summary["answer_model"],
        "suites": summary["suites"],
    }
    path = config.EVAL_RUNS_DIR.parent / "baseline.json"
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"baseline updated from {summary['run_at']} → {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--suite")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    run_dir = run(smoke=args.smoke, offline=args.offline, suite=args.suite)
    if args.full:
        from evals.report import generate

        generate(run_dir)
    if args.update_baseline:
        update_baseline(run_dir)
    else:
        check_regression(run_dir)


if __name__ == "__main__":
    main()
