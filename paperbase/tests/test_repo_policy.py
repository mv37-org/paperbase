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


def test_job_orm_carries_retry_policy_columns():
    """Regression test for #32: the worker retry policy requires four new
    columns on the jobs table plus a ``dead`` terminal state. This locks the
    schema so a future migration can't accidentally drop the retry fields.
    """
    from paperbase.repository import JobORM, default_max_attempts

    for column in ("attempts", "max_attempts", "next_run_at", "last_attempted_at", "dedupe_key"):
        assert column in JobORM.__table__.columns, (
            f"JobORM must carry '{column}' for the worker retry policy"
        )

    # Per-job-type attempt budgets per the #32 spec.
    assert default_max_attempts("index_paper") == 3
    assert default_max_attempts("sync_latest") == 5
    assert default_max_attempts("reindex_embeddings") == 2


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


def test_paper_schema_carries_code_ingestion_fields():
    """Regression test for #5: the paper-code ingestion feature requires four
    new columns on the papers table plus matching Pydantic-model fields so
    API consumers can read the classification verdict and manifest. Locks
    the contract so a future migration can't accidentally drop the
    code-ingestion fields."""
    from paperbase.repository import PaperORM

    for column in ("code_index_status", "code_is_python", "code_python_reason", "code_manifest"):
        assert column in PaperORM.__table__.columns, (
            f"PaperORM must carry '{column}' for paper-code ingestion (#5)"
        )

    paper = PaperMetadata(arxiv_id="2401.99999")
    # Defaults match the "not yet classified" state — a fresh paper should
    # neither be flagged Python nor carry a stale manifest.
    assert paper.code_index_status == "none"
    assert paper.code_is_python is None
    assert paper.code_python_reason is None
    assert paper.code_manifest is None


def test_paper_schema_carries_venue_signal_fields():
    """Regression test for #39 PR B: peer-review venue signal needs three
    new columns (venue / venue_year / venue_source) so retrieval can
    boost peer-reviewed work over preprints. Locks the contract so a
    future migration can't accidentally drop them."""
    from paperbase.repository import PaperORM

    for column in ("venue", "venue_year", "venue_source"):
        assert column in PaperORM.__table__.columns, (
            f"PaperORM must carry '{column}' for venue signal (#39 PR B)"
        )

    paper = PaperMetadata(arxiv_id="2401.99999")
    # Defaults are None — a fresh paper has no venue stamp until the
    # harvest_venue chunk worker runs ``set_paper_venue``.
    assert paper.venue is None
    assert paper.venue_year is None
    assert paper.venue_source is None


def test_job_orm_carries_priority_for_backfill_starvation_guard():
    """Regression test for #33: harvest jobs must run at lower priority than
    interactive on-demand work so a multi-year backfill cannot starve
    ``index_paper``. Locks the priority column + defaults in place."""
    from paperbase.repository import JobORM, default_priority

    assert "priority" in JobORM.__table__.columns, (
        "JobORM must carry a 'priority' column so bulk backfill cannot starve "
        "interactive on-demand work"
    )
    # Smaller = claimed first. Interactive on-demand work sits below zero so
    # it always wins; backfill sits above zero so it always loses.
    assert default_priority("index_paper") == -10
    assert default_priority("sync_latest") == 0
    assert default_priority("harvest_chunk") == 10
    assert default_priority("harvest_range") == 5
    assert default_priority("brand_new_job_type") == 0


def test_paper_schema_carries_code_url_provenance_fields():
    """Regression test for #46: the 3-layer code_url decision requires
    ``code_url_source`` (``extracted``/``hf_papers``/``admin``) and
    ``code_url_candidates`` (the full ranker input) on the papers table
    so admin pins survive re-index and a later operator can pick an
    alternate repo from the rejected candidates."""
    from paperbase.repository import PaperORM

    for column in ("code_url_source", "code_url_candidates"):
        assert column in PaperORM.__table__.columns, (
            f"PaperORM must carry '{column}' for #46 code_url provenance"
        )

    paper = PaperMetadata(arxiv_id="2401.99999")
    assert paper.code_url_source is None
    assert paper.code_url_candidates is None


def test_code_crosslink_setting_defaults_on(monkeypatch):
    """The layer-1 Hugging Face paper-page crosslink is intended to run
    by default on index-time paths; operators can still disable it via
    env when they want a pure text-only run."""
    from paperbase.config import Settings

    monkeypatch.delenv("PAPERBASE_FETCH_CODE_CROSSLINKS_AT_INDEX", raising=False)
    assert Settings(_env_file=None).fetch_code_crosslinks_at_index is True
