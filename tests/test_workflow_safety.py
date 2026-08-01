"""Static release-workflow invariants that must hold even when a job fails."""

import pytest

from assistant import config


def _ci_workflow() -> str:
    return (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _job(name: str, next_name: str) -> str:
    return _ci_workflow().split(f"  {name}:", 1)[1].split(f"  {next_name}:", 1)[0]


def test_failed_full_eval_still_uploads_evidence():
    full_eval = _job("full-evals-nightly", "independent-audit")
    upload = full_eval.split("- name: Upload report", 1)[1]
    assert "if: always()" in upload, (
        "nightly evaluation failures must retain their partial report, traces, "
        "and provenance for diagnosis"
    )


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
)
def test_paid_eval_jobs_restore_and_save_the_model_cache(job, next_job):
    """ADR 0022. Model calls are this project's largest AWS line. A job that
    starts cold re-buys answers it already has, so both paid eval jobs must
    restore the content-keyed cache and save it again afterwards."""
    body = _job(job, next_job)
    assert "actions/cache/restore@" in body, f"{job} must restore evals/cache before running"
    assert "actions/cache/save@" in body, f"{job} must save evals/cache after running"
    assert "restore-keys: eval-model-cache-" in body, (
        f"{job}'s restore must fall through to the shared prefix, so it can inherit the "
        "cache the nightly full run wrote on the default branch"
    )


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
)
def test_a_failing_eval_still_saves_the_calls_it_paid_for(job, next_job):
    """A red regression gate does not make the model calls free. Saving only on
    success would make a week of failing nightlies re-buy the same 201 cases
    every morning."""
    save = _job(job, next_job).split("- name: Save the answer/judge model cache", 1)[1]
    assert "if: always()" in save.split("uses:", 1)[0], (
        f"{job} must save the model cache even when the run fails its gate"
    )


def test_one_scheduled_full_run_a_week_is_cold():
    """ADR 0022 trades six nights of provider-drift detection for cost, and buys
    it back with one cold run a week. If every nightly were cache-served,
    nothing would ever re-measure the provider."""
    workflow = _ci_workflow()
    crons = [line for line in workflow.splitlines() if line.strip().startswith("- cron:")]
    assert len(crons) >= 2, "expected a cached nightly cron and a separate cold weekly cron"
    full_eval = _job("full-evals-nightly", "independent-audit")
    assert "--refresh-cache" in full_eval, (
        "the weekly schedule must run the full suite with --refresh-cache, so it both "
        "re-measures the provider and leaves the cache agreeing with what it published"
    )
    cold_cron = full_eval.split("github.event.schedule == '", 1)[1].split("'", 1)[0]
    assert any(cold_cron in line for line in crons), (
        f"the cold-run schedule guard names {cold_cron!r}, which is not one of this "
        "workflow's cron entries — a renamed cron would silently make every nightly cached"
    )
