"""Closed-contract checks for the immutable agency-console deployment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_CONSOLE = REPO_ROOT / "infra" / "deploy-console.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return DEPLOY_CONSOLE.read_text(encoding="utf-8")


def _clean_script_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    infra = repo / "infra"
    infra.mkdir(parents=True)
    deployed = infra / DEPLOY_CONSOLE.name
    shutil.copy2(DEPLOY_CONSOLE, deployed)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", deployed], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            repo,
            "-c",
            "user.name=Console deploy test",
            "-c",
            "user.email=console-deploy@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "test fixture",
        ],
        check=True,
    )
    return repo


def _run_with_evidence(
    repo: Path,
    evidence: Path,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    for command in ("aws", "uv"):
        executable = fake_bin / command
        executable.write_text(
            f'#!/bin/sh\necho "unexpected {command} invocation" >&2\nexit 99\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FPA_RIDER_FUNCTION_NAME": "fare-policy-assistant-test",
        "FPA_RIDER_ALIAS": "live",
        "FPA_RIDER_BASE_URL": "https://fare.example.invalid",
        "FPA_CONSOLE_TOKEN_PARAMETER_NAME": "/fare/test/console-token",
        "FPA_PROMOTION_EVIDENCE_DIR": os.fspath(evidence),
    }
    return subprocess.run(
        ["bash", "infra/deploy-console.sh"],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _shell_function(script: str, name: str) -> str:
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start) + len("\n}\n")
    return script[start:end]


def _exercise_ambiguous_restore(
    tmp_path: Path,
    *,
    function: str,
    alias: str,
    active_variable: str,
    expected_prefix: str,
    restore_version: str,
    restore_description: str,
    applied_version: str,
    applied_description: str,
) -> dict[str, object]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    state_path = tmp_path / "aliases.json"
    state_path.write_text(
        json.dumps(
            {
                alias: {
                    "FunctionVersion": applied_version,
                    "RevisionId": "revision-returned-only-by-a-follow-up-read",
                    "Description": applied_description,
                    "RoutingConfig": {"AdditionalVersionWeights": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            state_path = Path(os.environ["FAKE_ALIAS_STATE"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            name = args[args.index("--name") + 1]
            if args[:2] == ["lambda", "get-alias"]:
                print(json.dumps(state[name]))
                raise SystemExit(0)
            if args[:2] != ["lambda", "update-alias"]:
                raise SystemExit(90)
            current = state[name]
            revision = args[args.index("--revision-id") + 1]
            if revision != current["RevisionId"]:
                raise SystemExit(91)
            current = {
                "FunctionVersion": args[args.index("--function-version") + 1],
                "RevisionId": current["RevisionId"] + "-restored",
                "Description": args[args.index("--description") + 1],
                "RoutingConfig": {"AdditionalVersionWeights": {}},
            }
            state[name] = current
            state_path.write_text(json.dumps(state), encoding="utf-8")
            print(json.dumps(current))
            """
        ),
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)

    expected_revision = f"{expected_prefix}_EXPECTED_REVISION"
    harness = tmp_path / "restore.sh"
    harness.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            CONSOLE_FN=fare-policy-assistant-test-console
            LIVE_ALIAS=live
            ROLLBACK_ALIAS=rollback
            REGION=us-west-2
            EMPTY_ALIAS_ROUTING='{{"AdditionalVersionWeights":{{}}}}'
            {active_variable}=true
            {expected_prefix}_EXPECTED_VERSION={applied_version}
            {expected_revision}=""
            {expected_prefix}_EXPECTED_DESCRIPTION={applied_description!r}
            {expected_prefix}_RESTORE_VERSION={restore_version}
            {expected_prefix}_RESTORE_DESCRIPTION={restore_description!r}
            assert_unweighted_alias() {{
              jq -e '((.RoutingConfig.AdditionalVersionWeights // {{}}) | length) == 0' \
                <<<"$1" >/dev/null
            }}
            {function}
            {function.split("()", 1)[0]}
            """
        ),
        encoding="utf-8",
    )
    harness.chmod(0o755)
    subprocess.run(
        ["bash", harness],
        check=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_ALIAS_STATE": os.fspath(state_path),
        },
        capture_output=True,
        text=True,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert isinstance(state, dict)
    restored = state[alias]
    assert isinstance(restored, dict)
    return restored


def test_script_is_executable_and_has_valid_bash_syntax() -> None:
    assert DEPLOY_CONSOLE.stat().st_mode & 0o111
    subprocess.run(["bash", "-n", DEPLOY_CONSOLE], check=True)


@pytest.mark.parametrize(
    ("included", "extra", "expected"),
    [
        (
            ("summary.json", "results.jsonl"),
            None,
            "promotion evidence is missing regular file promotion.json",
        ),
        (
            ("summary.json", "results.jsonl", "promotion.json"),
            "latest.json",
            "promotion evidence directory must contain exactly",
        ),
    ],
)
def test_evidence_directory_fails_before_any_external_mutation(
    tmp_path: Path,
    included: tuple[str, ...],
    extra: str | None,
    expected: str,
) -> None:
    repo = _clean_script_repo(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in included:
        (evidence / name).write_text("{}\n", encoding="utf-8")
    if extra is not None:
        (evidence / extra).write_text("{}\n", encoding="utf-8")

    result = _run_with_evidence(repo, evidence, tmp_path)

    assert result.returncode == 2
    assert expected in result.stderr
    assert "unexpected aws invocation" not in result.stderr
    assert "unexpected uv invocation" not in result.stderr


def test_inputs_and_closed_evidence_verifier_are_required(script: str) -> None:
    for variable in (
        "FPA_RIDER_FUNCTION_NAME",
        "FPA_RIDER_BASE_URL",
        "FPA_CONSOLE_TOKEN_PARAMETER_NAME",
        "FPA_PROMOTION_EVIDENCE_DIR",
    ):
        assert f"${{{variable}:?" in script
    assert '[[ "$EVIDENCE_INPUT" == /* ]]' in script
    assert '[[ "$RIDER_ALIAS" == "live" ]]' in script
    assert "summary.json results.jsonl promotion.json" in script
    verifier = script.index("verify_promotion_evidence(")
    first_aws_read = script.index("aws sts get-caller-identity")
    assert verifier < first_aws_read
    for digest in ("summary_sha256", "results_sha256", "promotion_sha256"):
        assert digest in script
    assert 'PROMOTION_RUNTIME="$(jq -c \'.runtime_release\' <<<"$EVIDENCE_STATUS")"' in script


def test_bundle_is_tracked_hash_pinned_and_byte_reproducible(script: str) -> None:
    assert '--require-hashes -r "$ROOT/infra/requirements-deploy.txt"' in script
    assert script.count("uv run --frozen") >= 4
    assert "scripts/copy_tracked_bundle.py" in script
    assert "--tree src/assistant" in script
    for bundled_file in (
        "corpus/processed/chunks.jsonl",
        "web/__init__.py",
        "web/console.py",
        "web/a11y.py",
    ):
        assert f"--file {bundled_file}" in script
    assert "scripts/build_lambda_zip.py" in script
    assert "zip -q" not in script
    assert "cp -R" not in script
    assert "bundled promotion evidence differs from the verified bytes" in script
    assert 'git -C "$ROOT" status --porcelain --untracked-files=normal' in script


def test_candidate_is_verified_before_guarded_alias_cutover(script: str) -> None:
    publish = script.index("aws lambda publish-version", script.index("# Stage the new artifact"))
    health = script.index('console_candidate_health "$NEW_VERSION"', publish)
    final_rider_check = script.index(
        "# Verify the rider tuple again immediately before any console alias movement.",
        health,
    )
    live_update = script.index(
        "aws lambda update-alias \\\n"
        '      --function-name "$CONSOLE_FN" \\\n'
        '      --name "$LIVE_ALIAS"',
        final_rider_check,
    )
    assert publish < health < final_rider_check < live_update
    assert '--code-sha256 "$CANDIDATE_CODE_SHA"' in script
    assert '--revision-id "$CANDIDATE_REVISION"' in script
    assert "--update-runtime-on FunctionUpdate" in script
    assert '--routing-config "$EMPTY_ALIAS_ROUTING"' in script
    assert '--revision-id "$BASELINE_LIVE_REVISION"' in script


def test_live_and_rollback_aliases_have_exit_guards(script: str) -> None:
    for function in (
        "restore_unverified_live()",
        "restore_previous_rollback_pointer()",
        "release_exit_guard()",
    ):
        assert function in script
    assert "trap release_exit_guard EXIT" in script
    assert "PROMOTION_GUARD_ACTIVE=true" in script
    assert "ROLLBACK_POINTER_GUARD_ACTIVE=true" in script
    assert '--name "$LIVE_ALIAS"' in script
    assert '--name "$ROLLBACK_ALIAS"' in script
    assert '--function-version "$BASELINE_LIVE_VERSION"' in script


def test_live_guard_restores_an_applied_update_when_cli_returned_no_revision(
    tmp_path: Path,
    script: str,
) -> None:
    restored = _exercise_ambiguous_restore(
        tmp_path,
        function=_shell_function(script, "restore_unverified_live"),
        alias="live",
        active_variable="PROMOTION_GUARD_ACTIVE",
        expected_prefix="PROMOTION_GUARD",
        restore_version="5",
        restore_description="prior live",
        applied_version="6",
        applied_description="candidate previous=5",
    )

    assert restored["FunctionVersion"] == "5"
    assert restored["Description"] == "prior live"


def test_rollback_guard_is_armed_before_update_and_restores_ambiguous_apply(
    tmp_path: Path,
    script: str,
) -> None:
    rollback_block = script.index('if PREVIOUS_ROLLBACK_JSON="$(')
    guard = script.index("ROLLBACK_POINTER_GUARD_ACTIVE=true", rollback_block)
    update = script.index('UPDATED_ROLLBACK_JSON="$(', rollback_block)
    assert guard < update

    restored = _exercise_ambiguous_restore(
        tmp_path,
        function=_shell_function(script, "restore_previous_rollback_pointer"),
        alias="rollback",
        active_variable="ROLLBACK_POINTER_GUARD_ACTIVE",
        expected_prefix="ROLLBACK_POINTER_GUARD",
        restore_version="4",
        restore_description="prior rollback",
        applied_version="5",
        applied_description="prior live",
    )

    assert restored["FunctionVersion"] == "4"
    assert restored["Description"] == "prior rollback"


def test_api_gateway_and_permission_target_only_the_live_alias(script: str) -> None:
    assert 'ALIAS_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$CONSOLE_FN:$LIVE_ALIAS"' in script
    assert '--target "$ALIAS_ARN"' in script
    assert '--integration-uri "$ALIAS_INTEGRATION_URI"' in script
    assert '--qualifier "$LIVE_ALIAS"' in script
    assert "--statement-id apigw-live" in script
    assert "and .Resource == $resource" in script
    assert "integration_targets_unqualified_function" in script
    assert "restoring the prior integration" in script
    assert '--target "$UNQUALIFIED_ARN"' not in script


def test_auth_and_rider_identity_are_preserved_and_probed(script: str) -> None:
    assert "FPA_CONSOLE_TOKEN_PARAMETER_NAME: $token_parameter" in script
    assert '"Action": "ssm:GetParameter"' in script
    assert "parameter$CONSOLE_TOKEN_PARAMETER" in script
    assert 'rawPath":"/console/api/status"' in script
    assert ".statusCode == 401" in script
    assert 'function:$RIDER_FN:live"' in script
    assert 'function:$RIDER_FN:*"' in script
    assert '--function-name "$RIDER_FN" --qualifier "$rider_version"' in script
    for field in (
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "artifact_code_sha256",
        "function_version",
    ):
        assert f"$promotion.{field}" in script
    assert '"$RIDER_BASE_URL/version"' in script
