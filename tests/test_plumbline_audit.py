"""The independent audit must be reproducible by a stranger.

That is the whole claim, and until 2026-08-16 it was false: `make audit`
resolved its harness from `EVAL_HARNESS ?= ../govchat-eval`, and that repository
went private and archived. The audit could not be re-run by anyone outside the
project, or inside it without an archived clone, and the CI job that was
supposed to notice ran only on a schedule, only behind a repository variable,
and with `continue-on-error: true`.

These tests pin the properties that make the replacement checkable rather than
merely present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from assistant import config
from evals import plumbline_export as export
from evals import plumbline_guard as guard

PIN = config.REPO_ROOT / "plumbline.pin"
GATE = config.REPO_ROOT / "plumbline-gate.sh"
TARGET = config.REPO_ROOT / "evals" / "plumbline" / "target.toml"


def _pin() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in PIN.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0]
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class TestTheArchivedHarnessIsGone:
    def test_no_tracked_file_resolves_govchat_eval_at_run_time(self) -> None:
        """The name may survive in history and in prose; the *dependency* may not.

        `evals/govchat/golden.jsonl` stays: it is the recording, and the whole
        audit is built from it. What must not survive is anything that clones,
        cd's into, or executes the archived harness.
        """
        offenders = []
        for path in (
            config.REPO_ROOT / "Makefile",
            config.REPO_ROOT / ".github" / "workflows" / "ci.yml",
        ):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue  # a comment explaining the migration is fine
                if re.search(r"govchat-eval(\.git|\s|/|\")", line) or "EVAL_HARNESS" in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offenders, "these lines still reach for the archived harness:\n" + "\n".join(
            offenders
        )


class TestThePin:
    def test_the_ref_is_an_exact_commit(self) -> None:
        """A branch or tag can move, and then a green gate means nothing."""
        ref = _pin()["ref"]
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"ref {ref!r} is not a 40-hex commit"

    def test_the_pin_names_a_public_harness_and_a_target(self) -> None:
        pin = _pin()
        assert pin["repo"] == "https://github.com/ChelseaKR/plumbline.git"
        assert pin["config"] == "evals/plumbline/target.toml"
        assert pin["baseline"] == "evals/plumbline/baseline.json"

    def test_the_gate_runner_is_executable(self) -> None:
        assert GATE.is_file() and GATE.stat().st_mode & 0o111, (
            "plumbline-gate.sh must be executable; CI and the Makefile invoke it directly"
        )

    def test_the_pin_records_the_known_upstream_integrity_defect(self) -> None:
        """The pinned commit hashes bundles with `iterdir()`, so a subdirectory
        would go unhashed. This bundle is flat and the exporter seals with its
        own recursive walk, so the hole is not open here — but a reader has to
        be able to find that out from the pin rather than from a commit message.
        """
        text = PIN.read_text(encoding="utf-8")
        assert "iterdir" in text and "KNOWN DEFECT" in text


class TestTheBundleIsWhatTheRecordingProduces:
    def test_committed_bundle_matches_a_fresh_export(self) -> None:
        """`make audit` runs this first, so a suite edit or a re-recording that
        was not exported fails the build instead of being audited against stale
        evidence."""
        drift = export.check_bundle(export.build_bundle())
        assert not drift, f"regenerate the bundle (`make audit-record`); differing: {drift}"

    def test_every_cited_source_resolves(self) -> None:
        """The one thing the adapter can get wrong that would silently invert a
        finding: if a source id did not match the id the recorded answer cites,
        `citation_validity` would read every real citation as a fabricated one
        and the suite would read 0.0 while nothing was wrong."""
        bundle_dir = export.BUNDLE_DIR
        source_ids = {
            json.loads(line)["id"]
            for line in (bundle_dir / "sources.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        }
        citation_re = re.compile(r"\[([A-Za-z][A-Za-z0-9._:-]*)\]")
        unresolved: set[str] = set()
        for line in (bundle_dir / "responses.jsonl").read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            unresolved |= set(citation_re.findall(row["response"])) - source_ids
        assert not unresolved, f"recorded answers cite ids with no source: {sorted(unresolved)}"

    def test_the_checksums_cover_every_file_including_nested_ones(self, tmp_path: Path) -> None:
        """The exporter seals with a recursive walk rather than the pinned
        harness's top-level one. A file in a subdirectory must be hashed."""
        bundle = tmp_path / "bundle"
        (bundle / "nested").mkdir(parents=True)
        (bundle / "flat.txt").write_text("a", encoding="utf-8")
        (bundle / "nested" / "deep.txt").write_text("b", encoding="utf-8")
        checksums = export._seal(bundle)
        assert set(checksums["files"]) == {"flat.txt", "nested/deep.txt"}

    def test_provenance_travels_with_the_source_text(self) -> None:
        """Same repair as evals/judges._passages_block, for the same reason: the
        answer is required to disclose its snapshot date, so a grounding checker
        that cannot see the date scores the disclosure as invented."""
        for line in (export.BUNDLE_DIR / "sources.jsonl").read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            source = json.loads(line)
            assert source["text"].startswith("(source: http")
            assert "fetched 20" in source["text"].splitlines()[0]

    def test_forbidden_phrases_ride_only_on_attack_probes(self) -> None:
        """`forbidden_content` here means "must not be asserted" and Plumbline's
        `forbidden` means "must not appear". Exporting one as the other failed
        four items for correctly *denying* the claim."""
        items = [
            json.loads(line)
            for line in (export.BUNDLE_DIR / "items.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        with_forbidden = [i for i in items if i.get("forbidden")]
        assert with_forbidden, "the attack probes should still carry their screen"
        assert all(i.get("adversarial") for i in with_forbidden)


class TestTheGuardIsTheGate:
    """`plumbline gate` decides on floors, and several floors here sit below the
    harness's defaults with written reasons. A floor of 0.04 catches collapse
    and nothing else, so the guard is what stops a slow slide."""

    def _report(self) -> dict:
        return json.loads(guard.latest_report().read_text(encoding="utf-8"))

    def _baseline(self) -> dict:
        return json.loads(guard.BASELINE_PATH.read_text(encoding="utf-8"))

    def _acknowledged(self) -> dict:
        raw = json.loads(guard.ACK_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}

    def test_the_committed_audit_passes_the_guard(self) -> None:
        assert guard.check(self._report(), self._baseline(), self._acknowledged()) == []

    def test_a_score_below_baseline_fails(self) -> None:
        report = self._report()
        report["suites"][0]["score"] = round(report["suites"][0]["score"] - 0.05, 4)
        problems = guard.check(report, self._baseline(), self._acknowledged())
        assert any("below the committed baseline" in p for p in problems)

    def test_a_new_hard_failure_fails(self) -> None:
        report = self._report()
        for suite in report["suites"]:
            if suite["suite"] == "privacy":
                suite["details"]["unsourced_disclosures"].append("brand-new-item")
        problems = guard.check(report, self._baseline(), self._acknowledged())
        assert any("nobody has acknowledged" in p and "brand-new-item" in p for p in problems)

    def test_an_acknowledgement_that_stopped_firing_fails(self) -> None:
        acknowledged = self._acknowledged()
        acknowledged["privacy"] = dict(acknowledged["privacy"], **{"already-fixed": "reason"})
        problems = guard.check(self._report(), self._baseline(), acknowledged)
        assert any("no longer fire" in p and "already-fixed" in p for p in problems)

    def test_an_incomparable_baseline_fails_rather_than_being_skipped(self) -> None:
        """The harness declines to subtract scores across different evidence,
        correctly. "Not comparable" must not then read as "fine": a re-recording
        is exactly when a regression is easiest to miss."""
        report = self._report()
        report["provenance"]["dataset_sha256"] = "0" * 64
        problems = guard.check(report, self._baseline(), self._acknowledged())
        assert any("not comparable" in p for p in problems)

    def test_every_acknowledgement_carries_a_reason(self) -> None:
        for suite, entries in self._acknowledged().items():
            for item, reason in entries.items():
                assert len(reason.strip()) > 40, f"{suite}/{item} has no real reason"


class TestTheTargetConfigExplainsItself:
    def test_every_disabled_suite_says_why_and_who_fixes_it(self) -> None:
        import tomllib

        raw = tomllib.loads(TARGET.read_text(encoding="utf-8"))
        disabled = {
            name: spec for name, spec in raw["suites"].items() if not spec.get("enabled", True)
        }
        assert disabled, "if nothing is disabled this test should be deleted, not weakened"
        for name, spec in disabled.items():
            assert spec.get("gap"), (
                f"[suites.{name}] is off with no `gap` explaining what is unmeasured"
            )
            assert spec.get("fix_belongs_in"), f"[suites.{name}] is off with no owner"

    def test_no_enabled_suite_sits_above_the_committed_baseline(self) -> None:
        """A floor above the measurement is a gate that is red on the day it
        lands, which teaches everyone to ignore it."""
        import tomllib

        raw = tomllib.loads(TARGET.read_text(encoding="utf-8"))
        baseline = {
            s["suite"]: s["score"]
            for s in json.loads(guard.BASELINE_PATH.read_text(encoding="utf-8"))["suites"]
        }
        for name, spec in raw["suites"].items():
            if not spec.get("enabled", True):
                continue
            floor = spec.get("floor")
            if floor is None or name not in baseline:
                continue
            assert floor <= baseline[name] + 1e-9, (
                f"[suites.{name}].floor {floor} is above the measured {baseline[name]}"
            )


@pytest.mark.parametrize(
    "path",
    [
        "items.jsonl",
        "responses.jsonl",
        "sources.jsonl",
        "manifest.json",
        "checksums.json",
        "interface.html",
        "transcripts.html",
    ],
)
def test_the_bundle_ships_every_file_the_audit_reads(path: str) -> None:
    assert (export.BUNDLE_DIR / path).is_file()
