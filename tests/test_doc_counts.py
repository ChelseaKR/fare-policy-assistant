"""Numbers quoted in the docs must match the repository.

Written after finding `docs/model-card.md` claiming 216 eval cases and
`docs/procurement-brief.md` claiming "118 cases across six suites" when the
repository held 258 across nine. Both were true when written. Neither had any
way to stop being true quietly, and the procurement brief is the document
written for the reader least able to check.

The counts come from the project's own loaders, never from a private reimple-
mentation: `sensitivity.yaml` stores `pairs` of `variants` rather than `cases`,
so a naive YAML count silently reports 228 instead of 258, and a guard that
counts wrongly is worse than no guard at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from assistant import config
from evals.runner import load_suites

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _case_count() -> int:
    return sum(len(suite.get("cases") or []) for suite in load_suites())


def _suite_count() -> int:
    return len(load_suites())


def _agency_count() -> int:
    manifest = yaml.safe_load(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    return len({doc["agency"] for doc in manifest["documents"]})


def _numbers_before(text: str, noun: str) -> set[str]:
    """Every number written immediately before `noun` in this document."""
    return set(re.findall(rf"(\d+)\s+{noun}", text))


def test_model_card_case_count_matches_the_suites() -> None:
    text = (DOCS / "model-card.md").read_text(encoding="utf-8")
    claimed = _numbers_before(text, "cases")
    assert claimed, "model-card.md no longer states a case count; update this guard too"
    assert claimed == {str(_case_count())}, (
        f"docs/model-card.md claims {sorted(claimed)} cases; the suites hold {_case_count()}"
    )


def test_procurement_brief_case_count_matches_the_suites() -> None:
    text = (DOCS / "procurement-brief.md").read_text(encoding="utf-8")
    claimed = _numbers_before(text, "cases")
    assert claimed, "procurement-brief.md no longer states a case count; update this guard too"
    assert claimed == {str(_case_count())}, (
        f"docs/procurement-brief.md claims {sorted(claimed)} cases; the suites hold {_case_count()}"
    )


def test_procurement_brief_suite_count_matches_the_suites() -> None:
    text = (DOCS / "procurement-brief.md").read_text(encoding="utf-8")
    words = {
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
    }
    claimed = {
        words[word] for word in re.findall(r"([a-z]+) suites", text.lower()) if word in words
    }
    claimed |= {int(n) for n in re.findall(r"(\d+) suites", text)}
    assert claimed == {_suite_count()}, (
        f"docs/procurement-brief.md claims {sorted(claimed)} suites; there are {_suite_count()}"
    )


def test_docs_do_not_understate_the_agency_count() -> None:
    """Agency counts in prose must match the manifest.

    Four agencies were added on 2026-08-12/13 and several documents kept saying
    five, six or seven, each correct at the moment it was written.
    """
    words = {
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    actual = _agency_count()
    wrong: list[str] = []
    for name in ("model-card.md", "procurement-brief.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        for word in re.findall(r"([a-z]+) agencies", text.lower()):
            if word in words and words[word] != actual:
                wrong.append(f"{name} says '{word} agencies'")
        for digits in re.findall(r"(\d+) agencies", text):
            if int(digits) != actual:
                wrong.append(f"{name} says '{digits} agencies'")
    assert not wrong, f"{wrong}; the manifest holds {actual}"
