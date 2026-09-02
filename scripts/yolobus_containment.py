"""Decide, from a build's own corpus, whether it must still contain `yolobus-fares`.

Issue #164. `yolobus-fares` was contained by default from 2026-07 because the
committed fare table had already expired on 2026-06-30, and serving an expired
fare is worse than serving nothing. The replacement source ("All below fares are
effective July 1, 2026 - June 30, 2027", fetched 2026-08-21) is current and
evaluated, so the *forward* containment is lifted in `infra/deploy.sh`.

`infra/rollback.sh` cannot simply drop its own copy of that default. A rollback
moves the rider-facing alias to an **older** Lambda version, and an older version
carries an older corpus — possibly one that still holds the expired table. The
one combination that must stay impossible is "expired snapshot, no containment",
and deleting the requirement would make it reachable the first time a rollback
target predated the refresh. Production's own pin at the time of writing,
`35ec70d6359d`, is exactly such a corpus.

So the requirement is derived rather than deleted or hard-coded. Every corpus the
project has published is archived under `corpus/versions/<corpus_version>/`, and
the fare period is stated in the document's own text, so the question "does this
specific build serve an expired Yolobus fare table?" is answerable offline from
the checkout, for any version, without a network call, a model call, or git
history (which CI's shallow checkout would not have anyway).

**Every unresolvable case requires containment.** A missing archive, an
unparseable period, a corpus id that is not a corpus id: none of them are
evidence that the build is safe, and this module never reports "not required"
except from a fare period it actually read and found unexpired. An operator who
knows better can still override with `FPA_REQUIRED_DISABLED_DOC_IDS`, which is
an explicit, logged decision rather than a silent default.

    python3 scripts/yolobus_containment.py 35ec70d6359d   # -> "yolobus-fares"
    python3 scripts/yolobus_containment.py 3dd8b7bd757e   # -> ""

Standard library only, and invoked as `python3` rather than through `uv`: this
runs on the rollback path, during an incident, where "resolve and sync a virtual
environment first" is a dependency worth not having.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

CONTAINED_DOC_ID = "yolobus-fares"

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_ROOT = REPO_ROOT / "corpus" / "versions"

CORPUS_VERSION_PATTERN = re.compile(r"^[0-9a-f]{12}$")

# "All below fares are effective July 1, 2025 – June 30, 2026 ." — the closed
# range is what can expire. An en dash, em dash or hyphen all appear across the
# snapshots this has to read, and the day may or may not carry a comma.
FARE_PERIOD_PATTERN = re.compile(
    r"effective\s+"
    r"(?P<start>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"
    r"\s*[-‐-―]\s*"
    r"(?P<end>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContainmentVerdict:
    """Whether `yolobus-fares` must stay contained, and the evidence for it.

    `reason` is not decoration. A containment decision that cannot say which
    corpus it read and what fare period it found is the same shape of defect as
    the containment marker issue #145 fixed: a check that reports a verdict it
    did not actually measure.
    """

    required: bool
    reason: str

    @property
    def required_disabled_doc_ids(self) -> str:
        return CONTAINED_DOC_ID if self.required else ""


def _parse_period_end(text: str) -> date | None:
    """The latest closed fare-period end date stated in `text`, if any."""

    ends: list[date] = []
    for match in FARE_PERIOD_PATTERN.finditer(text):
        raw = re.sub(r"\s+", " ", match.group("end")).replace(",", "").strip()
        try:
            ends.append(datetime.strptime(raw, "%B %d %Y").date())
        except ValueError:
            continue
    return max(ends) if ends else None


def fare_period_end(chunks: list[dict[str, object]]) -> date | None:
    """The end of the Yolobus fare period stated in an archived corpus.

    `None` means the corpus has `yolobus-fares` chunks but none of them state a
    closed period — an open-ended "effective July 1, 2026" with no end, or a
    wording this parser does not recognise. Callers must treat that as
    unresolved, not as unexpired: the two are indistinguishable from here.
    """

    text = " ".join(
        str(chunk.get("text", "")) for chunk in chunks if chunk.get("doc_id") == CONTAINED_DOC_ID
    )
    return _parse_period_end(text) if text.strip() else None


def _archived_chunks(corpus_version: str, versions_root: Path) -> list[dict[str, object]] | None:
    path = versions_root / corpus_version / "chunks.jsonl"
    if not path.is_file():
        return None
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def verdict_for_corpus_version(
    corpus_version: str,
    *,
    versions_root: Path = VERSIONS_ROOT,
    today: date | None = None,
) -> ContainmentVerdict:
    """Whether a build pinned to `corpus_version` must contain `yolobus-fares`."""

    today = today or datetime.now(UTC).date()

    if not CORPUS_VERSION_PATTERN.match(corpus_version):
        return ContainmentVerdict(
            True,
            f"corpus version {corpus_version!r} is not a 12-character corpus identity, "
            "so the fare period it serves cannot be read",
        )

    chunks = _archived_chunks(corpus_version, versions_root)
    if chunks is None:
        return ContainmentVerdict(
            True,
            f"corpus {corpus_version} is not archived under {versions_root}, so the fare "
            "period it serves cannot be read",
        )

    if not any(chunk.get("doc_id") == CONTAINED_DOC_ID for chunk in chunks):
        return ContainmentVerdict(
            False,
            f"corpus {corpus_version} has no {CONTAINED_DOC_ID} document to contain",
        )

    end = fare_period_end(chunks)
    if end is None:
        return ContainmentVerdict(
            True,
            f"corpus {corpus_version} states no closed fare period for {CONTAINED_DOC_ID}, "
            "so it cannot be shown to be unexpired",
        )
    if end < today:
        return ContainmentVerdict(
            True,
            f"corpus {corpus_version} serves a {CONTAINED_DOC_ID} fare period that ended "
            f"{end.isoformat()}, before {today.isoformat()}",
        )
    return ContainmentVerdict(
        False,
        f"corpus {corpus_version} serves a {CONTAINED_DOC_ID} fare period running through "
        f"{end.isoformat()}, on or after {today.isoformat()}",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <corpus-version>", file=sys.stderr)
        return 2
    verdict = verdict_for_corpus_version(argv[1])
    print(verdict.reason, file=sys.stderr)
    print(verdict.required_disabled_doc_ids)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(main(sys.argv))
