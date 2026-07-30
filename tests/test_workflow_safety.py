"""Static release-workflow invariants that must hold even when a job fails."""

from assistant import config


def test_failed_full_eval_still_uploads_evidence():
    workflow = (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    full_eval = workflow.split("  full-evals-nightly:", 1)[1].split("  independent-audit:", 1)[0]
    upload = full_eval.split("- name: Upload report", 1)[1]
    assert "if: always()" in upload, (
        "nightly evaluation failures must retain their partial report, traces, "
        "and provenance for diagnosis"
    )
