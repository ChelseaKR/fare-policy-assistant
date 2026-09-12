"""Turn the demo stack's CloudWatch alarm state into a redacted GitHub issue.

## What was wrong

`infra/deploy.sh` creates the SNS topic `<function>-alerts`, points six alarms
at it (handler errors, Lambda errors, throttles, p99 latency, unpriced model
completions, and a Bedrock call surge), and then does this:

    if [[ "$CONFIRMED_SUBSCRIPTIONS" == "0" ]]; then
      echo "WARNING: ... alarms are configured but cannot page an operator" >&2
    fi

A warning on stderr, during a deploy, once. Measured against the live account
on 2026-09-12, `fare-policy-assistant-demo-alerts` has zero subscriptions --
confirmed or pending -- and has had since it was created. The warning has been
correct and unread the whole time. `infra/deploy-cutoff.sh` makes it sharper:
the spend breaker's "page a human" leg publishes to this same topic, so the
half of the cost control that is supposed to tell somebody has never worked.

The six alarms were deleted in the August cost triage precisely because they
notified nobody, which means the current state is worse than it looks: the next
`deploy.sh` recreates all six, pointed at the same silent topic, and the
warning scrolls past again.

## What this is

The same answer the rest of the portfolio is converging on: report into a
GitHub issue. It needs no address, it cannot be unsubscribed from by accident,
and it is durable and numbered rather than a line in a deploy log.

It reads two AWS responses captured by `.github/workflows/alarm-relay.yml` --
`cloudwatch describe-alarms` and `sns get-topic-attributes` -- and creates or
changes nothing.

## What it will not print

This repository is public and its issues are world-readable, and the service it
watches answers questions from members of the public about their own fares. The
report is ASSEMBLED, not forwarded: fixed prose, integers, alarm names that
matched a strict shape, and a pointer to the log group. Never an alarm's
`StateReason` (which quotes metric values), never an ARN (which carries the AWS
account id), never anything a caller supplied.

`assert_no_identifiers` re-reads the finished report and refuses to emit it if
anything identifier-shaped survived. That is deliberately redundant with
`safe_alarm_name`: the allowlist is the guarantee and the scan is the proof.
`tests/test_alarm_relay.py` sabotages the allowlist to show the scan catches
what it claims to.

## Three states, not two

Zero alarms matching the prefix is not "nothing is firing". It is the state
this stack is in RIGHT NOW, because the alarms were deleted -- and a check that
read that as quiet would be the exact defect being fixed, one layer up. It is
reported as its own finding.

Exit codes:

    0  read cleanly, nothing to report
    1  read cleanly, there is a finding (the report says what)
    2  could not read: the input was missing or malformed

The workflow does not trust the exit code alone: a crashed script also exits
non-zero, and an issue opened from a crash would assert a finding on no
evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Shown in place of an alarm name that did not match the expected shape.
REDACTED = "(name withheld: did not match the expected alarm-name shape)"

#: The only alarm names allowed through verbatim.
#:
#: `infra/deploy.sh` names every alarm `"$FN-$1"` from literals in the script,
#: e.g. `fare-policy-assistant-demo-handler-errors`. Lowercase words joined by
#: single hyphens covers all six and admits nothing a person ever typed. An
#: alarm created by hand in the console -- which can be named anything -- does
#: not match, and is withheld.
SAFE_ALARM_NAME = re.compile(r"^fare-[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Patterns that must never appear in a report this module emits. Not the
#: redaction mechanism (`safe_alarm_name` is) -- the assertion that it worked.
IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("a 32-or-more-character hex literal", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    (
        "a UUID",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    ("a 12-digit run (an AWS account id is 12 digits)", re.compile(r"\b\d{12}\b")),
    ("an ARN", re.compile(r"arn:aws[a-z-]*:")),
    # The service answers questions typed by members of the public. A request
    # id is the handle those turns are logged under, and a report that only
    # needs counts has no reason to carry one.
    ("a request-id-shaped token", re.compile(r"\breq[_-][A-Za-z0-9]{6,}\b")),
)


class Unreadable(Exception):
    """The AWS response was missing or malformed: exit 2, report nothing."""


class WouldLeak(Exception):
    """The finished report contained something identifier-shaped."""


def safe_alarm_name(name: object) -> str:
    """Return `name` if it matched the allowlist, otherwise `REDACTED`."""
    if isinstance(name, str) and SAFE_ALARM_NAME.match(name):
        return name
    return REDACTED


def assert_no_identifiers(text: str, where: str = "the report") -> None:
    """Raise `WouldLeak` if `text` contains anything identifier-shaped."""
    for label, pattern in IDENTIFIER_PATTERNS:
        hit = pattern.search(text)
        if hit:
            raise WouldLeak(
                f"refusing to publish {where}: it contains {label} "
                f"(matched {hit.group(0)!r}). This repository is public. Widen "
                "safe_alarm_name only if the new shape is provably "
                "deploy-script-controlled; never widen assert_no_identifiers to "
                "make a report pass."
            )


def build_report(
    alarms: list[dict[str, Any]],
    topic_name: str,
    confirmed: int,
    pending: int,
    *,
    sanitize: Callable[[object], str] = safe_alarm_name,
) -> tuple[bool, str]:
    """Build the issue body. Returns `(there_is_a_finding, markdown)`.

    `sanitize` exists so the test can disable the allowlist and prove the final
    scan still refuses. Production callers do not pass it.
    """
    examined = len(alarms)
    alarming = sorted(
        sanitize(a.get("AlarmName")) for a in alarms if a.get("StateValue") == "ALARM"
    )
    ok = sum(1 for a in alarms if a.get("StateValue") == "OK")
    insufficient = sum(1 for a in alarms if a.get("StateValue") == "INSUFFICIENT_DATA")

    no_alarms_found = examined == 0
    no_destination = confirmed == 0
    finding = no_alarms_found or no_destination or bool(alarming)

    state = " ".join(
        (
            f"examined={examined}",
            f"alarming={len(alarming)}",
            f"confirmed_subscribers={confirmed}",
            "names="
            + (
                "|".join("withheld" if n == REDACTED else n for n in alarming)
                if alarming
                else "none"
            ),
        )
    )

    out: list[str] = []
    out.append(
        "Filed by `.github/workflows/alarm-relay.yml`, which reads the demo "
        "stack's CloudWatch alarm state directly. It reports whether or not "
        "anything is subscribed to the alerts topic -- that independence is the "
        "point of it."
    )
    out.append("")
    out.append(
        "This issue is updated in place, never duplicated, and never closed by "
        "the workflow. A person closes it, having looked."
    )
    out.append("")
    out.append("## Counts")
    out.append("")
    out.append(f"- Alarms examined: **{examined}**")
    out.append(f"- In `ALARM`: **{len(alarming)}**")
    out.append(f"- In `OK`: **{ok}**")
    out.append(f"- In `INSUFFICIENT_DATA`: **{insufficient}**")
    out.append(f"- Confirmed subscribers on the alerts topic: **{confirmed}**")
    out.append(f"- Pending (unconfirmed) subscriptions on that topic: **{pending}**")
    out.append("")

    if no_alarms_found:
        out.append("## No alarm matched this stack's prefix")
        out.append("")
        out.append(
            "`infra/deploy.sh` creates six alarms (`handler-errors`, "
            "`lambda-errors`, `lambda-throttles`, `unpriced-model-calls`, "
            "`latency-p99`, `bedrock-surge`) and `infra/deploy-cutoff.sh` adds "
            "the spend-cutoff alarm. Finding none of them means either they "
            "were deleted -- which is what happened in the August cost triage, "
            "on the grounds that they notified nobody -- or that this query "
            "could not see them: wrong credentials, wrong account, wrong "
            "region. In every one of those cases nothing is watching the "
            "service, which is not the same as nothing being wrong with it. "
            "Re-running `infra/deploy.sh` recreates the six."
        )
        out.append("")

    if no_destination:
        out.append("## The alerts topic has no confirmed subscriber")
        out.append("")
        out.append(
            f"SNS topic `{topic_name}` reports **{confirmed}** confirmed "
            + (
                f"subscriptions ({pending} pending confirmation). "
                if pending
                else "subscriptions. "
            )
            + "Every alarm `infra/deploy.sh` creates publishes there, and so "
            "does the *page a human* leg of the spend breaker in "
            "`infra/deploy-cutoff.sh` -- so on the SNS side, none of them can "
            "reach anyone. `deploy.sh` prints a warning about exactly this on "
            "every deploy; this issue exists because a warning on stderr during "
            "a deploy is not a channel. Subscribing an endpoint needs no code "
            "change, and the two paths fail independently: adding one does not "
            "make this issue redundant."
        )
        out.append("")

    if alarming:
        out.append("## Alarms currently in `ALARM`")
        out.append("")
        out.extend(f"- `{name}`" for name in alarming)
        out.append("")
        out.append(
            "Names only. An alarm's `StateReason` quotes metric values and "
            "dimensions and is deliberately not reproduced here."
        )
        out.append("")

    out.append("## Where the detail is")
    out.append("")
    out.append("- Lambda log group: `/aws/lambda/fare-policy-assistant-demo`")
    out.append(
        "- What each alarm means, and the metric it reads, is in the "
        "`observability` section of `infra/deploy.sh`; the dashboard it also "
        "creates is named `fare-policy-assistant-demo`."
    )
    out.append("")
    out.append(f"<!-- alarm-relay-verdict: {'report' if finding else 'clear'} -->")
    out.append(f"<!-- alarm-relay-state: {state} -->")

    body = "\n".join(out)
    assert_no_identifiers(body)
    return finding, body


def _load(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unreadable(f"could not read {what} from {path}: {exc}") from exc


def run(alarms_path: Path, topic_path: Path, out_path: Path) -> int:
    alarms_doc = _load(alarms_path, "the describe-alarms response")
    topic_doc = _load(topic_path, "the get-topic-attributes response")

    # A missing key is a malformed response, which is a different thing from an
    # empty list and must not decay into one.
    metric_alarms = alarms_doc.get("MetricAlarms") if isinstance(alarms_doc, dict) else None
    if not isinstance(metric_alarms, list):
        raise Unreadable("describe-alarms response carries no MetricAlarms list")

    attributes = topic_doc.get("Attributes") if isinstance(topic_doc, dict) else None
    if not isinstance(attributes, dict) or not isinstance(attributes.get("TopicArn"), str):
        raise Unreadable("get-topic-attributes response carries no Attributes.TopicArn")

    # The ARN is read to derive the topic NAME and then dropped: it carries the
    # AWS account id, and this report is published publicly.
    topic_name = attributes["TopicArn"].rsplit(":", 1)[-1]
    if safe_alarm_name(topic_name) == REDACTED:
        topic_name = REDACTED

    finding, body = build_report(
        metric_alarms,
        topic_name,
        int(attributes.get("SubscriptionsConfirmed", 0)),
        int(attributes.get("SubscriptionsPending", 0)),
    )
    out_path.write_text(body + "\n", encoding="utf-8")
    return 1 if finding else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relay CloudWatch alarm state to an issue.")
    parser.add_argument("--alarms", type=Path, required=True)
    parser.add_argument("--topic", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.alarms, args.topic, args.out)
    except (Unreadable, WouldLeak) as exc:
        # Loud, not quiet. A relay that cannot report must not exit 0 with
        # nothing to say; that is the defect it exists to remove, one layer up.
        print(f"alarm-relay: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised by the workflow
    raise SystemExit(main())
