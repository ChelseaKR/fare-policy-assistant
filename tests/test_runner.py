"""Eval-runner tests.

The runner is the headline deliverable. These exercise it end to end on the
real suites and corpus in offline/mock mode (no model calls, no cost), plus the
credential gate, the cost accounting, suite loading/validation, and the
regression gate. Everything writes to a tmp runs directory so the committed
baseline and report are never touched.
"""

from __future__ import annotations

import hashlib
import json
import stat
from types import SimpleNamespace

import pytest

from assistant import config
from evals import runner


@pytest.fixture
def tmp_runs(tmp_path, monkeypatch):
    """Redirect eval-run output (and the baseline next to it) into a temp dir."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(config, "EVAL_RUNS_DIR", runs)
    # Isolate the answer/judge cache too, so tests never read or write the
    # real repo's evals/cache/.
    monkeypatch.setattr(config, "EVAL_CACHE_DIR", tmp_path / "cache")
    return runs


# ── load_suites / validate_cases ─────────────────────────────────────────────


def test_load_suites_reads_every_suite_and_tags_each_case():
    suites = runner.load_suites()
    assert suites, "expected the committed eval suites to load"
    for s in suites:
        for case in s["cases"]:
            assert case["suite"], "each case is tagged with its suite stem"


def test_load_suites_only_filter_selects_one_suite():
    only = runner.load_suites(only="refusal")
    assert len(only) == 1
    assert all(c["suite"] == "refusal" for c in only[0]["cases"])


def test_validate_cases_rejects_duplicate_ids():
    suites = [
        {
            "cases": [
                {"id": "dup", "question": "a?", "expected_behavior": "answer", "rationale": "x"},
                {"id": "dup", "question": "b?", "expected_behavior": "answer", "rationale": "x"},
            ]
        }
    ]
    with pytest.raises(SystemExit, match="duplicate case id"):
        runner.validate_cases(suites)


def test_validate_cases_rejects_bad_expected_behavior():
    suites = [
        {
            "cases": [
                {"id": "c", "question": "a?", "expected_behavior": "maybe", "rationale": "x"},
            ]
        }
    ]
    with pytest.raises(SystemExit, match="bad expected_behavior"):
        runner.validate_cases(suites)


def test_validate_cases_rejects_missing_required_fields():
    suites = [{"cases": [{"id": "c", "question": "a?"}]}]
    with pytest.raises(SystemExit, match="missing fields"):
        runner.validate_cases(suites)


def test_the_committed_suites_validate():
    # Guards against a malformed real suite shipping: the runner would refuse it.
    runner.validate_cases(runner.load_suites())


def test_run_raises_when_no_suite_matches(tmp_runs):
    import pytest as _pytest

    with _pytest.raises(SystemExit, match="no suites found"):
        runner.run(offline=True, suite="does-not-exist")


# ── credential gate ──────────────────────────────────────────────────────────


def test_have_credentials_mock_is_always_available():
    assert runner._have_credentials("mock") is True


def test_have_credentials_anthropic_needs_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner._have_credentials("anthropic") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert runner._have_credentials("anthropic") is True


def test_have_credentials_bedrock_reads_aws_chain(monkeypatch, tmp_path):
    for var in (
        "AWS_PROFILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ACCESS_KEY_ID",
        "FPA_ASSUME_AWS_CREDS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Point HOME at an empty dir so a real ~/.aws on the dev box can't leak in.
    monkeypatch.setattr(runner.Path, "home", classmethod(lambda cls: tmp_path))
    assert runner._have_credentials("bedrock") is False
    monkeypatch.setenv("AWS_PROFILE", "default")
    assert runner._have_credentials("bedrock") is True


def test_have_credentials_local_probes_the_configured_transport(monkeypatch):
    import httpx

    monkeypatch.setattr(
        config,
        "resolve_provider_transport",
        lambda _provider: SimpleNamespace(base_url="http://ollama.test"),
    )
    calls = []

    def available(url, *, timeout):
        calls.append((url, timeout))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(httpx, "get", available)
    assert runner._have_credentials("local") is True
    assert calls == [("http://ollama.test/api/version", 2.0)]

    def unavailable(_url, *, timeout):
        assert timeout == 2.0
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", unavailable)
    assert runner._have_credentials("local") is False


# ── execution environment contract ──────────────────────────────────────────


def test_effective_eval_environment_validates_supplied_string_mapping():
    assert runner._effective_eval_environment({"FPA_PROVIDER": "mock"}) == {"FPA_PROVIDER": "mock"}
    with pytest.raises(SystemExit, match="only strings"):
        runner._effective_eval_environment({"FPA_PROVIDER": 1})


def test_effective_eval_environment_decodes_lambda_variables(monkeypatch):
    monkeypatch.setenv(
        runner._EFFECTIVE_ENVIRONMENT_JSON,
        json.dumps({"Variables": {"FPA_PROVIDER": "mock"}}),
    )
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    assert runner._effective_eval_environment() == {
        "FPA_PROVIDER": "mock",
        "AWS_REGION": "us-west-2",
    }


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ("not-json", "must contain valid JSON"),
        ("[]", "string environment mapping"),
        ('{"FPA_PROVIDER":1}', "string environment mapping"),
    ],
)
def test_effective_eval_environment_rejects_malformed_payloads(
    monkeypatch,
    encoded,
    message,
):
    monkeypatch.setenv(runner._EFFECTIVE_ENVIRONMENT_JSON, encoded)
    with pytest.raises(SystemExit, match=message):
        runner._effective_eval_environment()


def test_environment_overlay_is_exact_and_restores_process_state(monkeypatch):
    monkeypatch.setenv("FPA_PROVIDER", "anthropic")
    monkeypatch.setenv("FPA_DENSE", "1")
    monkeypatch.delenv("FPA_JUDGE_MODEL", raising=False)

    with runner._environment_overlay(
        {"FPA_PROVIDER": "mock", "FPA_JUDGE_MODEL": "judge-under-test"}
    ):
        assert runner.os.environ["FPA_PROVIDER"] == "mock"
        assert runner.os.environ["FPA_JUDGE_MODEL"] == "judge-under-test"
        assert "FPA_DENSE" not in runner.os.environ

    assert runner.os.environ["FPA_PROVIDER"] == "anthropic"
    assert runner.os.environ["FPA_DENSE"] == "1"
    assert "FPA_JUDGE_MODEL" not in runner.os.environ


# ── cost accounting ──────────────────────────────────────────────────────────


def test_cost_block_aggregates_tokens_and_estimates_usd():
    cfg = config.Config(
        models=config.ModelConfig(
            provider="anthropic", answer_model="claude-haiku-4-5", judge_model="claude-sonnet-4-6"
        )
    )
    usage = {
        "answer": [1_000_000, 1_000_000, 0, 0],
        "judge": [1_000_000, 1_000_000, 0, 0],
    }
    block = runner._cost_block(cfg, usage)
    # haiku $1/$5 per 1M, sonnet $3/$15 per 1M.
    assert block["answer_model"]["est_usd"] == pytest.approx(6.0)
    assert block["judge_model"]["est_usd"] == pytest.approx(18.0)
    assert block["total_tokens"] == 4_000_000
    assert block["total_est_usd"] == pytest.approx(24.0)
    assert block["unpriced_models"] == []


def test_cost_block_surfaces_unknown_model_instead_of_silent_zero():
    cfg = config.Config(
        models=config.ModelConfig(
            provider="anthropic", answer_model="future-model", judge_model="claude-sonnet-4-6"
        )
    )
    block = runner._cost_block(cfg, {"answer": [100, 10, 0, 0], "judge": [100, 10, 0, 0]})
    assert block["answer_model"]["est_usd"] is None
    assert block["total_est_usd"] is None
    assert block["unpriced_models"] == ["future-model"]


def test_cost_block_applies_bedrock_multi_region_premium():
    cfg = config.Config(
        models=config.ModelConfig(
            provider="bedrock",
            answer_model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            judge_model="us.anthropic.claude-sonnet-4-6",
        )
    )
    block = runner._cost_block(
        cfg,
        {
            "answer": [1_000_000, 1_000_000, 0, 0],
            "judge": [1_000_000, 1_000_000, 0, 0],
        },
    )
    assert block["answer_model"]["est_usd"] == pytest.approx(6.6)
    assert block["judge_model"]["est_usd"] == pytest.approx(19.8)
    assert block["total_est_usd"] == pytest.approx(26.4)


def test_cost_block_prices_cache_buckets_without_double_charging():
    cfg = config.Config(
        models=config.ModelConfig(
            provider="anthropic",
            answer_model="claude-haiku-4-5",
            judge_model="claude-sonnet-4-6",
        )
    )
    block = runner._cost_block(
        cfg,
        {
            "answer": [1_000_000, 0, 200_000, 300_000],
            "judge": [0, 0, 0, 0],
        },
    )
    assert block["answer_model"]["cache_creation_input_tokens"] == 200_000
    assert block["answer_model"]["cache_read_input_tokens"] == 300_000
    assert block["answer_model"]["est_usd"] == pytest.approx(0.78)


# ── full offline run end to end ──────────────────────────────────────────────


def _summary(run_dir):
    return json.loads((run_dir / "summary.json").read_text())


def _captured_file(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    captured = runner._capture_regular_file(path, name)
    assert captured is not None
    return captured


def test_offline_suite_run_writes_traces_and_scoreboard(tmp_runs):
    run_dir = runner.run(offline=True, suite="refusal")
    assert run_dir.parent == tmp_runs
    summary = _summary(run_dir)
    assert summary["offline"] is True
    assert summary["judges_ran"] is False  # never judge offline
    assert summary["run_at"].endswith("Z")
    assert summary["run_id"] == run_dir.name
    assert (
        summary["results_sha256"]
        == hashlib.sha256((run_dir / "results.jsonl").read_bytes()).hexdigest()
    )
    assert summary["gate_status"] == "pending"
    assert summary["promotion_requested"] is False
    assert summary["attestation"]["promotion"]["eligible"] is False
    assert "not_promotion_run" in summary["attestation"]["promotion"]["reasons"]
    subject = summary["attestation"]["subject"]
    assert subject["descriptor_verified"] is False
    if subject["source_state"] == "dirty":
        assert subject["source_revision"] is None
        assert subject["release_version"] is None
    else:
        assert subject["source_revision"] == subject["head_revision"]
        assert len(subject["release_version"]) == 64
    assert summary["attestation"]["context_version"]
    assert summary["evaluation_inputs"]["facts"]["facts_version"]
    assert summary["evaluation_inputs"]["gtfs"]["schema"].endswith("gtfs-legacy-eval-input.v1")
    assert summary["evaluation_inputs"]["evaluator"]["evaluator_version"]
    assert summary["answer_model"] == "mock"
    assert summary["served_models"] == {"answer": ["mock"], "judge": []}
    assert "refusal" in summary["suites"]
    # results.jsonl carries one full trace per case.
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    assert len(records) == summary["suites"]["refusal"]["total"]
    assert all("checks" in r and "passages" in r for r in records)
    assert all("answer_models_served" in r and "judge_models_served" in r for r in records)
    assert all(
        r["run_context_version"] == summary["attestation"]["context_version"]
        and len(r["case_semantics_version"]) == 64
        for r in records
    )
    assert [entry["case_id"] for entry in summary["attestation"]["evidence"]["case_manifest"]] == [
        record["case_id"] for record in records
    ]
    assert [
        entry["case_semantics_version"]
        for entry in summary["attestation"]["evidence"]["case_manifest"]
    ] == [record["case_semantics_version"] for record in records]


def test_run_never_reloads_captured_prompts_or_structured_fares(tmp_runs, monkeypatch):
    expected_system = config.load_prompt("system")
    expected_answer_user = config.load_prompt("answer_user")
    real_answer_question = runner.answer_question
    answer_calls = 0

    def captured_answer_question(*args, **kwargs):
        nonlocal answer_calls
        answer_calls += 1
        assert kwargs["system_prompt"] == expected_system
        assert kwargs["answer_user_prompt"] == expected_answer_user
        return real_answer_question(*args, **kwargs)

    captured_paths = {
        config.CHUNKS_PATH,
        config.FACTS_PATH,
        config.MANIFEST_PATH,
        config.ANSWER_SCHEMA_PATH,
        *(config.PROMPTS_DIR / f"{name}.txt" for name in runner.PROMPT_NAMES),
    }
    real_read_bytes = runner.Path.read_bytes

    def unexpected_path_read(path):
        if path in captured_paths:
            pytest.fail(f"captured evaluation input was reopened: {path}")
        return real_read_bytes(path)

    def unexpected_reload(_name):
        pytest.fail("evaluation behavior must use the captured prompt strings")

    monkeypatch.setattr(runner.Path, "read_bytes", unexpected_path_read)
    monkeypatch.setattr(config, "load_prompt", unexpected_reload)
    monkeypatch.setattr(runner, "answer_question", captured_answer_question)
    monkeypatch.setattr(
        runner.fare_table,
        "structured_fares",
        lambda _agency: pytest.fail("GTFS fare files must not be reopened per case"),
    )
    runner.run(offline=True, suite="refusal", jobs=1, use_cache=False)
    assert answer_calls > 0


def test_capture_regular_file_rejects_an_in_place_read_race(tmp_path, monkeypatch):
    selected = tmp_path / "racing.txt"
    selected.write_bytes(b"a" * 32)
    real_read = runner.os.read
    raced = False

    def racing_read(descriptor, size):
        nonlocal raced
        block = real_read(descriptor, size)
        if block and not raced:
            raced = True
            selected.write_bytes(b"b" * 32)
        return block

    monkeypatch.setattr(runner.os, "read", racing_read)
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="changed"):
        runner._capture_regular_file(selected, "racing input")


def test_captured_jsonl_inputs_preserve_unicode_line_separators(tmp_path):
    chunk_record = {
        "chunk_id": "test#0",
        "doc_id": "test",
        "agency": "TEST",
        "agency_full": "Test Transit",
        "doc_title": "Fares",
        "url": "https://example.gov/fares",
        "fetch_date": "2026-07-30",
        "language": "en",
        "section": "Prices",
        "text": "first\u2028second\u2029third",
    }
    fact_record = {
        "agency": "TEST",
        "doc_id": "test",
        "chunk_id": "test#0",
        "program": "first\u2028second\u2029third",
        "rider_class": "adult",
        "price": 2.5,
        "currency": "USD",
        "age_min": None,
        "age_max": None,
        "confidence": "parsed",
    }
    chunks_path = tmp_path / "chunks.jsonl"
    facts_path = tmp_path / "facts.jsonl"
    chunks_path.write_bytes((json.dumps(chunk_record, ensure_ascii=False) + "\n").encode("utf-8"))
    facts_path.write_bytes((json.dumps(fact_record, ensure_ascii=False) + "\n").encode("utf-8"))
    captured_chunks = runner._capture_regular_file(chunks_path, "chunks")
    captured_facts = runner._capture_regular_file(facts_path, "facts")
    assert captured_chunks is not None
    assert captured_facts is not None

    chunks = runner._parse_chunks(captured_chunks)
    facts = runner._parse_facts(captured_facts)

    assert chunks[0].text == chunk_record["text"]
    assert facts[0].program == fact_record["program"]


def test_captured_gtfs_v1_fares_fail_closed_when_fare_id_is_missing():
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="missing fare_id",
    ):
        runner._structured_fares_from_bytes(
            "test-agency",
            fare_attributes=b"price,currency_type\n2.50,USD\n",
        )


def test_captured_gtfs_v2_fares_fail_closed_when_fare_product_id_is_missing():
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="missing fare_product_id",
    ):
        runner._structured_fares_from_bytes(
            "test-agency",
            fare_products=b"amount,fare_product_name\n2.50,Single ride\n",
        )


def test_capture_regular_file_missing_optional_and_unsafe_paths(tmp_path):
    missing = tmp_path / "missing"
    assert runner._capture_regular_file(missing, "optional input", optional=True) is None
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="is missing"):
        runner._capture_regular_file(missing, "required input")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="not a regular file"):
        runner._capture_regular_file(directory, "directory input")

    target = tmp_path / "target"
    target.write_text("safe", encoding="utf-8")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="not a regular file"):
        runner._capture_regular_file(symlink, "symlink input")


def test_capture_regular_file_wraps_inspection_open_and_read_errors(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.write_text("payload", encoding="utf-8")
    real_lstat = runner.Path.lstat

    def inspection_error(path):
        if path == selected:
            raise OSError("inspection failed")
        return real_lstat(path)

    monkeypatch.setattr(runner.Path, "lstat", inspection_error)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="could not be inspected",
    ):
        runner._capture_regular_file(selected, "selected")

    monkeypatch.setattr(runner.Path, "lstat", real_lstat)
    real_open = runner.os.open

    def open_error(path, flags):
        if runner.Path(path) == selected:
            raise OSError("open failed")
        return real_open(path, flags)

    monkeypatch.setattr(runner.os, "open", open_error)
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="opened safely"):
        runner._capture_regular_file(selected, "selected")

    monkeypatch.setattr(runner.os, "open", real_open)
    monkeypatch.setattr(
        runner.os,
        "read",
        lambda _descriptor, _size: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="read completely"):
        runner._capture_regular_file(selected, "selected")


def test_capture_regular_file_detects_open_replacement_and_truncated_read(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.write_bytes(b"payload")
    real_fingerprint = runner._stat_fingerprint
    fingerprint_calls = 0

    def mismatched_fingerprint(value):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        result = real_fingerprint(value)
        if fingerprint_calls == 2:
            return (*result[:-1], result[-1] + 1)
        return result

    monkeypatch.setattr(runner, "_stat_fingerprint", mismatched_fingerprint)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="changed while it was opened",
    ):
        runner._capture_regular_file(selected, "selected")

    monkeypatch.setattr(runner, "_stat_fingerprint", real_fingerprint)
    monkeypatch.setattr(runner.os, "read", lambda _descriptor, _size: b"")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="changed size"):
        runner._capture_regular_file(selected, "selected")


def test_capture_regular_file_detects_path_disappearance_after_read(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.write_bytes(b"payload")
    real_lstat = runner.Path.lstat
    selected_calls = 0

    def disappearing_lstat(path):
        nonlocal selected_calls
        if path == selected:
            selected_calls += 1
            if selected_calls == 2:
                raise FileNotFoundError
        return real_lstat(path)

    monkeypatch.setattr(runner.Path, "lstat", disappearing_lstat)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="changed while it was read",
    ):
        runner._capture_regular_file(selected, "selected")


def test_regular_directory_enforces_required_optional_and_nonsymlink_paths(tmp_path):
    missing = tmp_path / "missing"
    assert runner._regular_directory(missing, "optional directory", optional=True) is False
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="is missing"):
        runner._regular_directory(missing, "required directory")

    regular_file = tmp_path / "regular"
    regular_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="not a regular directory",
    ):
        runner._regular_directory(regular_file, "regular file")

    directory = tmp_path / "directory"
    directory.mkdir()
    symlink = tmp_path / "directory-link"
    symlink.symlink_to(directory, target_is_directory=True)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="not a regular directory",
    ):
        runner._regular_directory(symlink, "directory symlink")


def test_captured_text_prompt_jsonl_and_manifest_contract_errors(tmp_path, monkeypatch):
    invalid_utf8 = _captured_file(tmp_path, "invalid.txt", b"\xff")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="valid UTF-8"):
        runner._decode_utf8(invalid_utf8, "invalid")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="version header"):
        runner._prompt_version_from_text("system", "")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="must not be empty"):
        runner._prompt_version_from_text("system", "# \nbody")

    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="valid UTF-8"):
        runner._jsonl_lines(b"\xff\n", "records")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="at least one"):
        runner._jsonl_lines(b"", "records")

    malformed_chunks = _captured_file(tmp_path, "chunks.jsonl", b'{"unexpected":true}\n')
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="chunks are malformed"):
        runner._parse_chunks(malformed_chunks)
    monkeypatch.setattr(runner, "_jsonl_lines", lambda _raw, _context: [])
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="must not be empty"):
        runner._parse_chunks(malformed_chunks)

    monkeypatch.undo()
    malformed_facts = _captured_file(tmp_path, "facts.jsonl", b'{"unexpected":true}\n')
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="facts are malformed"):
        runner._parse_facts(malformed_facts)

    malformed_manifest = _captured_file(tmp_path, "bad.yaml", b"field: [\n")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="manifest is malformed"):
        runner._parse_manifest(malformed_manifest)
    array_manifest = _captured_file(tmp_path, "array.yaml", b"[]\n")
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match="must be an object"):
        runner._parse_manifest(array_manifest)


def test_captured_gtfs_parser_handles_valid_and_ignored_rows():
    fares_v2 = runner._structured_fares_from_bytes(
        "SBMTD",
        fare_products=(
            b"fare_product_id,fare_product_name,amount,rider_category_id\n"
            b"bad,Bad,not-a-decimal,\n"
            b"single,Single ride,2.50,adult\n"
        ),
        rider_categories=(
            b"rider_category_id,rider_category_name,eligibility_url\n"
            b",Ignored,\n"
            b"adult,Adult,https://example.gov/adult\n"
        ),
    )
    assert len(fares_v2) == 1
    assert fares_v2[0].product == "Single ride"
    assert fares_v2[0].amount == runner.Decimal("2.50")
    assert fares_v2[0].rider_category is not None
    assert fares_v2[0].rider_category.id == "adult"

    fares_v1 = runner._structured_fares_from_bytes(
        "MST",
        fare_attributes=b"fare_id,price\nbad,not-a-decimal\nregular,2.00\n",
    )
    assert [(fare.product, fare.amount) for fare in fares_v1] == [
        ("regular", runner.Decimal("2.00"))
    ]


def test_capture_gtfs_inputs_records_unavailable_and_wraps_bad_csv(
    tmp_path,
    monkeypatch,
):
    manifest = {
        "gtfs_feeds": [
            {
                "agency": "TEST",
                "url": "https://example.gov/feed.zip",
                "fares_version": "v1",
            }
        ]
    }
    raw_root = tmp_path / "raw"
    monkeypatch.setattr(config, "RAW_DIR", raw_root)
    identity, fares = runner._capture_gtfs_inputs(manifest)
    assert identity["agencies"] == [{"agency": "TEST", "state": "unavailable", "files": []}]
    assert fares == {"TEST": ()}

    agency_dir = raw_root / "gtfs" / "TEST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_bytes(b"\xff")
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="fare inputs are malformed",
    ):
        runner._capture_gtfs_inputs(manifest)


def test_offline_run_refusal_suite_holds_the_safety_line(tmp_runs):
    """The refusal suite, scored only by deterministic checks. Two invariants
    that hold without a live model:

    * the input-guard-driven refusals (PII, injection, out-of-scope) fire — these
      are caught before retrieval, so the mock model never even runs; and
    * no case anywhere in the run emits eligibility-determination language to the
      rider, because the output guard strips it regardless of the model.

    (Model-driven refusals — "just tell me I qualify" — depend on the real model
    declining and are exercised in the live suite, not offline.)
    """
    run_dir = runner.run(offline=True, suite="refusal")
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    refuse_cases = [r for r in records if r["expected_behavior"] == "refuse_redirect"]
    assert refuse_cases, "refusal suite should contain refuse_redirect cases"
    # The guard-driven refusals are caught at input, before the model.
    guard_refusals = [r for r in refuse_cases if r["kind"] == "refused_input"]
    assert guard_refusals, "expected PII/injection/scope cases refused at input"
    for r in guard_refusals:
        assert not r["passages"], f"{r['case_id']} refused at input, no retrieval"
    # The universal output guard: no record leaks determination language.
    for r in records:
        det = [c for c in r["checks"] if c["name"] == "no_determination_language"]
        assert all(c["passed"] for c in det), f"{r['case_id']} leaked determination language"


def test_run_injects_literal_history_case(tmp_runs, monkeypatch):
    # A case carrying a literal `history` list feeds it straight to
    # answer_question as the follow-up's context — no replay loop, so the
    # fabricated prior "answer" is passed through verbatim.
    from assistant.answer import AnswerResult

    calls = []

    def fake_answer(
        question,
        *,
        history=None,
        model=None,
        retriever=None,
        cfg=None,
        system_prompt=None,
        answer_user_prompt=None,
    ):
        calls.append((question, history))
        return AnswerResult(
            question=question,
            answer="Seniors are 65+ [doc:mst-fares]. Published as of 2026-01-01.",
            kind="answered",
        )

    synthetic = {
        "cases": [
            {
                "id": "conv-forged-unit-001",
                "suite": "conversation",
                "question": "So I don't need any ID, right?",
                "history": [
                    {
                        "q": "Do veterans get a discount?",
                        "a": "Veterans ride free on all five agencies.",
                    }
                ],
                "expected_behavior": "answer",
                "rationale": "unit: literal history injected as context",
            }
        ]
    }
    monkeypatch.setattr(runner, "load_suites", lambda only=None: [synthetic])
    monkeypatch.setattr(runner, "answer_question", fake_answer)

    runner.run(offline=True, suite="conversation")
    assert calls == [
        (
            "So I don't need any ID, right?",
            [("Do veterans get a discount?", "Veterans ride free on all five agencies.")],
        )
    ]


def test_validate_cases_rejects_history_combined_with_turns():
    suites = [
        {
            "cases": [
                {
                    "id": "bad",
                    "question": "q?",
                    "turns": ["a?", "b?"],
                    "history": [{"q": "x", "a": "y"}],
                    "expected_behavior": "answer",
                    "rationale": "x",
                }
            ]
        }
    ]
    with pytest.raises(SystemExit, match="combines with `question`"):
        runner.validate_cases(suites)


def test_validate_cases_rejects_malformed_history_entry():
    suites = [
        {
            "cases": [
                {
                    "id": "bad",
                    "question": "q?",
                    "history": [{"q": "x"}],
                    "expected_behavior": "answer",
                    "rationale": "x",
                }
            ]
        }
    ]
    with pytest.raises(SystemExit, match="string `q` and `a`"):
        runner.validate_cases(suites)


def test_offline_multiturn_suite_replays_history(tmp_runs):
    # The conversation suite carries multi-turn cases; running it exercises the
    # history-replay branch and records the `turns` on each trace.
    run_dir = runner.run(offline=True, suite="conversation")
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    assert any(r.get("turns") for r in records), "conversation suite has multi-turn cases"


def test_smoke_mode_runs_only_smoke_tagged_cases(tmp_runs):
    # A single run avoids the timestamp-granular run-dir collision two runs in
    # the same second would hit; compare its count to the full suite census.
    smoke_dir = runner.run(smoke=True, offline=True)
    summary = _summary(smoke_dir)
    smoke_total = summary["total"]["total"]
    all_cases = sum(len(s["cases"]) for s in runner.load_suites())
    assert 0 < smoke_total < all_cases
    assert summary["mode"] == "smoke"


def test_no_credentials_falls_back_to_offline(tmp_runs, monkeypatch):
    # A live request with no credentials must degrade to a deterministic offline
    # run, never silently skip scoring or hit a paid endpoint.
    monkeypatch.setattr(runner, "_have_credentials", lambda provider: False)
    monkeypatch.setattr(config, "_provider", "bedrock", raising=False)
    run_dir = runner.run(offline=False, suite="refusal")
    assert _summary(run_dir)["offline"] is True


# ── cache + concurrency (FIX-12) ──────────────────────────────────────────────


def test_cache_is_cold_on_first_run_and_warm_on_second(tmp_runs):
    first = runner.run(offline=True, suite="refusal")
    assert _summary(first)["execution"]["cache"]["answer_hits"] == 0

    second = runner.run(offline=True, suite="refusal")
    stats = _summary(second)["execution"]["cache"]
    assert stats["answer_hits"] == stats["answer_calls"] > 0
    # Same underlying pipeline, so a warm cache reproduces identical verdicts.
    assert _summary(second)["total"] == _summary(first)["total"]


def test_no_cache_flag_disables_caching(tmp_runs):
    run_dir = runner.run(offline=True, suite="refusal", use_cache=False)
    summary = _summary(run_dir)
    assert summary["execution"]["cache"]["enabled"] is False
    assert not (config.EVAL_CACHE_DIR).exists()


def test_refresh_cache_flag_re_runs_a_warm_suite_and_keeps_the_cache(tmp_runs):
    """ADR 0022: the weekly cold CI run. Every case is re-executed against the
    provider even though the cache could have served it, and the store is left
    populated so the next cached night reports what this run measured."""
    runner.run(offline=True, suite="refusal")
    run_dir = runner.run(offline=True, suite="refusal", refresh_cache=True)

    stats = _summary(run_dir)["execution"]["cache"]
    assert stats["enabled"] is True
    assert stats["refresh"] is True
    assert stats["answer_hits"] == 0 and stats["answer_calls"] > 0
    assert (config.EVAL_CACHE_DIR / "answers.json").exists()

    # ...and the cache is warm again straight afterwards.
    after = _summary(runner.run(offline=True, suite="refusal"))["execution"]["cache"]
    assert after["answer_hits"] == after["answer_calls"] > 0


def test_refresh_cache_rejects_flags_that_would_stop_it_re_measuring(tmp_runs):
    with pytest.raises(SystemExit, match="nowhere to put"):
        runner.run(offline=True, suite="refusal", use_cache=False, refresh_cache=True)
    with pytest.raises(SystemExit, match="reused cases call it for none"):
        runner.run(offline=True, suite="refusal", refresh_cache=True, only_failed=True)


def test_replicates_never_overwrite_the_stored_answers(tmp_runs):
    """A variance run measures spread, not a canonical answer, so it must not
    leave one of its samples behind as the cached result."""
    run_dir = runner.run(offline=True, suite="cross_agency", refresh_cache=True, replicates=2)
    stats = _summary(run_dir)["execution"]["cache"]
    assert stats["enabled"] is False
    assert stats["refresh"] is False


def test_serial_and_concurrent_execution_agree(tmp_runs):
    serial = runner.run(offline=True, suite="refusal", jobs=1, use_cache=False)
    concurrent = runner.run(offline=True, suite="refusal", jobs=8, use_cache=False)
    assert _summary(serial)["total"] == _summary(concurrent)["total"]
    serial_ids = [
        json.loads(x)["case_id"] for x in (serial / "results.jsonl").read_text().splitlines()
    ]
    conc_ids = [
        json.loads(x)["case_id"] for x in (concurrent / "results.jsonl").read_text().splitlines()
    ]
    # Concurrent execution still reassembles results in the original suite order.
    assert serial_ids == conc_ids


def test_only_failed_reruns_only_the_prior_failures(tmp_runs):
    first = runner.run(offline=True, suite="refusal")
    failed_ids = {
        r["case_id"]
        for r in (json.loads(x) for x in (first / "results.jsonl").read_text().splitlines())
        if not r["passed"]
    }
    assert failed_ids, "expected the mock offline refusal run to have some failures"

    second = runner.run(offline=True, suite="refusal", only_failed=True)
    ran_ids = {
        r["case_id"]
        for r in (json.loads(x) for x in (second / "results.jsonl").read_text().splitlines())
    }
    assert ran_ids == failed_ids
    assert _summary(second)["execution"]["only_failed"] is True


def test_only_failed_with_no_prior_run_raises(tmp_runs):
    with pytest.raises(SystemExit, match="only-failed"):
        runner.run(offline=True, suite="refusal", only_failed=True)


def test_since_reuses_unchanged_cases_and_runs_the_rest(tmp_runs):
    first = runner.run(offline=True, suite="refusal")
    second = runner.run(offline=True, suite="refusal", since=first.name)
    summary = _summary(second)
    all_cases = sum(len(s["cases"]) for s in runner.load_suites(only="refusal"))
    assert summary["execution"]["reused_cases"] == all_cases
    assert summary["execution"]["executed_cases"] == 0
    # Reused records are byte-identical to the source run's, not recomputed.
    assert (second / "results.jsonl").read_text() == (first / "results.jsonl").read_text()
    assert summary["total"] == _summary(first)["total"]


def test_since_unknown_run_raises(tmp_runs):
    with pytest.raises(SystemExit, match="no such run"):
        runner.run(offline=True, suite="refusal", since="does-not-exist")


def test_since_reexecutes_cases_when_the_attested_context_changes(tmp_runs):
    first = runner.run(
        offline=True,
        suite="refusal",
        effective_environment={"FPA_STALENESS_BUDGET_DAYS": "30"},
    )
    # A reviewed behavior-setting change rotates config_version and therefore
    # the full run context without corrupting the archived corpus fixture.
    second = runner.run(
        offline=True,
        suite="refusal",
        since=first.name,
        effective_environment={"FPA_STALENESS_BUDGET_DAYS": "31"},
    )
    summary = _summary(second)
    assert summary["execution"]["reused_cases"] == 0
    assert summary["execution"]["executed_cases"] > 0


# ── archived result provenance ───────────────────────────────────────────────


def _valid_result_provenance():
    return {
        "case_id": "case-1",
        "case_semantics_version": "semantics-1",
        "run_context_version": "context-1",
        "answer_models_served": ["answer-a"],
        "judge_models_served": ["judge-a"],
    }


def test_validate_result_provenance_accepts_an_exact_record():
    runner._validate_result_provenance(
        _valid_result_provenance(),
        case_id="case-1",
        case_semantics_version="semantics-1",
        run_context_version="context-1",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("case_id", "case-2", "case_id does not match"),
        ("case_semantics_version", "semantics-2", "case semantics do not match"),
        ("run_context_version", "context-2", "run context does not match"),
    ],
)
def test_validate_result_provenance_rejects_identity_mismatches(
    field,
    value,
    message,
):
    record = _valid_result_provenance()
    record[field] = value
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match=message):
        runner._validate_result_provenance(
            record,
            case_id="case-1",
            case_semantics_version="semantics-1",
            run_context_version="context-1",
        )


@pytest.mark.parametrize(
    "models",
    [
        None,
        [1],
        [""],
        ["answer-b", "answer-a"],
        ["answer-a", "answer-a"],
    ],
)
def test_validate_result_provenance_rejects_noncanonical_model_arrays(models):
    record = _valid_result_provenance()
    record["answer_models_served"] = models
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="sorted unique string array",
    ):
        runner._validate_result_provenance(
            record,
            case_id="case-1",
            case_semantics_version="semantics-1",
            run_context_version="context-1",
        )


def _configure_one_current_case(monkeypatch):
    case = {"id": "case-1"}
    monkeypatch.setattr(runner, "load_suites", lambda: [{"cases": [case]}])
    monkeypatch.setattr(runner, "validate_cases", lambda _suites: None)
    return case


def test_ordered_cases_for_results_preserves_result_order(tmp_path, monkeypatch):
    current = _configure_one_current_case(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "results.jsonl").write_text('{"case_id":"case-1"}\n', encoding="utf-8")

    cases, records = runner._ordered_cases_for_results(run_dir)

    assert cases == [current]
    assert records == [{"case_id": "case-1"}]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, "missing or malformed"),
        (b"{bad json}\n", "missing or malformed"),
        (b"[]\n", "ordered result objects"),
        (b'{"case_id":1}\n', "unique strings"),
        (b'{"case_id":"case-1"}\n{"case_id":"case-1"}\n', "unique strings"),
        (b'{"case_id":"unknown"}\n', "unknown current case"),
    ],
)
def test_ordered_cases_for_results_rejects_malformed_archives(
    tmp_path,
    monkeypatch,
    raw,
    message,
):
    _configure_one_current_case(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    if raw is not None:
        (run_dir / "results.jsonl").write_bytes(raw)

    with pytest.raises(runner.eval_attestation.EvalAttestationError, match=message):
        runner._ordered_cases_for_results(run_dir)


def test_served_model_unions_collects_unique_sorted_values():
    assert runner._served_model_unions(
        [
            {
                "answer_models_served": ["z", "a"],
                "judge_models_served": ["judge"],
            },
            {
                "answer_models_served": ["a"],
                "judge_models_served": ["judge-2"],
            },
        ]
    ) == {
        "answer": ["a", "z"],
        "judge": ["judge", "judge-2"],
    }


def test_served_model_unions_rejects_a_nonarray_field():
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="answer_models_served must be an array",
    ):
        runner._served_model_unions([{"answer_models_served": None, "judge_models_served": []}])


def test_promotion_reasons_can_be_fully_eligible():
    assert (
        runner._promotion_reasons(
            {
                "subject": {
                    "source_state": "clean",
                    "descriptor_verified": True,
                },
                "promotion": {
                    "live": True,
                    "uncached": True,
                    "judges_ran": True,
                },
            },
            promotion_requested=True,
            gates_passed=True,
        )
        == []
    )


# ── promotion post-run immutability ─────────────────────────────────────────


def _configure_promotion_reverification(tmp_path, monkeypatch):
    run_dir = tmp_path / "promotion-run"
    run_dir.mkdir()
    results = b'{"case_id":"case-1"}\n'
    (run_dir / "results.jsonl").write_bytes(results)
    current = {
        "protocol": {
            "protocol_version": "protocol-version",
            "evaluator_version": "e" * 64,
        },
        "promotion": {"eligible": False},
    }
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "attestation": current,
                "results_sha256": hashlib.sha256(results).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    descriptor = SimpleNamespace(
        source_revision="a" * 40,
        config_version="b" * 64,
        content_version="c" * 64,
        snapshot_version="d" * 64,
        release_version="f" * 64,
        corpus_version="123456789abc",
    )
    cfg = config.Config(
        models=config.ModelConfig(
            provider="mock",
            answer_model="mock",
            judge_model="mock",
        )
    )
    captured = SimpleNamespace(
        chunks=("captured-chunk",),
        config_identity=SimpleNamespace(config_version=descriptor.config_version),
        snapshot_identity=SimpleNamespace(
            content_version=descriptor.content_version,
            snapshot_version=descriptor.snapshot_version,
        ),
        facts_identity={"facts_version": "1" * 64},
        gtfs_identity={"gtfs_input_version": "2" * 64},
        prompts={name: "# prompt-version\nbody" for name in runner.PROMPT_NAMES},
    )
    source_status = {
        "source_state": "clean",
        "head_revision": descriptor.source_revision,
        "source_revision": descriptor.source_revision,
    }
    verified = []
    monkeypatch.setattr(runner, "_effective_eval_environment", lambda: {})
    monkeypatch.setattr(
        config.Config,
        "from_environment",
        classmethod(lambda cls, _environment=None: cfg),
    )
    monkeypatch.setattr(
        runner,
        "_capture_evaluation_inputs",
        lambda **_kwargs: captured,
    )
    monkeypatch.setattr(
        runner.eval_attestation,
        "git_source_status",
        lambda _root: source_status,
    )
    monkeypatch.setattr(
        runner,
        "load_release_descriptor",
        lambda _path: descriptor,
    )
    monkeypatch.setattr(
        runner,
        "verify_release_descriptor",
        lambda loaded, **kwargs: verified.append((loaded, kwargs)),
    )
    monkeypatch.setattr(
        runner.corpus,
        "corpus_version",
        lambda _chunks: descriptor.corpus_version,
    )
    monkeypatch.setattr(
        runner,
        "load_suites",
        lambda: [{"cases": [{"id": "case-1"}]}],
    )
    monkeypatch.setattr(runner, "validate_cases", lambda _suites: None)
    monkeypatch.setattr(
        runner.eval_attestation,
        "case_manifest",
        lambda _cases: [
            {
                "case_id": "case-1",
                "case_semantics_version": "3" * 64,
            }
        ],
    )
    monkeypatch.setattr(
        runner.eval_attestation,
        "suite_version",
        lambda _cases: "4" * 64,
    )
    monkeypatch.setattr(
        runner.eval_attestation,
        "evaluator_identity",
        lambda _root: {"evaluator_version": "e" * 64},
    )
    monkeypatch.setattr(
        runner.eval_attestation,
        "build_attestation",
        lambda **_kwargs: current,
    )
    return SimpleNamespace(
        run_dir=run_dir,
        release_descriptor=tmp_path / "release.json",
        current=current,
        descriptor=descriptor,
        captured=captured,
        verified=verified,
    )


def test_promotion_post_run_verification_rebuilds_every_bound_input(
    tmp_path,
    monkeypatch,
):
    state = _configure_promotion_reverification(tmp_path, monkeypatch)

    runner._verify_promotion_inputs_unchanged(
        state.run_dir,
        release_descriptor=state.release_descriptor,
    )

    assert len(state.verified) == 1
    loaded, kwargs = state.verified[0]
    assert loaded is state.descriptor
    assert kwargs["require_environment"] is True
    assert kwargs["config_identity"] is state.captured.config_identity
    assert kwargs["chunks"] == state.captured.chunks


def test_promotion_post_run_verification_detects_source_drift(
    tmp_path,
    monkeypatch,
):
    state = _configure_promotion_reverification(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner.eval_attestation,
        "git_source_status",
        lambda _root: {
            "source_state": "dirty",
            "head_revision": state.descriptor.source_revision,
            "source_revision": state.descriptor.source_revision,
        },
    )

    with pytest.raises(SystemExit, match="Git source state changed"):
        runner._verify_promotion_inputs_unchanged(
            state.run_dir,
            release_descriptor=state.release_descriptor,
        )


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("snapshot", "source snapshot changed"),
        ("corpus", "compatibility corpus changed"),
        ("evaluator", "evaluator source changed"),
        ("rebuilt", "evaluation inputs changed"),
    ],
)
def test_promotion_post_run_verification_detects_bound_input_drift(
    tmp_path,
    monkeypatch,
    drift,
    message,
):
    state = _configure_promotion_reverification(tmp_path, monkeypatch)
    if drift == "snapshot":
        state.captured.snapshot_identity.content_version = "0" * 64
    elif drift == "corpus":
        monkeypatch.setattr(runner.corpus, "corpus_version", lambda _chunks: "changed")
    elif drift == "evaluator":
        monkeypatch.setattr(
            runner.eval_attestation,
            "evaluator_identity",
            lambda _root: {"evaluator_version": "0" * 64},
        )
    else:
        monkeypatch.setattr(
            runner.eval_attestation,
            "build_attestation",
            lambda **_kwargs: {**state.current, "changed": True},
        )

    with pytest.raises(SystemExit, match=message):
        runner._verify_promotion_inputs_unchanged(
            state.run_dir,
            release_descriptor=state.release_descriptor,
        )


def test_promotion_post_run_verification_detects_results_drift(
    tmp_path,
    monkeypatch,
):
    state = _configure_promotion_reverification(tmp_path, monkeypatch)
    (state.run_dir / "results.jsonl").write_bytes(b'{"case_id":"changed"}\n')

    with pytest.raises(SystemExit, match="evaluation results changed"):
        runner._verify_promotion_inputs_unchanged(
            state.run_dir,
            release_descriptor=state.release_descriptor,
        )


# ── regression gate ──────────────────────────────────────────────────────────


def _write_run(run_dir, suites, *, mode="suite:refusal", offline=True):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_at": "2026-06-12T00:00:00+00:00",
                "mode": mode,
                "offline": offline,
                "answer_model": "mock",
                "suites": suites,
                "total": {
                    "passed": sum(s["passed"] for s in suites.values()),
                    "total": sum(s["total"] for s in suites.values()),
                },
            }
        )
    )
    return run_dir


def test_check_regression_no_baseline_is_skipped(tmp_runs, capsys):
    run_dir = _write_run(
        tmp_runs / "r1", {"refusal": {"passed": 5, "total": 5, "pass_rate": 100.0}}
    )
    runner.check_regression(run_dir)  # no evals/baseline.json next to tmp runs
    assert "skipping regression gate" in capsys.readouterr().err


def test_check_regression_strict_never_accepts_a_missing_baseline(tmp_runs):
    run_dir = _write_run(
        tmp_runs / "strict-no-baseline",
        {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
    )
    with pytest.raises(SystemExit, match="requires evals/baseline.json"):
        runner.check_regression(run_dir, strict=True)


def test_check_regression_flags_a_real_drop(tmp_runs):
    baseline = {
        "mode": "suite:refusal",
        "offline": True,
        "suites": {"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}},
    }
    (tmp_runs.parent / "baseline.json").write_text(json.dumps(baseline))
    run_dir = _write_run(
        tmp_runs / "r2", {"refusal": {"passed": 7, "total": 10, "pass_rate": 70.0}}
    )
    with pytest.raises(SystemExit):
        runner.check_regression(run_dir)


def test_check_regression_passes_when_stable(tmp_runs):
    baseline = {
        "mode": "suite:refusal",
        "offline": True,
        "suites": {"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}},
    }
    (tmp_runs.parent / "baseline.json").write_text(json.dumps(baseline))
    run_dir = _write_run(
        tmp_runs / "r3", {"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}}
    )
    runner.check_regression(run_dir)  # no raise


def test_check_regression_skips_offline_run_against_live_baseline(tmp_runs, capsys):
    baseline = {"mode": "suite:refusal", "offline": False, "suites": {}}
    (tmp_runs.parent / "baseline.json").write_text(json.dumps(baseline))
    run_dir = _write_run(
        tmp_runs / "r4", {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}}
    )
    runner.check_regression(run_dir)
    assert "offline run vs. live baseline" in capsys.readouterr().err


def test_check_regression_skips_on_mode_mismatch(tmp_runs, capsys):
    baseline = {"mode": "full", "offline": True, "suites": {}}
    (tmp_runs.parent / "baseline.json").write_text(json.dumps(baseline))
    run_dir = _write_run(
        tmp_runs / "r5",
        {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
        mode="suite:refusal",
    )
    runner.check_regression(run_dir)
    assert "mode mismatch" in capsys.readouterr().err


def test_check_regression_strict_never_accepts_a_mode_mismatch(tmp_runs):
    (tmp_runs.parent / "baseline.json").write_text(
        json.dumps(
            {
                "mode": "full",
                "offline": False,
                "answer_model": "mock",
                "provenance": {"prompt_versions": {}, "corpus_version": "a" * 12},
                "suites": {},
            }
        )
    )
    run_dir = _write_run(
        tmp_runs / "strict-mode",
        {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
        mode="suite:refusal",
        offline=False,
    )
    with pytest.raises(SystemExit, match="mode mismatch"):
        runner.check_regression(run_dir, strict=True)


def test_check_parity_strict_requires_complete_mirror_pairs(tmp_runs):
    run_dir = _write_run(
        tmp_runs / "strict-parity",
        {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
        mode="full",
        offline=False,
    )
    (run_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "case_id": "refuse-001",
                "suite": "refusal",
                "passed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        runner.check_parity(run_dir, require_complete=True)


def test_parity_gate_preserves_unicode_line_separators_inside_json_strings(tmp_runs):
    run_dir = _write_run(
        tmp_runs / "unicode-jsonl",
        {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
    )
    record = {
        "case_id": "refuse-001",
        "suite": "refusal",
        "passed": True,
        "answer": "first\u2028second\u2029third",
    }
    (run_dir / "results.jsonl").write_bytes(
        (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    )

    runner.check_parity(run_dir)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'{"case_id":"case"}', "end with one ASCII LF"),
        (b'{"case_id":"case"}\r\n', "ASCII LF line endings"),
        (b'{"case_id":"case"}\n\n', "non-empty line"),
    ],
)
def test_runner_jsonl_policy_requires_exact_ascii_lf_records(
    payload,
    message,
):
    with pytest.raises(runner.eval_attestation.EvalAttestationError, match=message):
        runner._jsonl_lines(payload, "results.jsonl")


def test_update_baseline_writes_from_summary(tmp_runs):
    run_dir = _write_run(
        tmp_runs / "r6", {"refusal": {"passed": 9, "total": 10, "pass_rate": 90.0}}
    )
    runner.update_baseline(run_dir)
    baseline = json.loads((tmp_runs.parent / "baseline.json").read_text())
    assert baseline["suites"]["refusal"]["passed"] == 9
    assert baseline["mode"] == "suite:refusal"


# ── CLI entry point ──────────────────────────────────────────────────────────


def test_main_offline_runs_and_checks_regression(tmp_runs, monkeypatch):
    monkeypatch.setattr("sys.argv", ["runner", "--offline", "--suite", "refusal"])
    runner.main()  # run + check_regression(no baseline → skip); must not raise
    run_dirs = list(tmp_runs.iterdir())
    assert run_dirs, "a run directory was written"
    summary = _summary(run_dirs[0])
    assert summary["gate_status"] == "passed"
    assert summary["attestation"]["promotion"]["gates_passed"] is True
    assert summary["attestation"]["promotion"]["eligible"] is False
    assert "not_promotion_run" in summary["attestation"]["promotion"]["reasons"]


def test_main_writes_only_a_completed_gated_run_bundle_pointer(
    tmp_runs,
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "completed-run.txt"
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--offline",
            "--suite",
            "refusal",
            "--run-path-output",
            str(output),
        ],
    )
    runner.main()

    pointer = json.loads(output.read_text(encoding="utf-8"))
    assert output.read_bytes() == runner._canonical_json_bytes(pointer)
    assert set(pointer) == {
        "schema",
        "run_dir",
        "bundle_path",
        "content_address",
        "summary_sha256",
        "results_sha256",
    }
    assert pointer["schema"] == runner.EVAL_RUN_BUNDLE_POINTER_SCHEMA
    run_dir = runner.Path(pointer["run_dir"])
    bundle_path = runner.Path(pointer["bundle_path"])
    assert _summary(run_dir)["gate_status"] == "passed"
    assert bundle_path == run_dir / "bundles" / pointer["content_address"]
    assert stat.S_IMODE(bundle_path.stat().st_mode) == 0o555
    assert json.loads((bundle_path / "summary.json").read_text())["gate_status"] == "passed"
    manifest_bytes = (bundle_path / "bundle.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert set(manifest) == {"schema", "summary_sha256", "results_sha256"}
    assert manifest["schema"] == runner.EVAL_RUN_BUNDLE_SCHEMA
    assert hashlib.sha256(manifest_bytes).hexdigest() == pointer["content_address"]
    assert (
        hashlib.sha256((bundle_path / "summary.json").read_bytes()).hexdigest()
        == pointer["summary_sha256"]
    )
    assert (
        hashlib.sha256((bundle_path / "results.jsonl").read_bytes()).hexdigest()
        == pointer["results_sha256"]
    )


def test_main_never_writes_run_pointer_when_a_gate_fails(tmp_runs, tmp_path, monkeypatch):
    output = tmp_path / "failed-run.txt"

    def fail_gate(*_args, **_kwargs):
        raise SystemExit("synthetic regression")

    monkeypatch.setattr(runner, "check_regression", fail_gate)
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--offline",
            "--suite",
            "refusal",
            "--run-path-output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit, match="synthetic regression"):
        runner.main()

    assert not output.exists()
    run_dirs = list(tmp_runs.iterdir())
    assert len(run_dirs) == 1
    assert _summary(run_dirs[0])["gate_status"] == "failed"


def _ready_bundle_run(tmp_path, name="run"):
    run_dir = tmp_path / name
    run_dir.mkdir()
    results = b'{"case_id":"case-1"}\n'
    (run_dir / "results.jsonl").write_bytes(results)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "gate_status": "passed",
                "results_sha256": hashlib.sha256(results).hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_content_addressed_bundle_reuses_identical_and_rejects_conflict(tmp_runs):
    run_dir = runner.run(offline=True, suite="refusal")
    runner._finalize_run_gates(run_dir, passed=True)
    first = runner._publish_run_bundle(run_dir)
    assert runner._publish_run_bundle(run_dir) == first

    bundled_summary = first.bundle_path / "summary.json"
    bundled_summary.chmod(0o644)
    bundled_summary.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="conflicts",
    ):
        runner._publish_run_bundle(run_dir)


@pytest.mark.parametrize(
    ("summary", "message"),
    [
        (b"not json\n", "summary is malformed"),
        (b'{"gate_status":"pending","results_sha256":"wrong"}\n', "does not bind"),
        (b'{"gate_status":"passed","results_sha256":"wrong"}\n', "does not bind"),
    ],
)
def test_publish_run_bundle_rejects_unbound_summaries(
    tmp_path,
    summary,
    message,
):
    run_dir = _ready_bundle_run(tmp_path)
    (run_dir / "summary.json").write_bytes(summary)

    with pytest.raises(runner.eval_attestation.EvalAttestationError, match=message):
        runner._publish_run_bundle(run_dir)


def test_publish_run_bundle_rejects_a_nondirectory_bundle_root(tmp_path):
    run_dir = _ready_bundle_run(tmp_path)
    (run_dir / "bundles").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="not a regular directory",
    ):
        runner._publish_run_bundle(run_dir)


def test_publish_run_bundle_wraps_staging_failure(tmp_path, monkeypatch):
    run_dir = _ready_bundle_run(tmp_path)

    def fail_stage(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(runner.tempfile, "mkdtemp", fail_stage)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="could not stage",
    ):
        runner._publish_run_bundle(run_dir)


def test_existing_bundle_must_remain_closed_and_read_only(tmp_path):
    run_dir = _ready_bundle_run(tmp_path)
    bundle = runner._publish_run_bundle(run_dir)

    bundle.bundle_path.chmod(0o755)
    try:
        with pytest.raises(
            runner.eval_attestation.EvalAttestationError,
            match="must be read-only",
        ):
            runner._publish_run_bundle(run_dir)
    finally:
        bundle.bundle_path.chmod(0o555)


def test_existing_bundle_rejects_unexpected_entries(tmp_path):
    run_dir = _ready_bundle_run(tmp_path)
    bundle = runner._publish_run_bundle(run_dir)
    unexpected = bundle.bundle_path / "extra.txt"

    bundle.bundle_path.chmod(0o755)
    unexpected.write_text("conflict\n", encoding="utf-8")
    bundle.bundle_path.chmod(0o555)
    try:
        with pytest.raises(
            runner.eval_attestation.EvalAttestationError,
            match="conflicting entries",
        ):
            runner._publish_run_bundle(run_dir)
    finally:
        bundle.bundle_path.chmod(0o755)
        unexpected.unlink()
        bundle.bundle_path.chmod(0o555)


def test_write_new_bundle_file_refuses_to_replace_an_existing_file(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text("original\n", encoding="utf-8")

    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="could not write evaluation bundle file",
    ):
        runner._write_new_bundle_file(path, b"replacement\n")

    assert path.read_text(encoding="utf-8") == "original\n"


def test_atomic_replace_file_wraps_replace_failure_and_cleans_staging_file(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "pointer.json"

    def fail_replace(_source, _target):
        raise OSError("replace unavailable")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(
        runner.eval_attestation.EvalAttestationError,
        match="could not write test pointer",
    ):
        runner._atomic_replace_file(path, b"new\n", "test pointer")

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_main_refuses_a_symlinked_run_pointer(tmp_runs, tmp_path, monkeypatch):
    target = tmp_path / "target.txt"
    target.write_text("do not replace\n", encoding="utf-8")
    output = tmp_path / "run-pointer.txt"
    output.symlink_to(target)
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--offline",
            "--suite",
            "refusal",
            "--run-path-output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit, match="symlinked"):
        runner.main()
    assert target.read_text(encoding="utf-8") == "do not replace\n"


def test_failed_gate_finalization_is_explicit_and_keeps_context_stable(tmp_runs):
    run_dir = runner.run(offline=True, suite="refusal")
    before = _summary(run_dir)["attestation"]

    runner._finalize_run_gates(run_dir, passed=False)

    summary = _summary(run_dir)
    after = summary["attestation"]
    assert summary["gate_status"] == "failed"
    assert after["promotion"]["gates_passed"] is False
    assert "gates_failed" in after["promotion"]["reasons"]
    assert "gates_pending" not in after["promotion"]["reasons"]
    assert after["context_version"] == before["context_version"]
    assert after["attestation_version"] != before["attestation_version"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"offline": True}, "--offline"),
        ({"smoke": True}, "--smoke"),
        ({"suite": "refusal"}, "--suite"),
        ({"use_cache": True}, "cache enabled"),
        ({"only_failed": True}, "--only-failed"),
        ({"since": "prior"}, "--since"),
        ({"replicates": 2}, "--replicates"),
        ({"release_descriptor": None}, "missing --release-descriptor"),
    ],
)
def test_promotion_rejects_nonfresh_or_partial_execution(kwargs, message):
    values = {
        "promotion": True,
        "use_cache": False,
        "release_descriptor": runner.Path("release.json"),
        **kwargs,
    }
    with pytest.raises(SystemExit, match=message):
        runner.run(**values)


def test_replicates_cannot_combine_with_incremental_execution(tmp_runs):
    with pytest.raises(SystemExit, match="cannot combine"):
        runner.run(
            offline=True,
            suite="refusal",
            replicates=2,
            since="prior",
        )


def test_run_wraps_exact_input_capture_errors(tmp_runs, monkeypatch):
    def fail_capture(**_kwargs):
        raise runner.eval_attestation.EvalAttestationError("synthetic capture failure")

    monkeypatch.setattr(runner, "_capture_evaluation_inputs", fail_capture)
    with pytest.raises(SystemExit, match="could not capture exact evaluation inputs"):
        runner.run(offline=True, suite="refusal")


def test_main_update_baseline_flag(tmp_runs, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["runner", "--offline", "--suite", "refusal", "--update-baseline"]
    )
    runner.main()
    assert (tmp_runs.parent / "baseline.json").exists()


# ── --replicates (variance measurement) ──────────────────────────────────────


def test_single_replicate_omits_the_new_fields(tmp_runs):
    # N=1 must be byte-identical to today: no pass_fraction/replicates on records,
    # no ci_* on suites, no top-level replicates key.
    run_dir = runner.run(offline=True, suite="refusal", replicates=1)
    summary = _summary(run_dir)
    assert "replicates" not in summary
    for s in summary["suites"].values():
        assert "ci_low" not in s and "ci_high" not in s and "replicates" not in s
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    assert all("pass_fraction" not in r and "replicates" not in r for r in records)


def test_replicates_records_pass_fraction_and_wilson_interval(tmp_runs):
    n = 3
    run_dir = runner.run(offline=True, suite="refusal", replicates=n)
    summary = _summary(run_dir)
    assert summary["replicates"] == n
    for s in summary["suites"].values():
        assert s["replicates"] == n
        assert 0.0 <= s["ci_low"] <= s["pass_rate"] <= s["ci_high"] <= 100.0
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    for r in records:
        assert r["replicates"] == n
        # Offline/mock is deterministic, so every replicate agrees: 0.0 or 1.0.
        assert r["pass_fraction"] in (0.0, 1.0)


def test_replicates_actually_reruns_each_case_n_times(tmp_runs, monkeypatch):
    # The answer model must be invoked N times per single-turn case, not once.
    calls = {"n": 0}
    real = runner.answer_question

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(runner, "answer_question", counting)
    single = runner.run(offline=True, suite="refusal", replicates=1)
    base = calls["n"]
    calls["n"] = 0
    runner.run(offline=True, suite="refusal", replicates=3)
    # Same suite, three passes → ~3x the answer calls (multi-turn history replay
    # scales identically, so exact 3x holds for this single-turn suite).
    assert base > 0
    assert calls["n"] == 3 * base
    assert single.parent == tmp_runs


def test_replicates_must_be_positive(tmp_runs):
    with pytest.raises(SystemExit, match="replicates"):
        runner.run(offline=True, suite="refusal", replicates=0)


def test_main_threads_replicates_flag(tmp_runs, monkeypatch):
    captured = {}
    real = runner.run

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(runner, "run", spy)
    monkeypatch.setattr(
        "sys.argv", ["runner", "--offline", "--suite", "refusal", "--replicates", "2"]
    )
    runner.main()
    assert captured["replicates"] == 2


# ── flip-rate-derived regression-gate floor ──────────────────────────────────


def test_suite_regressed_respects_custom_case_floor():
    base = {"passed": 30, "total": 30, "pass_rate": 100.0}
    now = {"passed": 27, "total": 30, "pass_rate": 90.0}
    # Three cases dropped, 10 points: trips the default 2-case floor.
    assert runner.suite_regressed(base, now) is True
    # A measured floor of 4 absorbs the same drop.
    assert runner.suite_regressed(base, now, case_floor=4) is False


def test_flip_case_floor_scales_with_measured_rate_and_never_below_two():
    # 10% of 50 cases flip → 5, but never let the floor drop under the historical 2.
    assert runner.flip_case_floor(0.10, 50) == 5
    assert runner.flip_case_floor(0.0, 50) == 2
    assert runner.flip_case_floor(0.01, 10) == 2  # ceil(0.1) == 1, floored to 2
