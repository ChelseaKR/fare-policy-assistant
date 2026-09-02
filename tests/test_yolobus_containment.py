"""The rollback containment derivation (issue #164).

`infra/rollback.sh` used to hard-code `yolobus-fares` as a required disabled
document. Lifting the deploy-side containment without replacing that hard-coded
default would have made "roll back to a build carrying the expired fare table,
with nothing containing it" reachable, so the requirement is now derived from the
target build's own archived corpus. These tests pin both directions of that
derivation against the real archive, and pin every unresolvable case as
containment-required rather than allowed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from assistant import config

sys.path.insert(0, str(config.REPO_ROOT / "scripts"))

from yolobus_containment import (  # noqa: E402  (needs the sys.path insert above)
    CONTAINED_DOC_ID,
    VERSIONS_ROOT,
    ContainmentVerdict,
    fare_period_end,
    verdict_for_corpus_version,
)

SCRIPT = config.REPO_ROOT / "scripts" / "yolobus_containment.py"

# Two corpora that really are in `corpus/versions/`. The first is the pin
# production was still serving when #164 was filed, and it carries the fare
# table that expired 2026-06-30; the second is a post-refresh corpus carrying
# the period that runs to 2027-06-30.
EXPIRED_CORPUS = "35ec70d6359d"
CURRENT_CORPUS = "3dd8b7bd757e"

TODAY = date(2026, 9, 1)


def _archived_chunks(corpus_version: str) -> list[dict[str, object]]:
    path = VERSIONS_ROOT / corpus_version / "chunks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_corpus(root: Path, corpus_version: str, chunks: list[dict[str, object]]) -> Path:
    directory = root / corpus_version
    directory.mkdir(parents=True)
    (directory / "chunks.jsonl").write_text(
        "\n".join(json.dumps(chunk) for chunk in chunks) + "\n", encoding="utf-8"
    )
    return directory


class TestFarePeriodParsing:
    def test_reads_the_period_out_of_the_real_expired_corpus(self):
        assert fare_period_end(_archived_chunks(EXPIRED_CORPUS)) == date(2026, 6, 30)

    def test_reads_the_period_out_of_the_real_refreshed_corpus(self):
        assert fare_period_end(_archived_chunks(CURRENT_CORPUS)) == date(2027, 6, 30)

    @pytest.mark.parametrize(
        "text",
        [
            "All below fares are effective July 1, 2025 – June 30, 2026 .",
            "All below fares are effective July 1, 2025 - June 30, 2026.",
            "All below fares are effective July 1 2025 — June 30 2026.",
        ],
    )
    def test_accepts_the_dash_and_comma_forms_the_snapshots_use(self, text):
        assert fare_period_end([{"doc_id": CONTAINED_DOC_ID, "text": text}]) == date(2026, 6, 30)

    def test_ignores_other_agencies_fare_periods(self):
        chunks = [
            {"doc_id": "scmtd-fares-passes", "text": "effective July 1, 2019 - June 30, 2020"},
            {"doc_id": CONTAINED_DOC_ID, "text": "effective July 1, 2026 - June 30, 2027"},
        ]
        assert fare_period_end(chunks) == date(2027, 6, 30)

    def test_an_open_ended_effective_from_is_not_a_period(self):
        chunks = [{"doc_id": CONTAINED_DOC_ID, "text": "Fares are effective July 1, 2026."}]
        assert fare_period_end(chunks) is None


class TestVerdict:
    def test_the_expired_corpus_still_requires_containment(self):
        verdict = verdict_for_corpus_version(EXPIRED_CORPUS, today=TODAY)
        assert verdict.required
        assert verdict.required_disabled_doc_ids == CONTAINED_DOC_ID
        assert "ended 2026-06-30" in verdict.reason

    def test_the_refreshed_corpus_does_not(self):
        verdict = verdict_for_corpus_version(CURRENT_CORPUS, today=TODAY)
        assert not verdict.required
        assert verdict.required_disabled_doc_ids == ""
        assert "through 2027-06-30" in verdict.reason

    def test_every_archived_corpus_resolves_to_a_reasoned_verdict(self):
        """No archived corpus falls through to "unreadable".

        The derivation is only worth having if it can actually read the corpora
        the project has published. If a future archive stops parsing, this fails
        here rather than silently refusing a rollback during an incident.
        """

        archived = sorted(p.name for p in VERSIONS_ROOT.iterdir() if (p / "chunks.jsonl").exists())
        assert archived, "no archived corpora to check"
        unreadable = [
            name
            for name in archived
            if "cannot be read" in verdict_for_corpus_version(name, today=TODAY).reason
            or "no closed fare period" in verdict_for_corpus_version(name, today=TODAY).reason
        ]
        assert unreadable == []

    def test_the_last_day_of_the_period_is_still_inside_it(self):
        verdict = verdict_for_corpus_version(EXPIRED_CORPUS, today=date(2026, 6, 30))
        assert not verdict.required

    def test_the_day_after_the_period_is_outside_it(self):
        verdict = verdict_for_corpus_version(EXPIRED_CORPUS, today=date(2026, 7, 1))
        assert verdict.required


class TestUnresolvableCasesRequireContainment:
    def test_a_corpus_that_is_not_archived_here(self, tmp_path):
        verdict = verdict_for_corpus_version("deadbeef0000", versions_root=tmp_path, today=TODAY)
        assert verdict.required
        assert "not archived" in verdict.reason

    @pytest.mark.parametrize("corpus_version", ["", "not-a-hash", "35EC70D6359D", "35ec70d6359"])
    def test_a_pin_that_is_not_a_corpus_identity(self, corpus_version, tmp_path):
        verdict = verdict_for_corpus_version(corpus_version, versions_root=tmp_path, today=TODAY)
        assert verdict.required
        assert "not a 12-character corpus identity" in verdict.reason

    def test_a_corpus_whose_fare_period_cannot_be_parsed(self, tmp_path):
        _write_corpus(
            tmp_path,
            "aaaaaaaaaaaa",
            [{"doc_id": CONTAINED_DOC_ID, "text": "Fares change from time to time."}],
        )
        verdict = verdict_for_corpus_version("aaaaaaaaaaaa", versions_root=tmp_path, today=TODAY)
        assert verdict.required
        assert "no closed fare period" in verdict.reason

    def test_a_corpus_without_the_document_has_nothing_to_contain(self, tmp_path):
        _write_corpus(
            tmp_path,
            "bbbbbbbbbbbb",
            [{"doc_id": "mst-fares", "text": "MST fares."}],
        )
        verdict = verdict_for_corpus_version("bbbbbbbbbbbb", versions_root=tmp_path, today=TODAY)
        assert not verdict.required
        assert "no yolobus-fares document to contain" in verdict.reason


class TestCommandLine:
    """`infra/rollback.sh` reads stdout and echoes stderr, so both matter."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=config.REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_prints_the_document_id_for_an_expired_corpus(self):
        result = self._run(EXPIRED_CORPUS)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == CONTAINED_DOC_ID
        assert "ended 2026-06-30" in result.stderr

    def test_prints_an_empty_list_for_a_refreshed_corpus(self):
        result = self._run(CURRENT_CORPUS)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
        assert "through 2027-06-30" in result.stderr

    def test_refuses_a_call_with_no_corpus_version(self):
        result = self._run()
        assert result.returncode == 2
        assert "usage" in result.stderr


def test_verdict_reports_the_document_id_it_names():
    assert ContainmentVerdict(True, "because").required_disabled_doc_ids == CONTAINED_DOC_ID
    assert ContainmentVerdict(False, "because").required_disabled_doc_ids == ""
