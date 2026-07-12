"""Structured fare table: the agency's GTFS-Fares feed as the source of truth
for fare *numbers*.

The hardest, most rider-dangerous failure this project has is a misread fare —
a real number read from the wrong row of a prose table (ground-024,
conv-forged-002). ADR 0016 showed that catching that with a prose heuristic is
infeasible (15:1+ false positives). The durable fix is architectural: stop
having the model read fare amounts out of prose at all, and take them from the
machine-readable GTFS-Fares feed the agency already publishes, where an amount
is bound to a typed rider category (`standard` $2.50, `reduced` $1.25, `free`
$0.00), not a table cell the model has to parse.

This module turns the snapshotted feed (`assistant.gtfs.parse_fares`) into a
typed, queryable `StructuredFare` list plus a rider-category lookup, and renders
an authoritative fare card. Step one of "numbers from typed data" (ADR 0017);
wiring the card into the answer prompt and a structured consistency check are
the next increments.

    python -m assistant.fare_table SBMTD    # print the agency's authoritative fares
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal

from assistant import gtfs


@dataclass(frozen=True)
class RiderCategory:
    id: str
    name: str
    eligibility_url: str | None


@dataclass(frozen=True)
class StructuredFare:
    agency: str
    product: str
    amount: Decimal
    rider_category: RiderCategory | None

    @property
    def category_label(self) -> str:
        return self.rider_category.name if self.rider_category else "All riders"


def load_rider_categories(agency: str) -> dict[str, RiderCategory]:
    """Rider categories declared in a v2 feed (`rider_categories.txt`), keyed by
    id. Empty for a v1 feed or an agency with no snapshot — the fares then carry
    no typed category, which callers handle as `None`."""
    path = gtfs.GTFS_RAW_DIR / agency / "rider_categories.txt"
    if not path.exists():
        return {}
    out: dict[str, RiderCategory] = {}
    for row in gtfs._read_csv(path):
        cid = row.get("rider_category_id")
        if not cid:
            continue
        out[cid] = RiderCategory(
            id=cid,
            name=(row.get("rider_category_name") or cid).strip(),
            eligibility_url=(row.get("eligibility_url") or "").strip() or None,
        )
    return out


def structured_fares(agency: str) -> list[StructuredFare]:
    """Every fare in the agency's feed as a typed row, with its rider category
    resolved to a label. Empty when the agency has no snapshotted feed."""
    categories = load_rider_categories(agency)
    fares: list[StructuredFare] = []
    for feed in gtfs.parse_fares(agency):
        fares.append(
            StructuredFare(
                agency=feed.agency,
                product=feed.name,
                amount=feed.amount,
                rider_category=categories.get(feed.rider_category or ""),
            )
        )
    return fares


def render_fare_card(agency: str) -> str:
    """The authoritative fare list for the agency, sourced from the feed — the
    block a future increment injects into the answer prompt so the model states
    numbers it did not have to parse from a table. Empty string when there is no
    feed, so the caller falls back to today's prose-only behavior."""
    fares = structured_fares(agency)
    if not fares:
        return ""
    lines = [f"Authoritative fares for {agency} (from the agency's GTFS-Fares feed):"]
    for fare in fares:
        lines.append(f"  - {fare.product} [{fare.category_label}]: ${fare.amount:.2f}")
    urls = sorted(
        {
            f.rider_category.eligibility_url
            for f in fares
            if f.rider_category and f.rider_category.eligibility_url
        }
    )
    for url in urls:
        lines.append(f"  Eligibility: {url}")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m assistant.fare_table <AGENCY>", file=sys.stderr)
        return 2
    card = render_fare_card(sys.argv[1])
    if not card:
        print(f"no GTFS-Fares snapshot for {sys.argv[1]}", file=sys.stderr)
        return 1
    print(card)
    return 0


if __name__ == "__main__":
    sys.exit(main())
