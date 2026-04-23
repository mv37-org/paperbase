from __future__ import annotations

import re

from paperbase.config import repo_root
from paperbase.extractors import ExtractionOutput, build_manifest
from paperbase.models import PaperMetadata


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


def test_build_manifest_includes_preview_key_in_available_artifacts():
    paper = PaperMetadata(
        arxiv_id="2401.00001",
        title="Policy Probe",
        authors="Policy",
        abstract="x",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-02",
    )
    extraction = ExtractionOutput(source_type="abstract-only", text_markdown="# x\n")

    manifest = build_manifest("paper_1", paper, extraction)

    assert "preview" in manifest["available_artifacts"]
    assert manifest["available_artifacts"]["preview"] is None
