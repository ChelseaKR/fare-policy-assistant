"""Per-caller request limiting and the spend circuit breaker (ADR 0025).

Two controls share one small DynamoDB table, because both need state that
outlives a single Lambda container and neither justifies a second service.

**Per-caller limiting.** The gateway throttle is an *aggregate* ceiling: one
actor sustaining it starves every real rider at no cost to itself. This module
adds a per-caller fixed-window counter so a single source cannot consume the
whole allowance. Quotas live in ``assistant.config`` as reviewed release inputs.

**Spend breaker.** A tripped breaker stops new model calls while leaving every
non-model route working, so the service degrades to ``/offline`` and ``/guide``
rather than to an error page.

Privacy (the part to read before editing anything here). This service logs no
IP address, user agent, or question text, and that property is load-bearing --
see ADR 0019 and docs/dpia.md. Per-caller limiting cannot be done without
*some* per-caller signal, so this module handles the one it needs under strict
rules:

1. The source IP is read from the request context, passed straight into an HMAC,
   and dropped. It is never logged, never returned, never stored, and never
   held in a variable that outlives the call.
2. What is stored is ``HMAC-SHA256(secret, schema || window || route || ip)``
   truncated to 128 bits, plus an integer count. The secret lives only in the
   function's environment, so an attacker holding a dump of the table cannot
   walk the 2^32 IPv4 space to recover an address -- unlike a stored /24
   truncation, which is trivially reversible to a network.
3. The window index is part of the digest material, so the digest rotates every
   ``RATE_LIMIT_WINDOW_SECONDS``. Two windows of the same rider produce
   unrelated digests and cannot be linked to each other or into a session.
4. Items carry a TTL of one window plus slack, so the row is gone in about two
   minutes even if nothing ever reads it again.
5. The digest is never logged either. Telemetry records that *a* caller was
   limited on *a* route, with no key of any kind, so CloudWatch gains no
   pseudonymous identifier to correlate on.

This is still a real reduction in the "we persist nothing derived from a
request" property that ADR 0004 twice chose to keep. It is not hidden: ADR 0025
records the trade and docs/dpia.md carries it as processing.

Failure is open, by design. If the table is unreachable, misconfigured, or
absent, requests are admitted and the condition is logged. Failing closed would
convert a DynamoDB blip into a rider-facing outage, and the controls that
bounded spend before this module existed -- gateway throttle, reserved
concurrency, the in-process budget -- are all still in force underneath it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

from assistant import config, telemetry

# Well-known partition key of the breaker row. It is a fixed string, not a
# digest, so an operator can read and write it by hand from the CLI.
BREAKER_KEY = "spend-breaker"

# Extra seconds added to a counter's TTL beyond the end of its window, so a
# request arriving at the very edge of a window still has a live row. DynamoDB
# deletes expired items on its own schedule (typically within 48 hours), which
# is why the digest rotation above, not the TTL, is what bounds linkability.
_TTL_SLACK_SECONDS = 60

_client: Any = None
# Cached breaker state: (monotonic deadline, open?). Re-read after the deadline.
_breaker_cache: tuple[float, bool] | None = None


@dataclass(frozen=True)
class Decision:
    """The outcome of one per-caller check.

    ``counted`` distinguishes "under quota" from "the limiter did not run at
    all" (no table configured, no source IP, or a backend failure). Callers use
    it only for telemetry; an uncounted request is always allowed.
    """

    allowed: bool
    counted: bool


_ALLOWED_UNCOUNTED = Decision(allowed=True, counted=False)


def _table_name() -> str:
    """Read at call time so tests and an operator toggle need no redeploy."""
    return os.environ.get("FPA_RATE_LIMIT_TABLE", "").strip()


def _secret() -> str:
    return os.environ.get("FPA_RATE_LIMIT_HMAC_KEY", "")


def _dynamodb() -> Any:
    """Build the client once per container, on first use.

    Import boto3 lazily: the offline test suite, the eval harness, and every
    non-Lambda entry point import this module transitively but never reach a
    request path, and none of them should pay for a client they do not use.
    """
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION"))
    return _client


def reset_for_tests() -> None:
    """Drop the cached client and breaker state (tests and key rotation)."""
    global _client, _breaker_cache
    _client = None
    _breaker_cache = None


def window_index(now: float) -> int:
    """The fixed window a timestamp falls in. Also the rotating digest salt."""
    return int(now // config.RATE_LIMIT_WINDOW_SECONDS)


def caller_digest(source_ip: str, route: str, now: float) -> str:
    """Return an opaque, window-scoped, secret-keyed digest of one caller.

    The caller's address enters here and does not leave. Including the window
    index means the same address yields a different digest each window; including
    the route means the ask and feedback quotas cannot be spent against each
    other. Truncation to 128 bits keeps the stored key small while leaving no
    useful collision surface at this traffic.
    """
    material = "\0".join(
        (config.CALLER_DIGEST_SCHEMA, str(window_index(now)), route, source_ip)
    ).encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), material, hashlib.sha256).hexdigest()[:32]


def source_ip(event: dict) -> str:
    """Extract the caller address from a payload-v2 event, or "" if absent.

    Absent means a direct Lambda invocation (the deploy's health check) or a
    local test, both of which are exempt from per-caller limiting. This reads
    only the gateway-populated ``requestContext``; a client-supplied
    ``X-Forwarded-For`` header is deliberately never consulted, since trusting
    it would let a caller mint a fresh identity per request.
    """
    context = event.get("requestContext")
    if not isinstance(context, dict):
        return ""
    http = context.get("http")
    if not isinstance(http, dict):
        return ""
    value = http.get("sourceIp")
    return value if isinstance(value, str) else ""


def check(event: dict, *, route: str, limit: int, now: float | None = None) -> Decision:
    """Count this caller's request in the current window and decide.

    One conditional-free ``UpdateItem`` does the whole job atomically: it
    increments the window counter, sets the TTL if this is the first request of
    the window, and returns the new count. There is no read-then-write race
    because there is no read.
    """
    table = _table_name()
    address = source_ip(event)
    if not table or not address or not _secret():
        return _ALLOWED_UNCOUNTED

    moment = time.time() if now is None else now
    key = caller_digest(address, route, moment)
    del address  # the caller's address must not outlive the digest
    expires_at = (window_index(moment) + 1) * config.RATE_LIMIT_WINDOW_SECONDS + _TTL_SLACK_SECONDS

    try:
        response = _dynamodb().update_item(
            TableName=table,
            Key={"pk": {"S": key}},
            UpdateExpression="SET expires_at = if_not_exists(expires_at, :ttl) ADD n :one",
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":ttl": {"N": str(expires_at)},
            },
            ReturnValues="UPDATED_NEW",
        )
        count = int(response["Attributes"]["n"]["N"])
    except Exception as exc:  # fail open; never block a rider on a backend fault
        telemetry.log_rate_limit_unavailable(route=route, error_type=type(exc).__name__)
        return _ALLOWED_UNCOUNTED

    return Decision(allowed=count <= limit, counted=True)


def breaker_open(now: float | None = None) -> bool:
    """Whether the spend breaker is tripped, cached briefly per container.

    Read failures resolve to "not tripped" for the same reason ``check`` fails
    open: an unreachable table must not take the assistant down. The breaker is
    a cost control, and the controls that bound worst-case spend without it are
    unchanged.
    """
    global _breaker_cache
    table = _table_name()
    if not table:
        return False

    moment = time.monotonic() if now is None else now
    if _breaker_cache is not None and moment < _breaker_cache[0]:
        return _breaker_cache[1]

    try:
        response = _dynamodb().get_item(
            TableName=table,
            Key={"pk": {"S": BREAKER_KEY}},
            ConsistentRead=False,
        )
        item = response.get("Item") or {}
        tripped = item.get("open", {}).get("BOOL", False) is True
    except Exception as exc:
        telemetry.log_rate_limit_unavailable(route="spend_breaker", error_type=type(exc).__name__)
        tripped = False

    _breaker_cache = (moment + config.SPEND_BREAKER_CACHE_SECONDS, tripped)
    return tripped
