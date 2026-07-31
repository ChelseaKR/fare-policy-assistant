"""Static release-workflow invariants that must hold even when a job fails."""

import yaml

from assistant import config


def _ci_workflow() -> tuple[str, dict]:
    text = (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def test_failed_full_eval_still_uploads_evidence():
    workflow, _ = _ci_workflow()
    full_eval = workflow.split("  full-evals-nightly:", 1)[1].split("  independent-audit:", 1)[0]
    upload = full_eval.split("- name: Upload report", 1)[1]
    assert "if: always()" in upload, (
        "nightly evaluation failures must retain their partial report, traces, "
        "and provenance for diagnosis"
    )


def test_pull_request_eval_is_explicitly_offline_and_cannot_mint_oidc_tokens():
    _, workflow = _ci_workflow()
    jobs = workflow["jobs"]
    offline = jobs["smoke-evals-offline"]

    assert offline["if"] == "github.event_name == 'pull_request'"
    assert offline["permissions"] == {"contents": "read"}
    commands = [step.get("run", "") for step in offline["steps"]]
    assert any("--smoke --offline --no-cache" in command for command in commands)
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
