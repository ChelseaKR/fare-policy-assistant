"""Per-caller limiting and the spend breaker (ADR 0025).

These tests run entirely offline against a fake DynamoDB client. The privacy
assertions are the point of the file: the caller's address must never reach a
stored key, a log record, or a response body.
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from assistant import config
from web import handler as web_handler
from web import ratelimit

TABLE = "fare-policy-assistant-demo-limits"
SECRET = "a" * 64
CALLER = "203.0.113.47"

# An instant safely inside a fixed window: 1_800_000_000 is exactly divisible by
# RATE_LIMIT_WINDOW_SECONDS, so this sits 30 seconds into one, 30 from the next.
MID_WINDOW = 1_800_000_030.0


class FakeDynamo:
    """Minimal stand-in implementing the two calls the limiter makes."""

    def __init__(self, *, fail: Exception | None = None, breaker: bool | None = None):
        self.counters: dict[str, int] = {}
        self.updates: list[dict] = []
        self.gets: list[dict] = []
        self.fail = fail
        self.breaker = breaker

    def update_item(self, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.updates.append(kwargs)
        key = kwargs["Key"]["pk"]["S"]
        self.counters[key] = self.counters.get(key, 0) + 1
        return {"Attributes": {"n": {"N": str(self.counters[key])}}}

    def get_item(self, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.gets.append(kwargs)
        if self.breaker is None:
            return {}
        return {"Item": {"pk": {"S": ratelimit.BREAKER_KEY}, "open": {"BOOL": self.breaker}}}


class PinnedWindowClock:
    """`web.ratelimit`'s clock, with the fixed window pinned.

    The two limiters on ``/api/ask`` measure time differently. ``ratelimit.check``
    counts in a fixed wall-clock window
    (``window_index(now) = int(time.time() // RATE_LIMIT_WINDOW_SECONDS)``), while
    ``web.handler._over_budget`` is a sliding window on ``time.monotonic()``. Only
    the per-caller 429 carries ``offline`` and ``guide``.

    A test that drives the handler in a loop is therefore racing the wall clock:
    if the loop straddles a minute boundary the fixed window rolls over, the
    per-caller counter resets to 1, the request the test expects that limiter to
    refuse is admitted, and the container backstop answers instead — with a body
    that has no ``offline`` key. The odds are the loop's duration over 60
    seconds, so it is near zero on a laptop and real on a slow runner (#206).

    Pinning the window is the fix, rather than loosening the assertion: the
    assertion is the property the test exists for. ``monotonic`` is deliberately
    left real. The sliding backstop is not the racy half — it is reset per test
    by ``clean_limiter`` clearing ``_RECENT`` — and freezing it would stop these
    tests from exercising it at all.
    """

    def __init__(self, at: float = MID_WINDOW) -> None:
        self.at = at

    def time(self) -> float:
        return self.at

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()


@pytest.fixture
def pinned_window(monkeypatch):
    """Pin the per-caller limiter's fixed window for the duration of a test."""
    clock = PinnedWindowClock()
    monkeypatch.setattr(ratelimit, "time", clock)
    return clock


@pytest.fixture(autouse=True)
def clean_limiter(monkeypatch):
    monkeypatch.setenv("FPA_PROVIDER", "mock")
    monkeypatch.delenv("FPA_RATE_LIMIT_TABLE", raising=False)
    monkeypatch.delenv("FPA_RATE_LIMIT_HMAC_KEY", raising=False)
    ratelimit.reset_for_tests()
    web_handler._RECENT.clear()
    web_handler._ANSWER_CACHE.clear()
    yield
    ratelimit.reset_for_tests()


@pytest.fixture
def limiter(monkeypatch):
    """An enabled limiter backed by the fake client."""

    def _install(**kwargs) -> FakeDynamo:
        monkeypatch.setenv("FPA_RATE_LIMIT_TABLE", TABLE)
        monkeypatch.setenv("FPA_RATE_LIMIT_HMAC_KEY", SECRET)
        fake = FakeDynamo(**kwargs)
        monkeypatch.setattr(ratelimit, "_client", fake)
        return fake

    return _install


def _event(
    *,
    ip: str | None = CALLER,
    path: str = "/api/ask",
    body: dict | None = None,
    method: str = "POST",
) -> dict:
    http: dict = {"method": method}
    if ip is not None:
        http["sourceIp"] = ip
    return {
        "requestContext": {"http": http},
        "rawPath": path,
        "body": json.dumps(body) if body is not None else None,
    }


def _log_records(caplog, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


class TestCallerDigest:
    def test_digest_is_stable_within_a_window(self, limiter):
        limiter()
        first = ratelimit.caller_digest(CALLER, "ask", 1_000_000.0)
        second = ratelimit.caller_digest(CALLER, "ask", 1_000_000.5)
        assert first == second

    def test_digest_rotates_between_windows(self, limiter):
        limiter()
        window = config.RATE_LIMIT_WINDOW_SECONDS
        first = ratelimit.caller_digest(CALLER, "ask", 1_000_000.0)
        later = ratelimit.caller_digest(CALLER, "ask", 1_000_000.0 + window)
        assert first != later, "the same caller must be unlinkable across windows"

    def test_routes_do_not_share_a_bucket(self, limiter):
        limiter()
        assert ratelimit.caller_digest(CALLER, "ask", 1.0) != ratelimit.caller_digest(
            CALLER, "feedback", 1.0
        )

    def test_digest_depends_on_the_secret(self, limiter, monkeypatch):
        limiter()
        with_first = ratelimit.caller_digest(CALLER, "ask", 1.0)
        monkeypatch.setenv("FPA_RATE_LIMIT_HMAC_KEY", "b" * 64)
        assert ratelimit.caller_digest(CALLER, "ask", 1.0) != with_first

    def test_digest_does_not_contain_the_address(self, limiter):
        limiter()
        digest = ratelimit.caller_digest(CALLER, "ask", 1.0)
        assert CALLER not in digest
        assert len(digest) == 32
        assert all(character in "0123456789abcdef" for character in digest)

    def test_distinct_callers_get_distinct_buckets(self, limiter):
        limiter()
        assert ratelimit.caller_digest("198.51.100.9", "ask", 1.0) != ratelimit.caller_digest(
            CALLER, "ask", 1.0
        )


class TestSourceAddressExtraction:
    def test_reads_the_gateway_populated_context(self):
        assert ratelimit.source_ip(_event()) == CALLER

    @pytest.mark.parametrize(
        "event",
        [
            {},
            {"requestContext": None},
            {"requestContext": {}},
            {"requestContext": {"http": None}},
            {"requestContext": {"http": {}}},
            {"requestContext": {"http": {"sourceIp": 42}}},
        ],
    )
    def test_missing_or_malformed_context_yields_no_address(self, event):
        assert ratelimit.source_ip(event) == ""

    def test_forwarded_header_is_never_trusted(self):
        event = _event()
        event["headers"] = {"x-forwarded-for": "9.9.9.9"}
        assert ratelimit.source_ip(event) == CALLER


class TestPerCallerCounting:
    def test_requests_up_to_the_quota_are_allowed(self, limiter):
        limiter()
        for _ in range(5):
            assert ratelimit.check(_event(), route="ask", limit=5).allowed

    def test_the_request_after_the_quota_is_refused(self, limiter):
        limiter()
        for _ in range(3):
            ratelimit.check(_event(), route="ask", limit=3, now=100.0)
        decision = ratelimit.check(_event(), route="ask", limit=3, now=100.0)
        assert decision.allowed is False
        assert decision.counted is True

    def test_a_new_window_gives_a_fresh_allowance(self, limiter):
        limiter()
        window = config.RATE_LIMIT_WINDOW_SECONDS
        for _ in range(4):
            ratelimit.check(_event(), route="ask", limit=3, now=100.0)
        assert ratelimit.check(_event(), route="ask", limit=3, now=100.0 + window).allowed

    def test_one_caller_cannot_spend_anothers_quota(self, limiter):
        limiter()
        for _ in range(4):
            ratelimit.check(_event(), route="ask", limit=3, now=100.0)
        other = _event(ip="198.51.100.9")
        assert ratelimit.check(other, route="ask", limit=3, now=100.0).allowed

    def test_stored_key_carries_no_address_and_an_expiry(self, limiter):
        fake = limiter()
        ratelimit.check(_event(), route="ask", limit=5, now=100.0)
        call = fake.updates[0]
        assert call["TableName"] == TABLE
        stored_key = call["Key"]["pk"]["S"]
        assert CALLER not in stored_key
        assert json.dumps(call).count(CALLER) == 0, "no address may reach DynamoDB"
        ttl = int(call["ExpressionAttributeValues"][":ttl"]["N"])
        assert ttl > 100.0
        assert ttl <= 100.0 + 2 * config.RATE_LIMIT_WINDOW_SECONDS + 60

    def test_counting_is_a_single_atomic_update(self, limiter):
        fake = limiter()
        ratelimit.check(_event(), route="ask", limit=5)
        assert len(fake.updates) == 1
        assert "ADD n :one" in fake.updates[0]["UpdateExpression"]
        assert fake.updates[0]["ReturnValues"] == "UPDATED_NEW"


class TestDisabledAndFailOpen:
    def test_no_table_configured_means_no_limiting(self, monkeypatch):
        monkeypatch.setenv("FPA_RATE_LIMIT_HMAC_KEY", SECRET)
        decision = ratelimit.check(_event(), route="ask", limit=1)
        assert decision.allowed is True
        assert decision.counted is False

    def test_no_secret_configured_means_no_limiting(self, monkeypatch):
        monkeypatch.setenv("FPA_RATE_LIMIT_TABLE", TABLE)
        decision = ratelimit.check(_event(), route="ask", limit=1)
        assert decision == ratelimit.Decision(allowed=True, counted=False)

    def test_direct_invocation_without_an_address_is_exempt(self, limiter):
        fake = limiter()
        decision = ratelimit.check({"body": "{}"}, route="ask", limit=1)
        assert decision.allowed is True
        assert decision.counted is False
        assert fake.updates == []

    def test_backend_failure_admits_the_request_and_is_logged(self, limiter, caplog):
        limiter(fail=RuntimeError("throughput exceeded"))
        with caplog.at_level(logging.WARNING, logger="fare_assistant"):
            decision = ratelimit.check(_event(), route="ask", limit=1)
        assert decision.allowed is True
        assert decision.counted is False
        record = _log_records(caplog, "rate_limit_unavailable")[-1]
        assert record.route == "ask"
        assert record.error_type == "RuntimeError"


class TestSpendBreaker:
    def test_closed_when_no_item_exists(self, limiter):
        limiter()
        assert ratelimit.breaker_open(now=1.0) is False

    def test_open_when_the_row_says_so(self, limiter):
        limiter(breaker=True)
        assert ratelimit.breaker_open(now=1.0) is True

    def test_explicitly_closed_row_is_closed(self, limiter):
        limiter(breaker=False)
        assert ratelimit.breaker_open(now=1.0) is False

    def test_state_is_cached_between_reads(self, limiter):
        fake = limiter(breaker=True)
        ratelimit.breaker_open(now=1.0)
        ratelimit.breaker_open(now=2.0)
        assert len(fake.gets) == 1

    def test_cache_expires(self, limiter):
        fake = limiter(breaker=True)
        ratelimit.breaker_open(now=1.0)
        ratelimit.breaker_open(now=1.0 + config.SPEND_BREAKER_CACHE_SECONDS + 1)
        assert len(fake.gets) == 2

    def test_no_table_means_closed_without_a_call(self, monkeypatch):
        assert ratelimit.breaker_open(now=1.0) is False

    def test_read_failure_leaves_the_service_answering(self, limiter, caplog):
        limiter(fail=RuntimeError("timeout"))
        with caplog.at_level(logging.WARNING, logger="fare_assistant"):
            assert ratelimit.breaker_open(now=1.0) is False
        record = _log_records(caplog, "rate_limit_unavailable")[-1]
        assert record.route == "spend_breaker"


class TestHandlerWiring:
    @pytest.fixture(autouse=True)
    def _pin_the_window(self, pinned_window):
        """Every test in this class drives the handler in a loop, so every one of
        them was racing the fixed window's rollover (#206). Pinned once here
        rather than per test, because the race is a property of the loop and not
        of any one assertion."""

    def test_ask_returns_429_and_points_at_the_offline_routes(self, limiter, caplog):
        limiter()
        limit = config.RATE_LIMIT_ASK_PER_WINDOW
        for i in range(limit):
            response = web_handler.handler(_event(body={"question": f"MST fare question {i}?"}))
            assert response["statusCode"] in (200, 429), response
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(_event(body={"question": "One more MST fare question?"}))
        assert response["statusCode"] == 429
        # Which limiter fired is asserted before the body is read. Both return
        # 429 and only one carries `offline`, so reading the body first turns
        # "the wrong limiter answered" into a bare KeyError that says nothing
        # about why.
        record = _log_records(caplog, "caller_rate_limited")[-1]
        assert record.route == "ask"
        assert record.limit == limit
        body = json.loads(response["body"])
        assert body["offline"] == "/offline"
        assert body["guide"] == "/guide"

    def test_a_window_rollover_hands_the_refusal_to_the_container_backstop(
        self, limiter, pinned_window, caplog
    ):
        """The trap behind #206, made a pinned property instead of a surprise.

        This is the same loop as the test above with one difference: the fixed
        window rolls over before the final request. The per-caller counter resets
        to 1, so that limiter admits the request, and the per-container sliding
        backstop refuses it instead — with a different body.

        Asserted rather than fixed. The two controls answer different questions
        ("this caller has had its share" against "this container is spending too
        fast"), the ordering between them is deliberate and documented in
        `assistant.config` (the ask quota of 10 sits above the container budget
        of 8 on purpose), and changing either body is a rider-facing decision,
        not a test fix. What was wrong was a test that could land on either side
        of it depending on when the clock ticked.
        """
        limiter()
        for i in range(config.RATE_LIMIT_ASK_PER_WINDOW):
            web_handler.handler(_event(body={"question": f"MST fare question {i}?"}))

        pinned_window.at += config.RATE_LIMIT_WINDOW_SECONDS

        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(_event(body={"question": "One more MST fare question?"}))
        assert response["statusCode"] == 429
        body = json.loads(response["body"])
        assert body["error"].startswith("Too many requests right now"), (
            "the container backstop should be the one refusing after a rollover"
        )
        assert "offline" not in body, (
            "only the per-caller 429 points a rate-limited rider at the "
            "zero-model-call routes; this asymmetry is what made #206 a KeyError"
        )

    def test_the_window_stays_pinned_across_a_loop(self, pinned_window):
        """Negative control for the fixture itself.

        A pin that silently stopped applying would read exactly like a fix: the
        test above would go back to passing locally and flaking in CI, with a
        fixture in the file suggesting otherwise.
        """
        before = ratelimit.window_index(ratelimit.time.time())
        for _ in range(config.RATE_LIMIT_ASK_PER_WINDOW + 1):
            assert ratelimit.window_index(ratelimit.time.time()) == before
        assert ratelimit.time.time() == pinned_window.at
        assert ratelimit.time is not time, "the pin never landed on the module under test"
        assert ratelimit.time.monotonic() == pytest.approx(time.monotonic(), abs=1.0), (
            "monotonic must stay real, or the sliding container backstop stops being exercised"
        )

    def test_a_limited_caller_does_not_block_another(self, limiter):
        limiter()
        for i in range(config.RATE_LIMIT_ASK_PER_WINDOW + 1):
            web_handler.handler(_event(body={"question": f"MST fare question {i}?"}))
        # Clear the pre-existing per-container budget, which is shared by every
        # caller and would otherwise mask the property under test. That backstop
        # starving a second rider is exactly the behaviour this limiter exists
        # to sit in front of.
        web_handler._RECENT.clear()
        other = _event(ip="198.51.100.9", body={"question": "A Unitrans fare question?"})
        assert web_handler.handler(other)["statusCode"] != 429

    def test_feedback_is_rate_limited(self, limiter, caplog):
        limiter()
        limit = config.RATE_LIMIT_FEEDBACK_PER_WINDOW
        for _ in range(limit):
            response = web_handler.handler(
                _event(path="/api/feedback", body={"verdict": "up"}),
            )
            assert response["statusCode"] == 200
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(_event(path="/api/feedback", body={"verdict": "up"}))
        assert response["statusCode"] == 429
        assert _log_records(caplog, "caller_rate_limited")[-1].route == "feedback"

    def test_feedback_and_ask_quotas_are_separate(self, limiter):
        limiter()
        for _ in range(config.RATE_LIMIT_FEEDBACK_PER_WINDOW + 1):
            web_handler.handler(_event(path="/api/feedback", body={"verdict": "down"}))
        response = web_handler.handler(_event(body={"question": "What is the MST senior fare?"}))
        assert response["statusCode"] == 200

    def test_malformed_body_does_not_consume_quota(self, limiter):
        fake = limiter()
        response = web_handler.handler(_event(body={"not_a_question": True}))
        assert response["statusCode"] == 400
        assert fake.updates == []

    def test_page_loads_are_not_counted(self, limiter):
        fake = limiter()
        for _ in range(5):
            web_handler.handler(_event(method="GET", path="/"))
        assert fake.updates == []


class TestSpendCutoffDegradesToTheOfflineRoutes:
    def test_ask_returns_503_pointing_at_offline_and_guide(self, limiter, caplog):
        limiter(breaker=True)
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(_event(body={"question": "What is the MST youth fare?"}))
        assert response["statusCode"] == 503
        body = json.loads(response["body"])
        assert body["offline"] == "/offline"
        assert body["guide"] == "/guide"
        assert _log_records(caplog, "spend_cutoff_served")[-1].route == "ask"
        record = _log_records(caplog, "answer_request")[-1]
        assert record.kind == "spend_cutoff"
        assert record.status_code == 503
        assert record.model_called is False

    def test_no_model_is_called_while_cut_off(self, limiter, caplog):
        limiter(breaker=True)
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            web_handler.handler(_event(body={"question": "What is the SBMTD senior fare?"}))
        assert not _log_records(caplog, "genai_call")

    def test_offline_and_guide_still_render(self, limiter):
        limiter(breaker=True)
        for path in ("/", "/offline", "/guide", "/embed"):
            response = web_handler.handler(_event(method="GET", path=path))
            assert response["statusCode"] == 200, path

    def test_version_route_still_answers(self, limiter):
        limiter(breaker=True)
        assert web_handler.handler(_event(method="GET", path="/version"))["statusCode"] == 200

    def test_already_cached_answers_are_still_served(self, limiter):
        fake = limiter()
        question = "What proof do I need for the MST veteran fare?"
        assert web_handler.handler(_event(body={"question": question}))["statusCode"] == 200
        fake.breaker = True
        ratelimit.reset_for_tests()
        # reset_for_tests drops the injected client along with the cache.
        ratelimit._client = fake
        response = web_handler.handler(_event(body={"question": question}))
        assert response["statusCode"] == 200, "a paid-for answer should survive the cutoff"

    def test_feedback_still_works_while_cut_off(self, limiter):
        limiter(breaker=True)
        response = web_handler.handler(_event(path="/api/feedback", body={"verdict": "up"}))
        assert response["statusCode"] == 200


class TestNoIdentifierReachesTheLogs:
    def test_no_record_carries_an_address_or_a_digest(self, limiter, caplog):
        limiter()
        with caplog.at_level(logging.DEBUG, logger="fare_assistant"):
            for i in range(config.RATE_LIMIT_ASK_PER_WINDOW + 2):
                web_handler.handler(_event(body={"question": f"An MST fare question {i}?"}))
            web_handler.handler(_event(path="/api/feedback", body={"verdict": "down"}))

        digest = ratelimit.caller_digest(CALLER, "ask", __import__("time").time())
        assert caplog.records
        for record in caplog.records:
            serialized = json.dumps(
                {key: str(value) for key, value in record.__dict__.items()},
            )
            assert CALLER not in serialized
            assert digest not in serialized
            for field in ("source_ip", "sourceIp", "ip", "caller", "digest", "user_agent"):
                assert not hasattr(record, field), f"{field} must never be logged"
