"""The spend circuit breaker's control plane: a second, tiny Lambda (ADR 0025).

Deployed by ``infra/deploy-cutoff.sh``, not by the rider deploy. It subscribes
to one SNS topic and does exactly one thing: write the breaker row that
``web/ratelimit.py`` reads, so the rider function stops making new model calls
and degrades to ``/offline`` and ``/guide``.

Why this exists rather than an AWS Budgets action. A Budgets action can apply an
IAM policy, apply an SCP, or stop EC2/RDS instances -- that enum is closed, and
none of it can set a Lambda's reserved concurrency or flip a flag. Budgets data
also lags actual usage by 8-12 hours, so a budget alone cannot stop a runaway;
it can only describe one after the fact. The fast path into this function is
therefore a CloudWatch alarm on the rider's own token-derived cost metric, which
lands within minutes. The tag-scoped budget still points here as the
billing-authoritative second opinion.

Two deliberate non-behaviours:

*It never resets itself.* Subscribing to alarm-OK transitions would let spend
resume the moment a five-minute window looked quiet, which is not a cutoff. An
operator clears the breaker by hand, after looking (infra/README.md).

*It never touches reserved concurrency.* Setting concurrency to zero would take
down the static page, the offline reference, and the guided finder along with
the model calls -- turning a cost event into a rider-facing outage. Stopping the
paid path while the free paths keep serving is the whole point. Concurrency zero
remains the documented last resort for a human.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# Must match web.ratelimit.BREAKER_KEY. Not imported from it: this function is
# deployed as a standalone one-file bundle with no src/ on its path.
BREAKER_KEY = "spend-breaker"

# Bounded allowlist for the recorded reason. The SNS payload is written by AWS,
# not by a rider, but this function still stores only a short label rather than
# an arbitrary message body.
_MAX_REASON_CHARS = 200

_client: Any = None


def _dynamodb() -> Any:
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION"))
    return _client


def reset_for_tests() -> None:
    global _client
    _client = None


def _reason(event: dict) -> str | None:
    """Return a short label for what tripped this, or None to ignore the event.

    Handles the two shapes that reach the topic: a CloudWatch alarm
    notification, whose ``Message`` is JSON, and an AWS Budgets notification,
    whose ``Message`` is prose. Anything else trips the breaker anyway under a
    generic label -- an unrecognized message on this topic is not a reason to
    keep spending.
    """
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return "direct-invocation"

    sns = records[0].get("Sns") if isinstance(records[0], dict) else None
    message = sns.get("Message") if isinstance(sns, dict) else None
    if not isinstance(message, str):
        return "unknown-notification"

    try:
        parsed = json.loads(message)
    except ValueError:
        return "budget-notification"

    if not isinstance(parsed, dict):
        return "unknown-notification"
    # A CloudWatch alarm returning to OK must not clear the breaker. Only alarm
    # actions are wired to this topic, so this is belt and braces.
    if parsed.get("NewStateValue") == "OK":
        return None
    name = parsed.get("AlarmName")
    return str(name)[:_MAX_REASON_CHARS] if isinstance(name, str) else "unknown-alarm"


def handler(event: dict, context: object = None) -> dict:
    """Trip the breaker. Idempotent: re-tripping only refreshes the reason."""
    reason = _reason(event if isinstance(event, dict) else {})
    if reason is None:
        print(json.dumps({"event": "spend_breaker_ignored", "state": "OK"}))
        return {"tripped": False, "reason": None}

    table = os.environ["FPA_RATE_LIMIT_TABLE"]
    _dynamodb().put_item(
        TableName=table,
        Item={
            "pk": {"S": BREAKER_KEY},
            "open": {"BOOL": True},
            "tripped_at": {"N": str(int(time.time()))},
            "reason": {"S": reason},
        },
    )
    # Plain print, not the rider telemetry module: this function ships without
    # the assistant package. The record carries no rider-derived data.
    print(json.dumps({"event": "spend_breaker_tripped", "reason": reason}))
    return {"tripped": True, "reason": reason}
