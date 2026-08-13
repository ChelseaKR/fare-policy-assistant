"""The spend breaker's control-plane Lambda (ADR 0025)."""

from __future__ import annotations

import json

import pytest

from web import ratelimit, spend_breaker

TABLE = "fare-policy-assistant-demo-limits"


class FakeDynamo:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def put_item(self, **kwargs):
        self.items.append(kwargs)
        return {}

    def get_item(self, **kwargs):
        for call in reversed(self.items):
            if call["Item"]["pk"]["S"] == kwargs["Key"]["pk"]["S"]:
                return {"Item": call["Item"]}
        return {}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("FPA_RATE_LIMIT_TABLE", TABLE)
    spend_breaker.reset_for_tests()
    ratelimit.reset_for_tests()
    yield
    spend_breaker.reset_for_tests()
    ratelimit.reset_for_tests()


@pytest.fixture
def store(monkeypatch) -> FakeDynamo:
    fake = FakeDynamo()
    monkeypatch.setattr(spend_breaker, "_client", fake)
    return fake


def _sns(message: str) -> dict:
    return {"Records": [{"Sns": {"Message": message}}]}


def _alarm(state: str = "ALARM", name: str = "fare-policy-assistant-demo-model-spend-cutoff"):
    return _sns(json.dumps({"AlarmName": name, "NewStateValue": state}))


class TestTripping:
    def test_a_cloudwatch_alarm_trips_the_breaker(self, store):
        result = spend_breaker.handler(_alarm())
        assert result["tripped"] is True
        assert result["reason"] == "fare-policy-assistant-demo-model-spend-cutoff"
        item = store.items[-1]["Item"]
        assert store.items[-1]["TableName"] == TABLE
        assert item["pk"]["S"] == spend_breaker.BREAKER_KEY
        assert item["open"]["BOOL"] is True
        assert int(item["tripped_at"]["N"]) > 0

    def test_a_budget_notification_trips_the_breaker(self, store):
        result = spend_breaker.handler(_sns("AWS Budgets: fare-demo has exceeded 80%"))
        assert result["tripped"] is True
        assert result["reason"] == "budget-notification"

    def test_direct_invocation_trips_the_breaker(self, store):
        assert spend_breaker.handler({})["reason"] == "direct-invocation"
        assert store.items

    def test_an_unrecognized_message_still_trips(self, store):
        assert spend_breaker.handler(_sns(json.dumps(["unexpected"])))["tripped"] is True
        assert spend_breaker.handler({"Records": [{}]})["tripped"] is True
        assert spend_breaker.handler({"Records": [{"Sns": {"Message": 7}}]})["tripped"] is True

    def test_an_overlong_alarm_name_is_truncated(self, store):
        result = spend_breaker.handler(_alarm(name="x" * 500))
        assert len(result["reason"]) == 200

    def test_an_alarm_without_a_name_is_labelled(self, store):
        assert (
            spend_breaker.handler(_sns(json.dumps({"NewStateValue": "ALARM"})))["reason"]
            == "unknown-alarm"
        )

    def test_tripping_twice_is_idempotent(self, store):
        spend_breaker.handler(_alarm())
        spend_breaker.handler(_alarm())
        assert all(call["Item"]["pk"]["S"] == spend_breaker.BREAKER_KEY for call in store.items)


class TestItNeverClearsItself:
    def test_an_alarm_returning_to_ok_writes_nothing(self, store):
        result = spend_breaker.handler(_alarm(state="OK"))
        assert result == {"tripped": False, "reason": None}
        assert store.items == []

    def test_ok_after_a_trip_leaves_the_breaker_open(self, store):
        spend_breaker.handler(_alarm())
        spend_breaker.handler(_alarm(state="OK"))
        assert store.items[-1]["Item"]["open"]["BOOL"] is True


class TestTheRiderSeesWhatTheBreakerWrote:
    """The two halves agree on the row: control plane writes, rider reads."""

    def test_written_row_reads_back_as_open(self, store, monkeypatch):
        monkeypatch.setenv("FPA_RATE_LIMIT_TABLE", TABLE)
        monkeypatch.setattr(ratelimit, "_client", store)
        assert ratelimit.breaker_open(now=1.0) is False

        spend_breaker.handler(_alarm())
        ratelimit.reset_for_tests()
        monkeypatch.setattr(ratelimit, "_client", store)
        assert ratelimit.breaker_open(now=1.0) is True

    def test_the_two_modules_use_the_same_key(self):
        assert spend_breaker.BREAKER_KEY == ratelimit.BREAKER_KEY
