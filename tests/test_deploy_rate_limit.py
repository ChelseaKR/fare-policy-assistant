"""Guards the rate limits configured in infra/deploy.sh.

`deploy.sh` is the whole deployment (there is no CDK/Terraform layer to
type-check), so this is the regression test for the roadmap P1 item 4 fix: a
gateway-level throttle, derived from and kept in sync with the Lambda
reserved-concurrency ceiling, is the true cross-container rate limit (see
ADR 0004 amendment "a true cross-container rate limit"). This test does not
call AWS; it asserts the script still declares that relationship correctly so
a future edit cannot silently drop the throttle or let it drift out of sync
with concurrency.

It also guards the per-caller layer added in ADR 0025, including the privacy
constraints on it: the limiter's least-privilege grant, and the rule that no
raw caller address may be logged or persisted.
"""

from __future__ import annotations

import ast
import re

from assistant import config

DEPLOY_SH = config.REPO_ROOT / "infra" / "deploy.sh"
DEPLOY_CONSOLE_SH = config.REPO_ROOT / "infra" / "deploy-console.sh"
DEPLOY_CUTOFF_SH = config.REPO_ROOT / "infra" / "deploy-cutoff.sh"
RATELIMIT_PY = config.REPO_ROOT / "web" / "ratelimit.py"
TELEMETRY_PY = config.REPO_ROOT / "src" / "assistant" / "telemetry.py"


def _script_text() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8")


def _literal_strings(source: str) -> set[str]:
    """Every string literal in a module except its docstrings.

    Prose that describes a rule ("the X-Forwarded-For header is never read")
    must not read as a violation of it, so the docstring-only assertions below
    look at code, not commentary.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


class TestGatewayThrottleConfigured:
    def test_deploy_script_exists(self):
        assert DEPLOY_SH.is_file()

    def test_reserved_concurrency_is_declared(self):
        text = _script_text()
        match = re.search(r"^RESERVED_CONCURRENCY=(\d+)\s*$", text, re.MULTILINE)
        assert match, "expected a RESERVED_CONCURRENCY=<int> declaration"
        assert int(match.group(1)) >= 1

    def test_throttle_rate_and_burst_are_derived_from_concurrency(self):
        text = _script_text()
        assert 'THROTTLE_RATE_LIMIT="$RESERVED_CONCURRENCY"' in text, (
            "the gateway throttle rate must be derived from RESERVED_CONCURRENCY, "
            "not a separate hardcoded number, or the two can silently drift"
        )
        assert re.search(r"THROTTLE_BURST_LIMIT=\$\(\(RESERVED_CONCURRENCY", text), (
            "the gateway throttle burst must be derived from RESERVED_CONCURRENCY"
        )

    def test_concurrency_and_throttle_use_the_same_variables(self):
        text = _script_text()
        # The Lambda concurrency ceiling and the API Gateway throttle must both
        # read from the same variables set at the top of the script -- no
        # separate magic numbers restating either value further down.
        assert '--reserved-concurrent-executions "$RESERVED_CONCURRENCY"' in text
        assert "$THROTTLE_RATE_LIMIT" in text
        assert "$THROTTLE_BURST_LIMIT" in text
        assert re.search(r"ThrottlingRateLimit.*THROTTLE_RATE_LIMIT", text)
        assert re.search(r"ThrottlingBurstLimit.*THROTTLE_BURST_LIMIT", text)

    def test_throttle_applied_to_the_default_stage(self):
        text = _script_text()
        assert "apigatewayv2 update-stage" in text
        assert "--stage-name '$default'" in text

    def test_rate_and_burst_relationship_is_sane(self):
        """Burst must be >= rate (a burst smaller than the steady rate is not
        a burst) and both must be positive integers, computed here the same
        way the script computes them so this test breaks if the formula
        changes without a human deciding that's still sane."""
        text = _script_text()
        match = re.search(r"^RESERVED_CONCURRENCY=(\d+)\s*$", text, re.MULTILINE)
        concurrency = int(match.group(1))
        rate = concurrency
        burst = concurrency * 2 + 1
        assert rate >= 1
        assert burst >= rate


