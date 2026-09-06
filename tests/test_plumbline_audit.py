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

    def test_a_hard_failure_the_harness_names_is_seen_even_under_an_unknown_key(self) -> None:
        """The guard must never see less than the tool it is gating.

        Until 2026-08-28 `hard_failures()` read only `_EXTRA_HARD_FAILURE_KEYS`,
        a hand-maintained list of *detail* keys, and ignored the per-suite
        `hard_failures` verdict the report already states outright
        (plumbline/src/plumbline/report.py). A pinned-harness bump that renamed
        a key, or a new suite whose hard failures land under a key nobody added
        here, would have made those findings invisible to the merge gate with
        nothing to notice.
        """
        report = self._report()
        for suite in report["suites"]:
            if suite["suite"] == "smoke":
                suite["hard_failures"] = ["planted-load-bearing-item"]
                suite["details"]["a_key_this_guard_has_never_heard_of"] = ["planted-under-new-key"]
        problems = guard.check(report, self._baseline(), self._acknowledged())
        assert any("planted-load-bearing-item" in p for p in problems), problems

    def test_the_derived_set_is_never_narrower_than_the_harness_verdict(self) -> None:
        report = self._report()
        derived = guard.hard_failures(report)
        for suite in report["suites"]:
            named = set(suite.get("hard_failures") or [])
            assert named <= set(derived.get(suite["suite"], [])), (
                f"{suite['suite']}: the harness calls {sorted(named)} hard failures and the "
                "guard does not see all of them"
            )

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
        lands, which teaches everyone to ignore it — UNLESS the target file
        says, in `floor_above_baseline_reason`, why this specific measurement
        is not a real ceiling to respect.

        That escape hatch exists for exactly one situation: the suite's own
        instrument is independently shown to be reporting the wrong number,
        as opposed to a blunt-but-real one. Every other low floor in this file
        (accuracy, cross_language, groundedness, ...) sits below a score that
        is a *noisy* signal of real behaviour — recall against the wrong
        shape of reference, mostly — and for those, "floor below measurement"
        is correct: raising the floor to what a human wishes were true is the
        anti-pattern. `adversarial` on 2026-09-04 was the first case where the
        measured 0.0 was checked against the raw recorded responses and found
        to be flatly wrong (three correct refusals, scored as three answers,
        because of a fixed upstream marker list this target cannot extend) —
        not noisy, wrong. A reason is required and must be substantial, so
        this cannot become a second way to paper over an aspirational floor;
        it only ever excuses a *specific, argued* case, on the record, in the
        same file the floor lives in.
        """
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
            if floor <= baseline[name] + 1e-9:
                continue
            reason = spec.get("floor_above_baseline_reason", "")
            assert len(reason.strip()) > 40, (
                f"[suites.{name}].floor {floor} is above the measured "
                f"{baseline[name]} with no `floor_above_baseline_reason` (or too "
                f"short a one) explaining why this is not the aspirational "
                f"floor this test exists to catch"
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


class TestAGateThatCouldNotRunIsNotAGateThatPassed:
    """#183: `make audit` and the `independent-audit` job could both exit 0
    over an audit that never happened.

    Two individually reasonable pieces combined into a hole. `plumbline gate`'s
    FAIL verdict is deliberately not this repository's merge gate, so the call
    ended `|| true` — which swallowed exit 2 (usage), 3 (integrity refusal:
    the evidence bundle did not verify and nothing was scored) and 4
    (configuration or environment error) just as happily as exit 1. On all
    three the harness writes no report, and `latest_report()` then fell back to
    the newest `report.json` on disk by mtime: the committed one, restored by
    checkout, and clean since the day it was committed. The guard graded that,
    found nothing wrong with it, and the build went green.

    Not red-and-ignored. Green, with the reason visible only in a step log that
    a green check gives nobody a reason to open.
    """

    GATE_RAN = (0, 1)  # PASS, and ran-then-reported-FAIL
    GATE_SCORED_NOTHING = (2, 3, 4)  # usage, integrity refusal, environment

    def _audit_harness(self, tmp_path: Path, gate_exit: int) -> tuple[int, str]:
        """Run the real `audit` recipe against a gate that exits `gate_exit`.

        The Makefile is copied verbatim rather than re-spelled, so this cannot
        drift from the recipe it is pinning. `uv` is stubbed with a script that
        logs its arguments, which is what lets the assertion be "the guard was
        never reached" rather than "the exit code looked right".
        """
        import os
        import shutil
        import subprocess

        shutil.copy(config.REPO_ROOT / "Makefile", tmp_path / "Makefile")

        gate = tmp_path / "plumbline-gate.sh"
        gate.write_text(f"#!/bin/sh\necho 'stub gate, exiting {gate_exit}'\nexit {gate_exit}\n")
        gate.chmod(0o755)

        log = tmp_path / "uv-calls.log"
        stub_bin = tmp_path / "bin"
        stub_bin.mkdir()
        uv = stub_bin / "uv"
        uv.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
        uv.chmod(0o755)

        env = dict(os.environ, PATH=f"{stub_bin}:{os.environ['PATH']}")
        proc = subprocess.run(
            ["make", "audit"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, log.read_text(encoding="utf-8") if log.exists() else ""

    @pytest.mark.parametrize("gate_exit", GATE_SCORED_NOTHING)
    def test_make_audit_stops_when_the_gate_scored_nothing(
        self, tmp_path: Path, gate_exit: int
    ) -> None:
        returncode, calls = self._audit_harness(tmp_path, gate_exit)
        assert returncode != 0, (
            f"plumbline-gate.sh exited {gate_exit} — it scored nothing — and `make audit` "
            "still succeeded. This is #183: the build passes over an audit that never ran."
        )
        assert "plumbline_guard" not in calls, (
            "the guard was reached after a gate that wrote no report, so the only thing "
            "left for it to grade is the committed report from a previous run"
        )

    @pytest.mark.parametrize("gate_exit", GATE_RAN)
    def test_make_audit_still_grades_a_gate_that_ran(self, tmp_path: Path, gate_exit: int) -> None:
        """The negative control. A FAIL verdict must still reach the guard, or
        this fix would have replaced a gate that cannot fail with one that
        cannot pass — and the guard, not the harness's verdict, is the gate."""
        returncode, calls = self._audit_harness(tmp_path, gate_exit)
        assert "plumbline_guard" in calls, (
            f"plumbline-gate.sh exited {gate_exit}, which means it ran; the guard is the "
            "merge gate and must still be the thing that decides"
        )
        assert returncode == 0
        assert "--not-before" in calls, (
            "the guard must be handed the second this run started, so a report older "
            "than the run is refused rather than graded"
        )

    def test_the_ci_job_does_not_swallow_a_gate_that_scored_nothing(self) -> None:
        """The Makefile is a laptop; `independent-audit` is the required check.

        Fixing only one of them leaves the hole open where it costs the most.
        """
        text = (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        job = text.split("independent-audit:", 1)[1]
        body = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
        assert './plumbline-gate.sh --summary-file "$GITHUB_STEP_SUMMARY" || true' not in body, (
            "`|| true` accepts exit 2, 3 and 4 as readily as exit 1, and on all three the "
            "harness wrote no report for the guard to grade"
        )
        assert "plumbline_guard --not-before" in body, (
            "the guard step must refuse a report older than this run"
        )

    def test_a_report_older_than_the_run_is_refused_rather_than_graded(
        self, tmp_path: Path
    ) -> None:
        """The second defence, exercised directly: an unforeseen way of writing
        no report still cannot be graded from a leftover."""
        import os

        run_dir = tmp_path / "abc123"
        run_dir.mkdir()
        report = run_dir / "report.json"
        report.write_text("{}", encoding="utf-8")

        started = 2_000_000_000.0
        os.utime(report, (started - 3600, started - 3600))

        with pytest.raises(SystemExit) as excinfo:
            guard.latest_report(tmp_path, not_before=started)
        message = str(excinfo.value)
        assert "before this run started" in message
        assert str(report) in message, "the message must name the leftover it refused"

    def test_a_report_this_run_produced_is_graded(self, tmp_path: Path) -> None:
        """Negative control for the timestamp: a fresh report must still pass,
        or `--not-before` would be a check that can only fail."""
        import os

        run_dir = tmp_path / "abc123"
        run_dir.mkdir()
        report = run_dir / "report.json"
        report.write_text("{}", encoding="utf-8")

        started = 2_000_000_000.0
        os.utime(report, (started + 1, started + 1))

        assert guard.latest_report(tmp_path, not_before=started) == report

    def test_no_timestamp_still_reads_the_latest(self, tmp_path: Path) -> None:
        """`--not-before` is opt-in, so `uv run python -m evals.plumbline_guard
        --report <path>` and a bare interactive run keep working."""
        import os

        for name, mtime in (("old", 1_000_000_000), ("new", 2_000_000_000)):
            run_dir = tmp_path / name
            run_dir.mkdir()
            report = run_dir / "report.json"
            report.write_text("{}", encoding="utf-8")
            os.utime(report, (mtime, mtime))

        assert guard.latest_report(tmp_path).parent.name == "new"
