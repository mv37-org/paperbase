from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from paperbase.config import Settings
from paperbase.dev_benchmarks import _build_iq1_report, _render_iq1_markdown, run_indexing_census


def test_build_iq1_report_computes_core_census_metrics():
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    rows = [
        {
            "paper_id": "paper_a",
            "primary_category": "cs.AI",
            "published": "2024-01-01",
            "source_type": "html",
            "index_status": "indexed",
            "created_at": now - timedelta(minutes=20),
            "indexed_at": now - timedelta(minutes=10),
            "has_paper_embedding": True,
            "total_chunks": 12,
            "chunks_with_embedding": 12,
            "section_count": 6,
            "figure_caption_count": 2,
            "table_count": 1,
            "full_text_count": 1,
        },
        {
            "paper_id": "paper_b",
            "primary_category": "math.OC",
            "published": "2017-05-02",
            "source_type": "abstract-only",
            "index_status": "indexed",
            "created_at": now - timedelta(minutes=40),
            "indexed_at": now - timedelta(minutes=20),
            "has_paper_embedding": False,
            "total_chunks": 1,
            "chunks_with_embedding": 0,
            "section_count": 1,
            "figure_caption_count": 0,
            "table_count": 0,
            "full_text_count": 1,
        },
        {
            "paper_id": "paper_c",
            "primary_category": "cs.CL",
            "published": "",
            "source_type": None,
            "index_status": "discovered",
            "created_at": now - timedelta(minutes=5),
            "indexed_at": None,
            "has_paper_embedding": False,
            "total_chunks": 0,
            "chunks_with_embedding": 0,
            "section_count": 0,
            "figure_caption_count": 0,
            "table_count": 0,
            "full_text_count": 0,
        },
    ]
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )

    report = _build_iq1_report(
        rows=rows,
        preview_paper_ids={"paper_a"},
        index_job_stats={
            "total_jobs": 5,
            "completed_jobs": 2,
            "dead_jobs": 1,
            "legacy_failed_jobs": 1,
            "pending_jobs": 1,
            "running_jobs": 0,
            "retried_jobs": 3,
        },
        settings=settings,
    )

    assert report["population"]["total_papers"] == 3
    assert report["population"]["indexed_papers"] == 2
    assert report["population"]["status_counts"] == {
        "indexed": 2,
        "discovered": 1,
    }

    assert report["source_richness"]["rich_index_rate"] == 0.5
    assert report["source_richness"]["abstract_only_rate"] == 0.5

    assert report["artifact_completeness"]["papers_with_sections"] == 2
    assert report["artifact_completeness"]["papers_with_five_sections"] == 1
    assert report["artifact_completeness"]["papers_with_figure_captions"] == 1
    assert report["artifact_completeness"]["papers_with_tables"] == 1
    assert report["artifact_completeness"]["papers_with_preview"] == 1
    assert report["artifact_completeness"]["median_sections_per_paper"] == 3.5
    assert report["artifact_completeness"]["median_chunks_per_paper"] == 6.5

    assert report["embedding_coverage"]["papers_with_embedding"] == 1
    assert report["embedding_coverage"]["paper_embedding_rate"] == 0.5
    assert report["embedding_coverage"]["total_chunks"] == 13
    assert report["embedding_coverage"]["chunks_with_embedding"] == 12
    assert report["embedding_coverage"]["chunk_embedding_rate"] == 12 / 13

    assert report["freshness"]["measured_papers"] == 2
    assert report["freshness"]["created_to_indexed_p50_seconds"] == 900.0
    assert report["freshness"]["created_to_indexed_p95_seconds"] == 1170.0
    assert report["freshness"]["created_to_indexed_p99_seconds"] == 1194.0

    assert report["reliability"]["terminal_failed_jobs"] == 2
    assert report["reliability"]["active_jobs"] == 1
    assert report["reliability"]["terminal_success_rate"] == 0.5
    assert report["reliability"]["terminal_failure_rate"] == 0.5
    assert report["reliability"]["retry_rate"] == 3 / 5
    assert report["reliability"]["active_rate"] == 1 / 5

    source_keys = [item["key"] for item in report["strata"]["source_type"]]
    assert source_keys == ["abstract-only", "html"]


def test_render_iq1_markdown_includes_eli5_headline_and_notes():
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    report = _build_iq1_report(
        rows=[
            {
                "paper_id": "paper_a",
                "primary_category": "cs.AI",
                "published": "2024-01-01",
                "source_type": "html",
                "index_status": "indexed",
                "created_at": now - timedelta(minutes=20),
                "indexed_at": now - timedelta(minutes=10),
                "has_paper_embedding": True,
                "total_chunks": 4,
                "chunks_with_embedding": 4,
                "section_count": 2,
                "figure_caption_count": 0,
                "table_count": 0,
                "full_text_count": 1,
            }
        ],
        preview_paper_ids={"paper_a"},
        index_job_stats={
            "total_jobs": 1,
            "completed_jobs": 1,
            "dead_jobs": 0,
            "legacy_failed_jobs": 0,
            "pending_jobs": 0,
            "running_jobs": 0,
            "retried_jobs": 0,
        },
        settings=settings,
    )

    rendered = _render_iq1_markdown(report)

    assert "# IQ-1 Indexing Census" in rendered
    assert "## ELI5" in rendered
    assert "current library" in rendered
    assert "Rich-source rate" in rendered
    assert "Preview coverage" in rendered
    assert "Terminal failure rate" in rendered
    assert "Reliability metrics currently reflect all current job-backed index_paper rows only" in rendered


def test_run_indexing_census_writes_json_and_markdown_reports(monkeypatch, tmp_path):
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)

    class FakeObjectStore:
        def exists(self, path: str) -> bool:
            return path == "papers/paper_a/preview/first-page-top.webp"

    monkeypatch.setattr("paperbase.dev_benchmarks._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "paperbase.dev_benchmarks._load_iq1_rows",
        lambda current_settings: [
            {
                "paper_id": "paper_a",
                "primary_category": "cs.AI",
                "published": "2024-01-01",
                "source_type": "html",
                "index_status": "indexed",
                "created_at": now - timedelta(minutes=15),
                "indexed_at": now - timedelta(minutes=5),
                "has_paper_embedding": True,
                "total_chunks": 4,
                "chunks_with_embedding": 4,
                "section_count": 2,
                "figure_caption_count": 0,
                "table_count": 0,
                "full_text_count": 1,
            }
        ],
    )
    monkeypatch.setattr("paperbase.dev_benchmarks.build_object_store", lambda current_settings: FakeObjectStore())
    monkeypatch.setattr(
        "paperbase.dev_benchmarks._load_iq1_index_job_stats",
        lambda current_settings: {
            "total_jobs": 1,
            "completed_jobs": 1,
            "dead_jobs": 0,
            "legacy_failed_jobs": 0,
            "pending_jobs": 0,
            "running_jobs": 0,
            "retried_jobs": 0,
        },
    )

    exit_code = run_indexing_census(settings=settings)

    assert exit_code == 0

    json_report = tmp_path / "benchmarks" / "out" / "iq1.current.json"
    markdown_report = tmp_path / "benchmarks" / "out" / "iq1.current.md"
    assert json_report.exists()
    assert markdown_report.exists()

    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["population"]["total_papers"] == 1
    assert payload["artifact_completeness"]["papers_with_preview"] == 1
    assert "# IQ-1 Indexing Census" in markdown_report.read_text(encoding="utf-8")