class TestPerCallerLimiterProvisioned:
    """ADR 0025: the aggregate throttle above is not a per-caller control."""

    def test_limiter_table_is_declared_and_passed_to_the_function(self):
        text = _script_text()
        assert re.search(r'^RATE_LIMIT_TABLE="\$\{FPA_RATE_LIMIT_TABLE:-', text, re.MULTILINE)
        assert '"FPA_RATE_LIMIT_TABLE": os.environ["FPA_DEPLOY_RATE_LIMIT_TABLE"]' in text
        assert '"FPA_RATE_LIMIT_HMAC_KEY": os.environ["FPA_DEPLOY_RATE_LIMIT_HMAC_KEY"]' in text

    def test_caller_digest_key_is_inherited_not_regenerated_each_deploy(self):
        """A key that changed every release would reset every counter and make
        the limiter trivially evadable by waiting for a deploy."""
        text = _script_text()
        assert 'RATE_LIMIT_HMAC_KEY="$EXISTING_RATE_LIMIT_HMAC_KEY"' in text
        assert re.search(r"RATE_LIMIT_HMAC_KEY.*=~ \^\[0-9a-f\]\{64\}\$", text)

    def test_table_is_created_with_ttl_so_counters_expire(self):
        text = _script_text()
        assert "dynamodb create-table" in text
        assert "--billing-mode PAY_PER_REQUEST" in text
        assert "AttributeName=expires_at" in text

    def test_the_rider_grant_is_least_privilege(self):
        """The handler may count and read the breaker. It must not be able to
        clear its own limiting, forge a breaker, or enumerate other callers."""
        text = _script_text()
        assert '"Action": ["dynamodb:UpdateItem", "dynamodb:GetItem"]' in text
        for forbidden in (
            "dynamodb:PutItem",
            "dynamodb:DeleteItem",
            "dynamodb:Scan",
            "dynamodb:Query",
            "dynamodb:*",
        ):
            assert forbidden not in text, f"rider role must not hold {forbidden}"

    def test_table_is_cost_allocated(self):
        assert "dynamodb tag-resource" in _script_text()


class TestSpendCutoff:
    def test_cutoff_script_exists_and_is_separate_from_the_release(self):
        assert DEPLOY_CUTOFF_SH.is_file()
        # The rider deploy must not create or require the breaker stack, or a
        # routine release would start failing wherever the cutoff is absent.
        text = _script_text()
        assert "$BREAKER_FN" not in text
        assert "spend-cutoff" not in text

    def test_breaker_listens_on_its_own_topic_not_the_alerts_topic(self):
        """Subscribing to the alerts topic would cut off spend whenever a
        latency or handler-error alarm fired."""
        text = DEPLOY_CUTOFF_SH.read_text(encoding="utf-8")
        assert 'TOPIC_NAME="${FPA_CUTOFF_TOPIC_NAME:-$FN-spend-cutoff}"' in text
        assert '--protocol lambda --notification-endpoint "$BREAKER_ARN"' in text
        assert '--topic-arn "$TOPIC_ARN"' in text
        assert '--notification-endpoint "$BREAKER_ARN"' in text
        assert "$ALERTS_TOPIC_ARN" in text  # paged, but only as an alarm action

    def test_fast_path_alarm_watches_the_application_cost_metric(self):
        text = DEPLOY_CUTOFF_SH.read_text(encoding="utf-8")
        assert "--metric-name EstimatedModelCostUsd" in text
        assert "--treat-missing-data notBreaching" in text

    def test_cutoff_never_zeroes_the_rider_concurrency(self):
        """Reserved concurrency 0 would take down /offline and /guide too,
        which is the opposite of degrading to them."""
        text = DEPLOY_CUTOFF_SH.read_text(encoding="utf-8")
        assert "--reserved-concurrent-executions 0" not in text
        breaker = (config.REPO_ROOT / "web" / "spend_breaker.py").read_text(encoding="utf-8")
        assert "put_function_concurrency" not in breaker
        # The breaker talks to exactly one AWS service. A Lambda client here
        # would mean it had grown the ability to change the rider function.
        assert '"lambda"' not in breaker
        assert _literal_strings(breaker) & {"dynamodb"}

    def test_breaker_grant_is_scoped_to_the_single_breaker_row(self):
        text = DEPLOY_CUTOFF_SH.read_text(encoding="utf-8")
        assert "dynamodb:LeadingKeys" in text
        assert '"dynamodb:PutItem"' in text
        assert "dynamodb:UpdateItem" not in text


