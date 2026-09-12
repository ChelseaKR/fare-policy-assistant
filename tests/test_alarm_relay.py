"""The alarm relay, checked for the one thing it must never do.

The relay files a GitHub issue on a PUBLIC repository from data read out of a
live AWS account, about a service that answers fare questions typed by members
of the public. Everything else about it is replaceable; "an identifier can never
reach the issue body" is not.

So the leak test is the load-bearing one, and it is written so that it can go
red. The fixture is deliberately hostile -- alarm names carrying an email
address, a 32-hex literal, a UUID, an AWS account id, a request id and a full
ARN -- because a redaction test whose fixture contains nothing the redactor
could have mishandled passes forever while redacting nothing. The negative
control disables the allowlist and asserts the final scan still refuses, which
is what makes the passing case evidence rather than decoration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.alarm_relay import (
    REDACTED,
    Unreadable,
    WouldLeak,
    assert_no_identifiers,
    build_report,
    main,
    run,
    safe_alarm_name,
)

TOPIC = "fare-policy-assistant-demo-alerts"

#: The six names `infra/deploy.sh` actually creates, as `"$FN-$1"`.
REAL_ALARMS = [
    "fare-policy-assistant-demo-handler-errors",
    "fare-policy-assistant-demo-lambda-errors",
    "fare-policy-assistant-demo-lambda-throttles",
    "fare-policy-assistant-demo-unpriced-model-calls",
    "fare-policy-assistant-demo-latency-p99",
    "fare-policy-assistant-demo-bedrock-surge",
]

#: Names nobody should be able to publish. An alarm created by hand in the
#: console can be called anything, and "anything" next to a public-facing
#: question-answering service means a rider's question, an address, or a
#: request id pasted in while debugging.
HOSTILE = [
    {"AlarmName": "rider someone@example.invalid complained", "StateValue": "ALARM"},
    {"AlarmName": "turn 0123456789abcdef0123456789abcdef stuck", "StateValue": "ALARM"},
    {
        "AlarmName": "case 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed",
        "StateValue": "ALARM",
    },
    {"AlarmName": "account 000000000000 throttled", "StateValue": "ALARM"},
    {"AlarmName": "req_9fA3kd02Lm timed out", "StateValue": "ALARM"},
    {
        "AlarmName": "arn:aws:sns:us-west-2:000000000000:t backed up",
        "StateValue": "ALARM",
    },
]


def alarms(names: list[str], state: str = "OK") -> list[dict[str, str]]:
    return [{"AlarmName": n, "StateValue": state} for n in names]


def test_every_name_deploy_sh_creates_passes_the_allowlist() -> None:
    for name in REAL_ALARMS:
        assert safe_alarm_name(name) == name, name


def test_anything_not_shaped_like_a_deploy_sh_alarm_name_is_withheld() -> None:
    for alarm in HOSTILE:
        assert safe_alarm_name(alarm["AlarmName"]) == REDACTED, alarm["AlarmName"]
    # Not only the hostile ones. A name from the right stack but carrying a
    # space or an uppercase letter is outside the shape deploy.sh produces, and
    # "close enough" is how a caller-supplied string gets through.
    assert safe_alarm_name("fare-policy-assistant-demo handler errors") == REDACTED
    assert safe_alarm_name("fare-policy-assistant-demo-Handler-Errors") == REDACTED
    assert safe_alarm_name(None) == REDACTED
    assert safe_alarm_name(7) == REDACTED


def test_no_identifier_from_a_hostile_alarm_name_reaches_the_report() -> None:
    finding, body = build_report(HOSTILE, TOPIC, confirmed=1, pending=0)

    assert finding is True
    # Stated as literals rather than "no pattern matched", so a reader can see
    # exactly what was withheld.
    assert "someone@example.invalid" not in body
    assert "0123456789abcdef0123456789abcdef" not in body
    assert "3f2504e0-4f89-11d3-9a0c-0305e82c3301" not in body
    assert "000000000000" not in body
    assert "req_9fA3kd02Lm" not in body
    assert "arn:aws:" not in body

    # And the withholding is visible, not silent: six alarms were in ALARM, so
    # six lines are present, every one the placeholder. A report that simply
    # dropped the unsafe names would under-count the incident, which is its own
    # way of lying.
    assert "In `ALARM`: **6**" in body
    assert body.count(REDACTED) == 6


def test_the_guard_is_not_vacuous() -> None:
    for text in (
        "mail someone@example.invalid",
        "digest 0123456789abcdef0123456789abcdef",
        "case 3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "account 000000000000",
        "req_9fA3kd02Lm",
        "arn:aws:sns:us-west-2:000000000000:t",
    ):
        with pytest.raises(WouldLeak):
            assert_no_identifiers(text)

    # It must still pass what it exists to allow, or it is not a guard, it is a
    # refusal.
    assert_no_identifiers(f"Alarms examined: **6**\n- `{REAL_ALARMS[0]}`")


def test_negative_control_with_the_allowlist_disabled_the_scan_refuses() -> None:
    """Sabotage the redaction and prove the leak test would go red.

    The mutation is exactly the regression this file exists to catch: someone
    deciding the alarm names "are just infrastructure" and passing them
    straight through.
    """
    # Prove the mutation lands rather than silently no-opping.
    assert (lambda n: n)(HOSTILE[0]["AlarmName"]) == HOSTILE[0]["AlarmName"]

    with pytest.raises(WouldLeak, match="an email address"):
        build_report(HOSTILE, TOPIC, confirmed=1, pending=0, sanitize=lambda n: str(n))


def test_a_topic_with_no_confirmed_subscriber_is_itself_a_finding() -> None:
    """The live state on 2026-09-12: the topic exists and nobody is on it."""
    finding, body = build_report(alarms(REAL_ALARMS), TOPIC, confirmed=0, pending=0)
    assert finding is True
    assert "no confirmed subscriber" in body
    # The spend breaker's page-a-human leg lands on this same topic, and saying
    # so is the difference between "an alarm is unrouted" and "half the cost
    # control never worked".
    assert "deploy-cutoff.sh" in body
    assert "<!-- alarm-relay-verdict: report -->" in body


def test_a_pending_subscription_is_not_a_destination() -> None:
    """SNS reports an unconfirmed email subscription as pending. It delivers
    nothing, and counting it would reproduce the original defect exactly: a
    channel that looks wired and reaches nobody."""
    finding, body = build_report(alarms(REAL_ALARMS), TOPIC, confirmed=0, pending=1)
    assert finding is True
    assert "1 pending confirmation" in body


def test_an_empty_alarm_list_is_a_finding_not_quiet() -> None:
    """This is the stack's actual state: the six alarms were deleted in the
    August cost triage because they notified nobody. A relay that read that as
    "nothing is firing" would be the same defect one layer up."""
    finding, body = build_report([], TOPIC, confirmed=1, pending=0)
    assert finding is True
    assert "nothing is watching the service" in body
    assert "recreates the six" in body


def test_all_quiet_with_a_live_subscriber_reports_nothing() -> None:
    finding, body = build_report(alarms(REAL_ALARMS), TOPIC, confirmed=1, pending=0)
    assert finding is False
    assert "<!-- alarm-relay-verdict: clear -->" in body


def test_a_firing_alarm_is_named_but_its_reason_is_not() -> None:
    firing = alarms(REAL_ALARMS[:1], "ALARM") + alarms(REAL_ALARMS[1:])
    firing[0]["StateReason"] = "Threshold Crossed: 1 datapoint [3.0 (12/09/26)]"
    finding, body = build_report(firing, TOPIC, confirmed=1, pending=0)

    assert finding is True
    assert REAL_ALARMS[0] in body
    assert "Threshold Crossed" not in body
    assert "3.0" not in body


def test_the_state_marker_changes_only_when_the_state_does() -> None:
    quiet = alarms(REAL_ALARMS)
    _, first = build_report(quiet, TOPIC, confirmed=1, pending=0)
    _, again = build_report(quiet, TOPIC, confirmed=1, pending=0)
    assert first == again

    _, firing = build_report(
        alarms(REAL_ALARMS[:1], "ALARM") + alarms(REAL_ALARMS[1:]),
        TOPIC,
        confirmed=1,
        pending=0,
    )
    assert firing != first


def _write(tmp_path: Path, alarms_doc: object, topic_doc: object) -> tuple[Path, Path, Path]:
    a = tmp_path / "alarms.json"
    t = tmp_path / "topic.json"
    o = tmp_path / "report.md"
    a.write_text(json.dumps(alarms_doc), encoding="utf-8")
    t.write_text(json.dumps(topic_doc), encoding="utf-8")
    return a, t, o


def test_run_derives_the_topic_name_and_drops_the_arn(tmp_path: Path) -> None:
    a, t, o = _write(
        tmp_path,
        {"MetricAlarms": alarms(REAL_ALARMS)},
        {
            "Attributes": {
                "TopicArn": "arn:aws:sns:us-west-2:000000000000:fare-policy-assistant-demo-alerts",
                "SubscriptionsConfirmed": "0",
                "SubscriptionsPending": "0",
            }
        },
    )
    assert run(a, t, o) == 1
    body = o.read_text(encoding="utf-8")
    assert "fare-policy-assistant-demo-alerts" in body
    # The ARN was read only to derive that name. The account id it carries must
    # not survive into a published report.
    assert "000000000000" not in body
    assert "arn:aws:" not in body


def test_a_missing_key_is_unreadable_not_empty(tmp_path: Path) -> None:
    """`MetricAlarms` absent is a malformed response, and a malformed response
    must not decay into "no alarms" -- which this module treats as a finding,
    on evidence it would not actually have."""
    a, t, o = _write(tmp_path, {}, {"Attributes": {"TopicArn": "a:b:c"}})
    with pytest.raises(Unreadable, match="MetricAlarms"):
        run(a, t, o)

    a, t, o = _write(tmp_path, {"MetricAlarms": []}, {})
    with pytest.raises(Unreadable, match="TopicArn"):
        run(a, t, o)


def test_main_exits_2_on_an_unreadable_input(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    out = tmp_path / "report.md"
    code = main(["--alarms", str(missing), "--topic", str(missing), "--out", str(out)])
    assert code == 2
    assert not out.exists(), "a failed read must not leave a report behind"
