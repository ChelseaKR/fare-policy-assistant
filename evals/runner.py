"""Eval runner.

    python -m evals.runner --smoke              # 25-case CI subset
    python -m evals.runner --full               # everything, then regenerate reports
    python -m evals.runner --offline            # mock model, deterministic checks only
    python -m evals.runner --suite refusal      # one suite
    python -m evals.runner --jobs 8              # bounded-concurrency case execution
    python -m evals.runner --no-cache            # skip the answer/judge cache (FIX-04 runs)
    python -m evals.runner --refresh-cache       # re-call the provider, then restore the cache
    python -m evals.runner --only-failed          # rerun only cases that failed last time
    python -m evals.runner --since 20260701T000000Z  # reuse unchanged cases from that run
    python -m evals.runner --replicates 3         # score every case 3x, Wilson intervals

Each run writes evals/runs/<timestamp>/ with results.jsonl (full traces) and
summary.json (scoreboard + versions). Judges run only when provider
credentials are available (AWS chain for bedrock, ANTHROPIC_API_KEY for
anthropic); otherwise judge verdicts are recorded as skipped, never as passes.

Cases execute under a bounded-concurrency ThreadPoolExecutor (`--jobs`,
default 4 — the pipeline is pure functions over an immutable retriever, and
4-8 workers fits Bedrock rate limits). Each case's multi-turn history replay
still runs sequentially within its own worker, so turns are never interleaved.
Answer and judge model calls are served from a content-keyed on-disk cache
(evals/cache.py) by default, so an incremental re-run after a one-prompt or
one-corpus change only pays for the cases that actually changed; `--no-cache`
disables this for runs that need to measure real model variance (FIX-04). CI
persists that cache across runs, so the cost of an unchanged suite is paid once
rather than once per pull request; `--refresh-cache` is the weekly cold run
that re-measures the provider and rewrites the stored answers (ADR 0022).

Variance runs (`--replicates N`, N > 1) score every case N times — a case's
replicate passes run sequentially inside its worker — and report a per-suite
mean pass rate with a Wilson 95% interval. A replicate run always bypasses the
cache (a cache-served replicate returns byte-identical answers and verdicts and
would measure zero variance) and cannot combine with `--since`/`--only-failed`
(reused cases contribute no fresh trials). A replicated multi-turn case replays
its history on every pass and pays for it every pass.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from assistant import config, corpus
from assistant import facts as facts_module
from assistant.answer import AnswerResult, answer_question
from assistant.ingest import load_chunks
from assistant.models import Model, get_model
from assistant.retrieve import Retriever
from evals import judges
from evals.cache import CachingModel, EvalCache, case_content_key
from evals.checks import run_checks
from evals.stats import wilson_interval


def _flatten_pairs(data: dict) -> list[dict]:
    """A sensitivity suite is written as `pairs:` of minimal-pair `variants:`.

    Flatten each variant into an ordinary case dict carrying a `pair_id` (the
    parent pair's id) and the pair's `boundary`, so every downstream consumer —
    validation, checks.py grading, credential gating, scoring — treats it as a
    normal case. The variants are re-grouped by `pair_id` only for the
    pair-level verdict (`pair_verdicts`), never for scoring.
    """
    cases = []
    for pair in data["pairs"]:
        for variant in pair["variants"]:
            variant["pair_id"] = pair["id"]
            variant.setdefault("boundary", pair.get("boundary"))
            cases.append(variant)
    return cases


def load_suites(only: str | None = None) -> list[dict]:
    suites = []
    for path in sorted(config.EVAL_SUITES_DIR.glob("*.yaml")):
        if only and path.stem != only:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "pairs" in data and "cases" not in data:
            data["cases"] = _flatten_pairs(data)
        for case in data["cases"]:
            case["suite"] = path.stem
        suites.append(data)
    return suites


def validate_cases(suites: list[dict]) -> None:
    seen: set[str] = set()
    required = {"id", "expected_behavior", "rationale"}
    for suite in suites:
        # Sensitivity suites carry minimal pairs that are flattened into cases.
        for pair in suite.get("pairs", []):
            if not pair.get("id"):
                raise SystemExit("sensitivity pair missing `id`")
            if not pair.get("boundary"):
                raise SystemExit(f"pair {pair['id']}: missing `boundary`")
            if len(pair.get("variants", [])) < 2:
                raise SystemExit(f"pair {pair['id']}: needs at least two variants")
        cases = suite.get("cases")
        if cases is None and "pairs" in suite:
            cases = _flatten_pairs(suite)
        for case in cases or []:
            # Auto-drafted skeletons (assistant.scaffold_agency) carry
            # `draft: true`. They have TODO questions and empty required_facts,
            # so they must never run or land in results: a human fills the facts
            # and removes the flag first. Refuse the whole run if any survive.
            if case.get("draft"):
                raise SystemExit(
                    f"case {case.get('id', '?')}: `draft: true` — fill it in and "
                    "remove the draft flag before running (see the scaffold checklist)"
                )
            missing = required - case.keys()
            if missing:
                raise SystemExit(f"case {case.get('id', '?')}: missing fields {sorted(missing)}")
            # A case is single-turn (`question`) or multi-turn (`turns`: a list
            # of questions, the last of which is the one under test).
            if "question" not in case and not case.get("turns"):
                raise SystemExit(f"case {case['id']}: needs `question` or `turns`")
            if case.get("turns") and len(case["turns"]) < 2:
                raise SystemExit(f"case {case['id']}: `turns` needs at least two questions")
            # `history`: a literal list of {q, a} pairs injected directly as the
            # follow-up's context (forged-history cases). It combines with a
            # single-turn `question` and is mutually exclusive with `turns`.
            if case.get("history") is not None:
                history = case["history"]
                if not isinstance(history, list) or not history:
                    raise SystemExit(f"case {case['id']}: `history` must be a non-empty list")
                for pair in history:
                    if not (
                        isinstance(pair, dict)
                        and isinstance(pair.get("q"), str)
                        and isinstance(pair.get("a"), str)
                    ):
                        raise SystemExit(
                            f"case {case['id']}: each `history` entry needs string `q` and `a`"
                        )
                if case.get("turns"):
                    raise SystemExit(
                        f"case {case['id']}: `history` combines with `question`, not `turns`"
                    )
                if "question" not in case:
                    raise SystemExit(f"case {case['id']}: `history` requires a `question`")
            if case["id"] in seen:
                raise SystemExit(f"duplicate case id: {case['id']}")
            seen.add(case["id"])
            if case["expected_behavior"] not in ("answer", "partial", "refuse_redirect"):
                raise SystemExit(f"case {case['id']}: bad expected_behavior")


def pair_verdicts(records: list[dict]) -> dict[str, bool]:
    """Group scored records by `pair_id` and return {pair_id: passed}.

    A minimal-pair boundary case only counts as distinguished if *every*
    variant passed — the per-variant required_facts / forbidden_content prove
    the answer actually changed (or held) across the boundary. One variant
    passing on boilerplate is not evidence of discrimination, so a mixed
    pass/fail pair reports failed.
    """
    grouped: dict[str, list[bool]] = {}
    for r in records:
        pid = r.get("pair_id")
        if not pid:
            continue
        grouped.setdefault(pid, []).append(bool(r["passed"]))
    return {pid: all(v) for pid, v in grouped.items()}


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
    if provider == "local":
        # No credentials to check — the analogous question is "is the Ollama
        # server up." A quick, short-timeout probe; any failure (not
        # running, wrong FPA_OLLAMA_HOST) reads as "not available" so the
        # normal --offline fallback below applies instead of hanging.
        import httpx

        host = config.resolve_provider_transport("local").base_url
        assert host is not None
        try:
            return httpx.get(f"{host}/api/version", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False
    return provider == "mock"


def _cost_block(cfg: config.Config, usage: dict[str, list[int]]) -> dict:
    """Exact token totals per model plus an estimated USD cost at list rates."""
    a_in, a_out, a_create, a_read = usage["answer"]
    j_in, j_out, j_create, j_read = usage["judge"]
    a_usd = config.estimate_cost_usd(
        cfg.models.answer_model,
        a_in,
        a_out,
        provider=cfg.models.provider,
        cache_creation_input_tokens=a_create,
        cache_read_input_tokens=a_read,
    )
    j_usd = config.estimate_cost_usd(
        cfg.models.judge_model,
        j_in,
        j_out,
        provider=cfg.models.provider,
        cache_creation_input_tokens=j_create,
        cache_read_input_tokens=j_read,
    )
    unpriced = [
        model
        for model, value in (
            (cfg.models.answer_model, a_usd),
            (cfg.models.judge_model, j_usd),
        )
        if value is None
    ]
    return {
        "answer_model": {
            "input_tokens": a_in,
            "output_tokens": a_out,
            "cache_creation_input_tokens": a_create,
            "cache_read_input_tokens": a_read,
            "est_usd": round(a_usd, 4) if a_usd is not None else None,
        },
        "judge_model": {
            "input_tokens": j_in,
            "output_tokens": j_out,
            "cache_creation_input_tokens": j_create,
            "cache_read_input_tokens": j_read,
            "est_usd": round(j_usd, 4) if j_usd is not None else None,
        },
        "total_tokens": a_in + a_out + j_in + j_out,
        "total_est_usd": (
            round(a_usd + j_usd, 4) if a_usd is not None and j_usd is not None else None
        ),
        "unpriced_models": unpriced,
    }


def _resolve_reference_run(name: str | None) -> Path | None:
    """The run directory `--since`/`--only-failed` compares against: the named
    run, or (name omitted) the most recent existing run. `None` if there is no
    prior run to compare against yet."""
    if name:
        run_dir = config.EVAL_RUNS_DIR / name
        if not run_dir.exists():
            raise SystemExit(f"no such run: {run_dir}")
        return run_dir
    if not config.EVAL_RUNS_DIR.exists():
        return None
    candidates = sorted(p for p in config.EVAL_RUNS_DIR.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _load_records(run_dir: Path) -> dict[str, dict]:
    results_path = run_dir / "results.jsonl"
    if not results_path.exists():
        return {}
    records = (json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines())
    return {r["case_id"]: r for r in records}


def _run_case(
    case: dict,
    *,
    answer_model: Model,
    judge_model: Model,
    retriever: Retriever,
    cfg: config.Config,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list] | None,
    run_judges: bool,
) -> tuple[dict, dict[str, list[int]]]:
    """Execute one case (including its multi-turn history replay, sequentially
    within this call so turns are never interleaved with another case's) and
    return its trace record plus the token usage it spent."""
    usage = {"answer": [0, 0, 0, 0], "judge": [0, 0, 0, 0]}
    judge_history: list[tuple[str, str]] | None = None
    if case.get("turns"):
        # Multi-turn: replay earlier turns to build history, then the final
        # turn is the one under test. Earlier turns' tokens count.
        history: list[tuple[str, str]] = []
        for q in case["turns"][:-1]:
            prior = answer_question(
                q, history=history or None, model=answer_model, retriever=retriever, cfg=cfg
            )
            usage["answer"][0] += prior.input_tokens
            usage["answer"][1] += prior.output_tokens
            usage["answer"][2] += prior.cache_creation_input_tokens
            usage["answer"][3] += prior.cache_read_input_tokens
            history.append((q, prior.answer))
        question = case["turns"][-1]
        result: AnswerResult = answer_question(
            question, history=history or None, model=answer_model, retriever=retriever, cfg=cfg
        )
        judge_history = history
    elif case.get("history"):
        injected = [(h["q"], h["a"]) for h in case["history"]]
        question = case["question"]
        result = answer_question(
            question, history=injected, model=answer_model, retriever=retriever, cfg=cfg
        )
        judge_history = injected
    else:
        question = case["question"]
        result = answer_question(question, model=answer_model, retriever=retriever, cfg=cfg)
    checks = run_checks(case, result, corpus_doc_ids, facts_by_doc)
    verdicts = []
    if run_judges:
        if case["expected_behavior"] in ("answer", "partial") and result.kind == "answered":
            verdicts.append(
                judges.judge_groundedness(judge_model, result, cfg, history=judge_history)
            )
        verdicts.append(
            judges.judge_helpfulness(
                judge_model,
                result,
                case["expected_behavior"],
                cfg,
                history=judge_history,
                rationale=case["rationale"],
            )
        )
    passed = all(c.passed for c in checks) and all(v.passed for v in verdicts)
    usage["answer"][0] += result.input_tokens
    usage["answer"][1] += result.output_tokens
    usage["answer"][2] += result.cache_creation_input_tokens
    usage["answer"][3] += result.cache_read_input_tokens
    for v in verdicts:
        usage["judge"][0] += v.input_tokens
        usage["judge"][1] += v.output_tokens
        usage["judge"][2] += v.cache_creation_input_tokens
        usage["judge"][3] += v.cache_read_input_tokens
    record = {
        "case_id": case["id"],
        "suite": case["suite"],
        "pair_id": case.get("pair_id"),
        "mirror_of": case.get("mirror_of"),
        "language": case.get("language", "en"),
        "expected_behavior": case["expected_behavior"],
        "question": question,
        "turns": case.get("turns"),
        "history": case.get("history"),
        "rationale": case["rationale"],
        "answer": result.answer,
        "kind": result.kind,
        "guard_flags": result.guard_flags,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_creation_input_tokens": result.cache_creation_input_tokens,
        "cache_read_input_tokens": result.cache_read_input_tokens,
        "raw_model_answer": result.raw_model_answer,
        "citations": [asdict(c) for c in result.citations],
        "passages": [
            {
                "chunk_id": sc.chunk.chunk_id,
                "section": sc.chunk.section,
                "score": round(sc.score, 2),
                "text": sc.chunk.text[:600],
            }
            for sc in result.passages
        ],
        "checks": [asdict(c) for c in checks],
        "judges": [asdict(v) for v in verdicts],
        "passed": passed,
    }
    return record, usage


def run(
    *,
    smoke: bool = False,
    offline: bool = False,
    suite: str | None = None,
    jobs: int = 4,
    use_cache: bool = True,
    refresh_cache: bool = False,
    only_failed: bool = False,
    since: str | None = None,
    replicates: int = 1,
) -> Path:
    if replicates < 1:
        raise SystemExit("--replicates must be >= 1")
    if refresh_cache and not use_cache:
        raise SystemExit(
            "--refresh-cache cannot combine with --no-cache: refreshing means "
            "re-measuring the provider and then storing the result, which a "
            "disabled cache has nowhere to put"
        )
    if refresh_cache and (only_failed or since):
        raise SystemExit(
            "--refresh-cache cannot combine with --since/--only-failed: a "
            "cold re-measurement must call the provider for every case, and "
            "reused cases call it for none"
        )
    if replicates > 1:
        if only_failed or since:
            raise SystemExit(
                "--replicates cannot combine with --since/--only-failed: a variance "
                "run must score every case fresh (reused cases contribute no trials)"
            )
        # A cache-served replicate returns byte-identical answers and verdicts
        # and would measure zero variance, so replicate runs always bypass the
        # answer/judge cache (equivalent to --no-cache). A replicate run also
        # measures variance rather than a canonical answer, so it must not
        # overwrite the stored entries either.
        use_cache = False
        refresh_cache = False
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
        return run(
            smoke=smoke,
            offline=True,
            suite=suite,
            jobs=jobs,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
            only_failed=only_failed,
            since=since,
            replicates=replicates,
        )

    suites = load_suites(suite)
    if not suites:
        raise SystemExit("no suites found")
    validate_cases(suites)

    chunks = load_chunks()
    corpus_doc_ids = {c.doc_id for c in chunks}
    corpus_version = corpus.corpus_version(chunks)
    facts_by_doc: dict[str, list] = collections.defaultdict(list)
    for fact in facts_module.load_facts(config.FACTS_PATH):
        facts_by_doc[fact.doc_id].append(fact)
    retriever = Retriever(chunks, cfg.retrieval)
    run_judges = have_key and cfg.models.provider != "mock"

    cache = EvalCache(config.EVAL_CACHE_DIR, enabled=use_cache, refresh=refresh_cache)
    answer_model: Model = get_model(cfg.models.provider, cfg.models.answer_model)
    judge_model: Model = get_model(cfg.models.provider, cfg.models.judge_model)
    if use_cache:
        answer_model = CachingModel(
            answer_model, cache, provider=cfg.models.provider, kind="answer"
        )
        judge_model = CachingModel(judge_model, cache, provider=cfg.models.provider, kind="judge")

    prompt_versions = {
        name: config.prompt_version(name)
        for name in ("system", "answer_user", "judge_groundedness", "judge_helpfulness")
    }

    # Flatten to an ordered case list (respecting --smoke) once, so
    # --only-failed / --since filtering and the concurrent executor both work
    # off the same sequence.
    ordered_cases = [
        case for s in suites for case in s["cases"] if not (smoke and not case.get("smoke"))
    ]

    reference_dir = None
    reference_records: dict[str, dict] = {}
    if only_failed or since:
        reference_dir = _resolve_reference_run(since)
        if reference_dir is not None:
            reference_records = _load_records(reference_dir)

    if only_failed:
        failed_ids = {cid for cid, r in reference_records.items() if not r.get("passed", True)}
        ordered_cases = [c for c in ordered_cases if c["id"] in failed_ids]
        if not ordered_cases:
            raise SystemExit(
                "--only-failed: no failed cases in reference run "
                f"({reference_dir or 'none found'}); nothing to do"
            )

    def _case_key(case: dict) -> str:
        content = json.dumps(case["turns"]) if case.get("turns") else case["question"]
        return case_content_key(
            case_id=case["id"],
            question_or_turns=content,
            expected_behavior=case["expected_behavior"],
            provider=cfg.models.provider,
            answer_model=cfg.models.answer_model,
            judge_model=cfg.models.judge_model,
            corpus_version=corpus_version,
            prompt_versions=prompt_versions,
            run_judges=run_judges,
        )

    to_run: list[dict] = []
    reused: list[dict] = []
    if since and reference_dir is not None:
        for case in ordered_cases:
            prior = reference_records.get(case["id"])
            if prior and prior.get("case_key") == _case_key(case):
                reused.append(prior)
            else:
                to_run.append(case)
    else:
        to_run = ordered_cases

    run_dir = config.EVAL_RUNS_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"

    totals: dict[str, dict[str, int]] = {}
    # Per-suite (successes, trials) across all replicate passes, used only when
    # replicates > 1 to derive a mean pass rate and a Wilson interval.
    trials: dict[str, dict[str, int]] = {}
    # Exact token usage, split by model (answer vs judge) since they price
    # differently. Aggregated into an estimated per-run cost in the summary.
    # Reused (--since) cases spent no tokens this run, so they contribute 0.
    # [canonical input total, output, cache creation input, cache read input]
    usage = {"answer": [0, 0, 0, 0], "judge": [0, 0, 0, 0]}
    started = time.monotonic()

    def _execute(case: dict) -> tuple[dict, dict[str, list[int]], int]:
        """Run one case's replicate passes (sequentially, within this worker —
        so a multi-turn replay is never interleaved) and return
        (record, usage, passes). The first pass supplies the full trace; passes
        across replicates form the case's pass fraction. At N=1 this is exactly
        one `_run_case` call."""
        agg: dict[str, list[int]] = {
            "answer": [0, 0, 0, 0],
            "judge": [0, 0, 0, 0],
        }
        record: dict | None = None
        passes = 0
        for _rep in range(replicates):
            rep_record, case_usage = _run_case(
                case,
                answer_model=answer_model,
                judge_model=judge_model,
                retriever=retriever,
                cfg=cfg,
                corpus_doc_ids=corpus_doc_ids,
                facts_by_doc=facts_by_doc,
                run_judges=run_judges,
            )
            for kind in ("answer", "judge"):
                for index in range(4):
                    agg[kind][index] += case_usage[kind][index]
            passes += int(rep_record["passed"])
            if record is None:
                record = rep_record
        assert record is not None
        if replicates > 1:
            # A case's "passed" stays the first pass's verdict (its trace);
            # pass_fraction carries the measured across-replicate rate for the
            # interval and for compare.py. Suite totals count majority votes.
            record["replicates"] = replicates
            record["pass_fraction"] = round(passes / replicates, 4)
        record["case_key"] = _case_key(case)
        return record, agg, passes

    fresh_by_id: dict[str, tuple[dict, dict[str, list[int]], int]] = {}
    if jobs <= 1:
        for case in to_run:
            fresh_by_id[case["id"]] = _execute(case)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_execute, case): case["id"] for case in to_run}
            for fut in as_completed(futures):
                fresh_by_id[futures[fut]] = fut.result()

    # Reassemble in the original suite order regardless of execution/reuse
    # path, so results.jsonl is stable run to run for the same case set.
    reused_by_id = {r["case_id"]: r for r in reused}
    records = []
    for case in ordered_cases:
        if case["id"] in fresh_by_id:
            record, case_usage, passes = fresh_by_id[case["id"]]
            usage["answer"][0] += case_usage["answer"][0]
            usage["answer"][1] += case_usage["answer"][1]
            usage["answer"][2] += case_usage["answer"][2]
            usage["answer"][3] += case_usage["answer"][3]
            usage["judge"][0] += case_usage["judge"][0]
            usage["judge"][1] += case_usage["judge"][1]
            usage["judge"][2] += case_usage["judge"][2]
            usage["judge"][3] += case_usage["judge"][3]
            status = "PASS" if record["passed"] else "FAIL"
        else:
            record = reused_by_id[case["id"]]
            passes = int(record["passed"])  # reuse path implies replicates == 1
            status = ("PASS" if record["passed"] else "FAIL") + " (reused)"
        records.append(record)
        t = totals.setdefault(case["suite"], {"passed": 0, "total": 0})
        t["total"] += 1
        # Count a case as passed by majority vote across replicates (== the
        # single pass at N=1), so passed/total stays interpretable.
        t["passed"] += 1 if passes * 2 >= replicates else 0
        tr = trials.setdefault(case["suite"], {"successes": 0, "trials": 0})
        tr["successes"] += passes
        tr["trials"] += replicates
        print(f"{status}  {case['id']}")

    with results_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cache.save()

    def _suite_entry(name: str, t: dict[str, int]) -> dict:
        entry = {**t, "pass_rate": round(100 * t["passed"] / t["total"], 1)}
        if replicates > 1:
            # Report the mean pass rate over all replicate trials and its Wilson
            # 95% interval, so the headline is a measured band, not a point.
            tr = trials[name]
            low, high = wilson_interval(tr["successes"], tr["trials"])
            entry["pass_rate"] = round(100 * tr["successes"] / tr["trials"], 1)
            entry["ci_low"] = round(100 * low, 1)
            entry["ci_high"] = round(100 * high, 1)
            entry["replicates"] = replicates
        return entry

    summary = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "smoke" if smoke else ("suite:" + suite if suite else "full"),
        "offline": offline or not have_key,
        "judges_ran": run_judges,
        "answer_model": cfg.models.answer_model,
        "judge_model": cfg.models.judge_model,
        "prompt_versions": prompt_versions,
        # Pinned so the provenance gate (evals/provenance.py) can prove EVALS.md,
        # the baseline, and the audit dataset describe the same corpus HEAD ships.
        "corpus_version": corpus_version,
        "duration_seconds": round(time.monotonic() - started, 1),
        "cost": _cost_block(cfg, usage),
        "execution": {
            "jobs": jobs,
            "cache": cache.stats(),
            "only_failed": only_failed,
            "since": since or (reference_dir.name if only_failed and reference_dir else None),
            "reused_cases": len(reused),
            "executed_cases": len(to_run),
        },
        "suites": {name: _suite_entry(name, t) for name, t in sorted(totals.items())},
        "total": {
            "passed": sum(t["passed"] for t in totals.values()),
            "total": sum(t["total"] for t in totals.values()),
        },
    }
    if replicates > 1:
        summary["replicates"] = replicates

    # Counterfactual sensitivity: fold the pair-level verdict into the
    # sensitivity suite's summary. A pair is distinguished only if every one of
    # its variants passed (see `pair_verdicts`).
    verdicts = pair_verdicts(records)
    if verdicts and "sensitivity" in summary["suites"]:
        summary["suites"]["sensitivity"]["pairs_passed"] = sum(verdicts.values())
        summary["suites"]["sensitivity"]["pairs_total"] = len(verdicts)

    # Bilingual parity (M-1): record the ES-vs-mirrored-EN delta alongside the
    # scoreboard so downstream tools (report, history) read one number instead
    # of re-deriving it from records.
    parity = parity_delta(records)
    if parity:
        summary["parity"] = parity

    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    overall = summary["total"]
    print(f"\n{overall['passed']}/{overall['total']} passed → {run_dir}")
    return run_dir


def suite_regressed(base: dict, now: dict, threshold: float = 2.0, case_floor: int = 2) -> bool:
    """A suite regresses only if its pass rate dropped more than `threshold`
    points AND its pass count dropped by at least `case_floor` cases.

    The case floor exists because the percentage gate alone is incoherent on
    small suites: one case in the 6-case conversation suite is 16.7 points, so a
    single boundary case flipping under LLM-judge variance would always trip a
    2-point gate. Two cases is still a cheap, sensitive signal on the larger
    suites while absorbing the one-case judge noise the harness sees run to run.

    `case_floor` defaults to 2 (the historical hand-tuned value). Once a
    replicated run (`--replicates`) has measured the per-case flip rate, pass a
    floor derived from it — e.g. `ceil` of the expected number of cases that
    flip under the null — instead of the guess. See `flip_case_floor`.
    """
    rate_drop = now["pass_rate"] < base["pass_rate"] - threshold
    case_drop = base["passed"] - now["passed"] >= case_floor
    return rate_drop and case_drop


def flip_case_floor(flip_rate: float, n_cases: int, safety: float = 1.0) -> int:
    """Derive a `suite_regressed` case floor from a measured per-case flip rate.

    Given the fraction of cases that flip pass/fail between replicate runs of the
    same config (`flip_rate`, from a `--replicates` run) and the suite size, the
    expected number of noise flips is `flip_rate * n_cases`. The floor is that
    expectation rounded up, times `safety`, and never below the historical 2 —
    so switching a suite to a measured floor can only make the gate stricter,
    never looser than today.
    """
    expected = math.ceil(flip_rate * n_cases * safety)
    return max(2, expected)


# ── bilingual parity gate (M-1; audit P1-1; AIEV-10/11, I18N-22) ─────────────

PARITY_SUITE = "multilingual"
PARITY_THRESHOLD_PP = 5.0
PARITY_CASE_FLOOR = 2
MACRO_THRESHOLD_PP = 5.0
_STRETCH_PREFIX = "stretch_"
EXPECTED_BELOW_MACRO_PATH = config.REPO_ROOT / "evals" / "expected_below_macro.json"


def parity_delta(records: list[dict], suite: str = PARITY_SUITE) -> dict | None:
    """The ES-vs-mirrored-EN pass delta over `suite`, from one run's records.

    Each case in `suite` that names a `mirror_of` English case present in the
    same run forms a pair; the delta is the mirrored-English pass rate minus
    the Spanish pass rate, in percentage points. Positive means the English
    mirrors passed more often — the equity gap the parity gate exists to catch.
    Comparing within a single run means judge model, prompts, and corpus cancel
    out, so the number is meaningful on any mode (offline, smoke, full).

    Pairs whose mirror is absent from the run (e.g. a smoke subset that kept
    the Spanish case but not its mirror) are skipped; returns None when no
    complete pair exists, so partial runs skip the gate loudly rather than
    tripping on noise.
    """
    by_id = {r["case_id"]: r for r in records}
    pairs = [
        (r, by_id[r["mirror_of"]])
        for r in records
        if r["suite"] == suite and r.get("mirror_of") in by_id
    ]
    if not pairs:
        return None
    passed = sum(1 for r, _ in pairs if r["passed"])
    mirror_passed = sum(1 for _, en in pairs if en["passed"])
    n = len(pairs)
    return {
        "suite": suite,
        "pairs": n,
        "passed": passed,
        "mirror_passed": mirror_passed,
        "delta_pp": round((mirror_passed - passed) * 100 / n, 1),
    }


def parity_regressed(
    parity: dict, threshold: float = PARITY_THRESHOLD_PP, case_floor: int = PARITY_CASE_FLOOR
) -> bool:
    """Same two-condition shape as `suite_regressed`: the parity gap must
    exceed `threshold` points AND at least `case_floor` more mirrored English
    cases than Spanish cases must have passed. The case floor absorbs the
    one-case judge-noise flip that would otherwise dominate a small pair set
    (see `suite_regressed`'s rationale); one flipped pair out of 22 is 4.5
    points and 1 case — noise, not an equity finding."""
    gap_cases = parity["mirror_passed"] - parity["passed"]
    return parity["delta_pp"] > threshold and gap_cases >= case_floor


def suites_below_macro(suites: dict, threshold: float = MACRO_THRESHOLD_PP) -> dict[str, dict]:
    """The general per-suite form of the parity gate (AIEV-10): every gated
    suite's pass rate must be at least the macro pass rate minus `threshold`
    points, where macro is the unweighted mean over gated suites.

    `stretch_*` suites are excluded from both the mean and the gate:
    docs/ROADMAP.md P3-3 and the report's stretch-parity section promise that a
    stretch language's score is reported honestly but never fails a build.

    Returns {suite: {"pass_rate", "macro", "floor"}} for each offender; floors
    are compared unrounded and rounded only for display.
    """
    gated = {n: s for n, s in suites.items() if not n.startswith(_STRETCH_PREFIX)}
    if not gated:
        return {}
    macro = sum(s["pass_rate"] for s in gated.values()) / len(gated)
    floor = macro - threshold
    return {
        name: {
            "pass_rate": s["pass_rate"],
            "macro": round(macro, 1),
            "floor": round(floor, 1),
        }
        for name, s in gated.items()
        if s["pass_rate"] < floor
    }


def expected_below_macro(path: Path | None = None) -> dict[str, str]:
    """The loud escape hatch for the below-macro form: a committed JSON file
    mapping suite name → written rationale (keys starting with "_" are file
    commentary). An annotated suite is reported, not failed — mirroring the
    `stale_acknowledged.json` pattern: a gap may be accepted, but only in
    writing, in the diff, deleted when the suite recovers."""
    p = path or EXPECTED_BELOW_MACRO_PATH
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parity_problems(
    records: list[dict], suites: dict, annotations: dict[str, str] | None = None
) -> list[str]:
    """All parity-gate findings for one run; empty is clean. Pure so the
    committed-report checker and tests can exercise it without a run dir."""
    notes = expected_below_macro() if annotations is None else annotations
    problems = []
    parity = parity_delta(records)
    if parity is None:
        print("no complete Spanish/English mirror pairs in this run; parity skipped")
    elif parity_regressed(parity):
        problems.append(
            f"Spanish parity: {parity['passed']}/{parity['pairs']} vs mirrored English "
            f"{parity['mirror_passed']}/{parity['pairs']} — gap {parity['delta_pp']} pp "
            f"exceeds {PARITY_THRESHOLD_PP:g} pp on {PARITY_CASE_FLOOR}+ cases"
        )
    for name, o in sorted(suites_below_macro(suites).items()):
        if name in notes:
            continue
        problems.append(
            f"{name}: {o['pass_rate']}% is below the macro floor {o['floor']}% "
            f"(macro {o['macro']}% − {MACRO_THRESHOLD_PP:g} pp) with no written "
            "annotation in evals/expected_below_macro.json"
        )
    return problems


def check_parity(run_dir: Path) -> None:
    """Fail (exit 1) if the run trips the bilingual parity gate: the Spanish
    vs mirrored-English delta exceeds 5 points on 2+ cases, or any gated suite
    sits more than 5 points below the macro pass rate without a written
    annotation. There is no silent skip: fix the gap, or annotate it in
    `evals/expected_below_macro.json` with a rationale that survives review."""
    records = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    problems = parity_problems(records, summary["suites"])
    if problems:
        print("PARITY GATE (M-1):\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)


def check_regression(run_dir: Path, threshold: float = 2.0) -> None:
    """Fail (exit 1) if any suite regressed vs. the committed baseline (see
    `suite_regressed`). Update the baseline deliberately with
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
        if now and suite_regressed(base, now, threshold):
            regressions.append(
                f"{suite}: {base['passed']}/{base['total']} → {now['passed']}/{now['total']}"
            )
    if regressions:
        print(
            "REGRESSION (>2 points and >=2 cases):\n  " + "\n  ".join(regressions), file=sys.stderr
        )
        raise SystemExit(1)


def update_baseline(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    baseline = {
        "from_run": summary["run_at"],
        "mode": summary["mode"],
        "offline": summary["offline"],
        "answer_model": summary["answer_model"],
        # Provenance so the gate can catch a baseline left behind by a prompt or
        # corpus change. Older/synthetic summaries (e.g. test fixtures, or runs
        # from before this field existed) may carry neither key, so fall back
        # rather than raising: an absent prompt_versions renders as {} instead
        # of crashing the update.
        "provenance": {
            "prompt_versions": summary.get("prompt_versions") or {},
            "corpus_version": summary.get("corpus_version") or corpus.corpus_version(),
        },
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
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="bounded-concurrency workers for case execution (default 4)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="disable the answer/judge cache — use for FIX-04 variance-measurement runs",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="call the provider for every case (no cache reads) but store the results, so a "
        "cold re-measurement leaves the cache agreeing with the scoreboard it published",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="only run cases that failed in the reference run (--since, or the latest run)",
    )
    parser.add_argument(
        "--since",
        metavar="RUN",
        help="reuse cases unchanged (by content key) from evals/runs/RUN; also the reference "
        "run for --only-failed if given",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=1,
        help="run each case N times and report a per-suite mean ± Wilson interval "
        "(N=1, the default, is byte-identical to a single run). Live and paid; "
        "always bypasses the cache and excludes --since/--only-failed.",
    )
    args = parser.parse_args()

    run_dir = run(
        smoke=args.smoke,
        offline=args.offline,
        suite=args.suite,
        jobs=args.jobs,
        use_cache=not args.no_cache,
        refresh_cache=args.refresh_cache,
        only_failed=args.only_failed,
        since=args.since,
        replicates=args.replicates,
    )
    if args.full:
        from evals.report import generate

        generate(run_dir)
    # Parity is within-run (ES vs mirrored EN of the same run), so unlike the
    # baseline regression gate it applies even when the baseline is being
    # deliberately re-set — a re-baseline must not silence an equity gap.
    check_parity(run_dir)
    if args.update_baseline:
        update_baseline(run_dir)
    else:
        check_regression(run_dir)


if __name__ == "__main__":
    main()