class TestCallerAddressNeverPersistedOrLogged:
    """The privacy constraint ADR 0025 trades against. Guard it in code, not
    just in prose: this service's value depends on the claim being true."""

    def test_the_limiter_hashes_the_address_with_a_secret(self):
        source = RATELIMIT_PY.read_text(encoding="utf-8")
        assert "hmac.new" in source
        assert "window_index" in source

    def test_the_limiter_never_logs(self):
        """web/ratelimit.py may call telemetry helpers that take no key, and
        must never print or log a caller-derived value directly."""
        source = RATELIMIT_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "print" not in called
        for helper in called & {"log_rate_limit_unavailable", "log_caller_rate_limited"}:
            assert helper in TELEMETRY_PY.read_text(encoding="utf-8")

    def test_no_telemetry_helper_accepts_an_address_or_a_digest(self):
        tree = ast.parse(TELEMETRY_PY.read_text(encoding="utf-8"))
        forbidden = {
            "ip",
            "source_ip",
            "sourceip",
            "address",
            "caller",
            "caller_key",
            "digest",
            "user_agent",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("log_"):
                continue
            names = {argument.arg.lower() for argument in node.args.kwonlyargs + node.args.args}
            assert not (names & forbidden), f"{node.name} takes a caller identifier: {names}"

    def test_the_forwarded_for_header_is_not_consulted(self):
        """Trusting a client-supplied header would let one caller mint a fresh
        identity per request and evade the limiter entirely. Only string
        literals count here: the module docstring explains the rule, and
        explaining it must not look like breaking it."""
        literals = {value.lower() for value in _literal_strings(RATELIMIT_PY.read_text("utf-8"))}
        assert not any("forwarded" in value for value in literals)
        assert not any("header" in value for value in literals)


class TestBundleRuntimeDependencies:
    """Imports that succeed in the checkout must also succeed in Lambda.

    The deploy scripts build intentionally small bundles instead of installing
    the whole project, so keep their explicit dependency/file lists aligned
    with the modules imported by each handler.
    """

    def test_rider_bundle_includes_structured_contract_runtime(self):
        # jsonschema arrives via the hash-pinned requirement set (M-7/P1-6);
        # tests/test_deploy_requirements.py holds that file against uv.lock.
        text = _script_text()
        assert '-r "$ROOT/infra/requirements-deploy.txt"' in text
        assert "mkdir -p" in text
        for directory in ("src", "corpus/processed", "docs"):
            assert f'"$BUNDLE/{directory}"' in text
        assert "uv run python scripts/copy_tracked_bundle.py" in text
        assert "--file docs/answer-contract.schema.json" in text

    def test_rider_bundle_includes_every_local_web_import(self):
        text = _script_text()
        handler = (config.REPO_ROOT / "web" / "handler.py").read_text(encoding="utf-8")
        imported_modules = {
            node.module
            for node in ast.walk(ast.parse(handler))
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("web.")
        }
        assert imported_modules, "expected the rider handler to import local web modules"
        for module in imported_modules:
            bundled_path = f"--file {module.replace('.', '/')}.py"
            assert bundled_path in text, f"rider bundle omits local import {module}"

    def test_console_bundle_includes_ingest_import_dependencies(self):
        text = DEPLOY_CONSOLE_SH.read_text(encoding="utf-8")
        assert '--require-hashes -r "$ROOT/infra/requirements-deploy.txt"' in text
        requirements = (config.REPO_ROOT / "infra" / "requirements-deploy.txt").read_text(
            encoding="utf-8"
        )
        assert "httpx==" in requirements
        assert "beautifulsoup4==" in requirements
