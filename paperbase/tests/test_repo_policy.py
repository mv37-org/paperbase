from __future__ import annotations

from paperbase.config import repo_root


def _read_repo_file(relative_path: str) -> str:
    root = repo_root()
    assert root is not None
    return (root / relative_path).read_text(encoding="utf-8")


def test_pre_push_hook_delegates_to_make_test():
    hook = _read_repo_file(".githooks/pre-push")

    assert "make test" in hook


def test_verify_workflow_delegates_to_make_test():
    workflow = _read_repo_file(".github/workflows/verify.yml")

    assert "make test" in workflow


def test_profile_workflow_delegates_to_make_profile():
    workflow = _read_repo_file(".github/workflows/profile.yml")

    assert "make profile" in workflow
