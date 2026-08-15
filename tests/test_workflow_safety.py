"""Static release-workflow invariants that must hold even when a job fails."""

import pytest
import yaml

from assistant import config


def _ci_workflow() -> tuple[str, dict]:
    text = (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def _job(name: str, next_name: str) -> str:
    text, _ = _ci_workflow()
    return text.split(f"  {name}:", 1)[1].split(f"  {next_name}:", 1)[0]


def test_failed_full_eval_still_uploads_evidence():
    full_eval = _job("full-evals-nightly", "independent-audit")
    upload = full_eval.split("- name: Upload report", 1)[1]
    assert "if: always()" in upload, (
        "nightly evaluation failures must retain their partial report, traces, "
        "and provenance for diagnosis"
    )


def test_pull_request_eval_is_explicitly_offline_and_cannot_mint_oidc_tokens():
    _, workflow = _ci_workflow()
    jobs = workflow["jobs"]
    offline = jobs["smoke-evals"]

    assert offline["if"] == "github.event_name == 'pull_request'"
    assert offline["permissions"] == {"contents": "read"}
    commands = [step.get("run", "") for step in offline["steps"]]
    assert any("make eval-selftest" in command for command in commands)
    assert all("evals.runner" not in command for command in commands)
    assert all(
        "configure-aws-credentials" not in str(step.get("uses", "")) for step in offline["steps"]
    )


def test_only_non_pr_eval_jobs_receive_oidc_permission():
    _, workflow = _ci_workflow()
    jobs = workflow["jobs"]
    online = jobs["smoke-evals-online"]
    nightly = jobs["full-evals-nightly"]

    assert online["if"] == "github.event_name == 'push'"
    assert online["permissions"]["id-token"] == "write"
    assert nightly["if"] == "github.event_name == 'schedule'"
    assert nightly["permissions"]["id-token"] == "write"
    oidc_jobs = {
        name
        for name, job in jobs.items()
        if isinstance(job, dict)
        and isinstance(job.get("permissions"), dict)
        and job["permissions"].get("id-token") == "write"
    }
    assert oidc_jobs == {"smoke-evals-online", "full-evals-nightly"}


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals-online", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
)
def test_paid_eval_jobs_restore_and_save_the_model_cache(job, next_job):
    """ADR 0022. Model calls are this project's largest AWS line. A job that
    starts cold re-buys answers it already has, so both paid eval jobs must
    restore the content-keyed cache and save it again afterwards.

    ``smoke-evals`` itself is offline (self-test only, no model calls, see
    ``test_pull_request_eval_is_explicitly_offline_and_cannot_mint_oidc_tokens``)
    so it has nothing to cache; ``smoke-evals-online`` is the paid job that
    replaced it post-merge."""
    body = _job(job, next_job)
    assert "actions/cache/restore@" in body, f"{job} must restore evals/cache before running"
    assert "actions/cache/save@" in body, f"{job} must save evals/cache after running"
    assert "restore-keys: eval-model-cache-" in body, (
        f"{job}'s restore must fall through to the shared prefix, so it can inherit the "
        "cache the nightly full run wrote on the default branch"
    )


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals-online", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
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
    workflow, _ = _ci_workflow()
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


def test_paid_eval_jobs_are_never_promotion_evidence():
    """ADR 0023 (evaluation identity and promotion attestation): a promotable
    run must be uncached, and cache speedups 'remain available for
    development, but cannot enter promotion evidence'. The actual promotion
    run is its own full, live, --no-cache invocation at deploy time
    (infra/deploy.sh), independent of these CI jobs — so it is correct, not a
    regression, for smoke-evals-online and full-evals-nightly to be
    cache-backed here."""
    deploy_script = (config.REPO_ROOT / "infra" / "deploy.sh").read_text(encoding="utf-8")
    assert "--full" in deploy_script and "--no-cache" in deploy_script, (
        "promotion must still run its own full, uncached evaluation independent of "
        "the routine CI jobs' caching"
    )
