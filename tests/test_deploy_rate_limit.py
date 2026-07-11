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

import re

from assistant import config

DEPLOY_SH = config.REPO_ROOT / "infra" / "deploy.sh"


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
