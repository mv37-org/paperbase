from __future__ import annotations

import re

from paperbase.config import repo_root


def _read_repo_file(relative_path: str) -> str:
    root = repo_root()
    assert root is not None
    return (root / relative_path).read_text(encoding="utf-8")


def test_pre_push_hook_delegates_to_make_test():
    hook = _read_repo_file(".githooks/pre-push")

    assert "make test" in hook


def test_verify_workflow_uses_smoke_on_pull_requests_and_full_test_elsewhere():
    workflow = _read_repo_file(".github/workflows/verify.yml")

    assert "make test-smoke" in workflow
    assert "make test" in workflow
    assert "cancel-in-progress: true" in workflow


def test_profile_workflow_delegates_to_make_profile():
    workflow = _read_repo_file(".github/workflows/profile.yml")

    assert "make profile" in workflow
    assert '"codex/**"' not in workflow
    assert "cancel-in-progress: true" in workflow


def test_cloudbuild_uses_supported_substitutions_only():
    cloudbuild = _read_repo_file("paperbase/cloudbuild.yaml")
    pattern = re.compile(r"(?<!\$)\$(\{[_A-Z][_A-Z0-9]*\}|[A-Z][A-Z0-9_]*)")

    tokens = [match.group(1).strip("{}") for match in pattern.finditer(cloudbuild)]
    invalid = sorted(
        {
            token
            for token in tokens
            if token not in {"PROJECT_ID", "SHORT_SHA"} and not token.startswith("_")
        }
    )

    assert invalid == []


def test_job_orm_carries_retry_policy_columns():
    """Regression test for #32: the worker retry policy requires four new
    columns on the jobs table plus a ``dead`` terminal state. This locks the
    schema so a future migration can't accidentally drop the retry fields.
    """
    from paperbase.repository import JobORM, default_max_attempts

    for column in ("attempts", "max_attempts", "next_run_at", "last_attempted_at"):
        assert column in JobORM.__table__.columns, (
            f"JobORM must carry '{column}' for the worker retry policy"
        )

    # Per-job-type attempt budgets per the #32 spec.
    assert default_max_attempts("index_paper") == 3
    assert default_max_attempts("sync_latest") == 5
    assert default_max_attempts("reindex_embeddings") == 2
