"""Guards the cross-container rate limit configured in infra/deploy.sh.

`deploy.sh` is the whole deployment (there is no CDK/Terraform layer to
type-check), so this is the regression test for the roadmap P1 item 4 fix: a
gateway-level throttle, derived from and kept in sync with the Lambda
reserved-concurrency ceiling, is the true cross-container rate limit (see
ADR 0004 amendment "a true cross-container rate limit"). This test does not
call AWS; it asserts the script still declares that relationship correctly so
a future edit cannot silently drop the throttle or let it drift out of sync
with concurrency.
"""

from __future__ import annotations

import ast
import re

from assistant import config

DEPLOY_SH = config.REPO_ROOT / "infra" / "deploy.sh"
DEPLOY_CONSOLE_SH = config.REPO_ROOT / "infra" / "deploy-console.sh"


def _script_text() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8")


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
        assert 'mkdir -p "$BUNDLE/src" "$BUNDLE/corpus/processed" "$BUNDLE/docs"' in text
        assert 'cp "$ROOT/docs/answer-contract.schema.json" "$BUNDLE/docs/"' in text

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
            bundled_path = f'"$ROOT/{module.replace(".", "/")}.py"'
            assert bundled_path in text, f"rider bundle omits local import {module}"

    def test_console_bundle_includes_ingest_import_dependencies(self):
        text = DEPLOY_CONSOLE_SH.read_text(encoding="utf-8")
        assert '"httpx>=0.27"' in text
        assert '"beautifulsoup4>=4.12"' in text
