"""Benchmark and regression-report helpers used by ``paperbase-dev``."""

from __future__ import annotations

import cProfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import pstats
from statistics import median
import subprocess
import time
from typing import Any
from xml.etree import ElementTree as ET

import psycopg
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from . import service as service_module
from . import sources
from .config import Settings, repo_root
from .extractors import ExtractionOutput, FigureArtifact, RawArtifact, SectionArtifact, TableArtifact
from .models import CreateChatThreadRequest, PaperMetadata, SearchRequest, SearchSectionRequest, SendChatMessageRequest, SignupRequest
from .repository import build_repository
from .service import PaperbaseService
from .storage import build_object_store


BENCHMARK_ENV_LABEL = "local-dev"
BENCHMARK_QUERY_TOKEN = "zzbench"
BENCHMARK_USER_EMAIL = "bench+profile@paperbase.local"


def _repo_root() -> Path:
    root = repo_root()
    if root is None:
        raise RuntimeError("Could not locate the repo root.")
    return root


def _benchmarks_dir() -> Path:
    return _repo_root() / "benchmarks"


def _baseline_dir() -> Path:
    return _benchmarks_dir() / "baselines"


def _out_dir() -> Path:
    return _benchmarks_dir() / "out"


def _test_baseline_path() -> Path:
    return _baseline_dir() / f"test-times.{BENCHMARK_ENV_LABEL}.json"


def _test_baseline_md_path() -> Path:
    return _baseline_dir() / f"test-times.{BENCHMARK_ENV_LABEL}.md"


def _test_current_path() -> Path:
    return _out_dir() / "test-times.current.json"


def _test_current_md_path() -> Path:
    return _out_dir() / "test-times.current.md"


def _profile_baseline_path() -> Path:
    return _baseline_dir() / f"hotpaths.{BENCHMARK_ENV_LABEL}.json"


def _profile_baseline_md_path() -> Path:
    return _baseline_dir() / f"hotpaths.{BENCHMARK_ENV_LABEL}.md"


def _profile_current_path() -> Path:
    return _out_dir() / "hotpaths.current.json"


def _profile_current_md_path() -> Path:
    return _out_dir() / "hotpaths.current.md"


def _iq1_current_path() -> Path:
    return _out_dir() / "iq1.current.json"


def _iq1_current_md_path() -> Path:
    return _out_dir() / "iq1.current.md"


def _ensure_benchmark_dirs() -> None:
    _baseline_dir().mkdir(parents=True, exist_ok=True)
    _out_dir().mkdir(parents=True, exist_ok=True)


def _run_command(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
    except Exception:
        return None
    return completed.stdout.strip() or None


def _git_sha() -> str | None:
    return _run_command(["git", "rev-parse", "--short", "HEAD"])


def _node_version() -> str | None:
    return _run_command(["node", "--version"])


def _python_version() -> str:
    return subprocess.run(["python3", "--version"], check=False, capture_output=True, text=True).stdout.strip() or ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _format_milliseconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}ms"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if upper == lower:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _benchmark_runtime() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
    }


def _metadata(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": BENCHMARK_ENV_LABEL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": _git_sha(),
        "python_version": _python_version(),
        "node_version": _node_version(),
    }


def _parse_pytest_junit(path: Path, suite: str) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tests: dict[str, float] = {}
    for case in root.iter("testcase"):
        classname = (case.attrib.get("classname") or Path(path).stem).strip()
        name = (case.attrib.get("name") or "unknown").strip()
        test_id = f"{suite}::{classname}::{name}"
        tests[test_id] = _safe_float(case.attrib.get("time"))
    return {
        "suite": suite,
        "seconds": sum(tests.values()),
        "tests": tests,
    }


def _parse_vitest_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tests: dict[str, float] = {}
    for result in payload.get("testResults", []):
        suite_name = str(result.get("name") or "chat")
        for assertion in result.get("assertionResults", []):
            full_name = str(assertion.get("fullName") or assertion.get("title") or "unknown")
            duration_ms = _safe_float(assertion.get("duration"))
            tests[f"chat::{suite_name}::{full_name}"] = duration_ms / 1000.0
    return {
        "suite": "chat",
        "seconds": sum(tests.values()),
        "tests": tests,
    }


def _top_slowest_tests(tests: dict[str, float], *, limit: int = 15) -> list[dict[str, Any]]:
    ordered = sorted(tests.items(), key=lambda item: item[1], reverse=True)
    return [{"test_id": test_id, "seconds": seconds} for test_id, seconds in ordered[:limit]]


def _test_deltas(current: dict[str, float], baseline: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    slower: list[dict[str, Any]] = []
    new_tests: list[dict[str, Any]] = []
    removed_tests: list[str] = []

    for test_id, current_seconds in current.items():
        previous_seconds = baseline.get(test_id)
        if previous_seconds is None:
            new_tests.append({"test_id": test_id, "seconds": current_seconds})
            continue
        delta = current_seconds - previous_seconds
        if delta <= 0:
            continue
        percent = ((delta / previous_seconds) * 100.0) if previous_seconds > 0 else None
        slower.append(
            {
                "test_id": test_id,
                "current_seconds": current_seconds,
                "baseline_seconds": previous_seconds,
                "delta_seconds": delta,
                "delta_percent": percent,
            }
        )

    for test_id in baseline:
        if test_id not in current:
            removed_tests.append(test_id)

    slower.sort(key=lambda item: (item["delta_seconds"], item["current_seconds"]), reverse=True)
    new_tests.sort(key=lambda item: item["seconds"], reverse=True)
    removed_tests.sort()
    return {
        "slower": slower[:20],
        "new_tests": new_tests[:20],
        "removed_tests": [{"test_id": test_id} for test_id in removed_tests[:20]],
    }


def _render_test_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Test Timing Report",
        "",
        f"- Generated: `{report['metadata']['generated_at']}`",
        f"- Git SHA: `{report['metadata'].get('git_sha') or 'unknown'}`",
        "",
        "## Suite Totals",
        "",
        "| Suite | Tests | Total |",
        "| --- | ---: | ---: |",
    ]
    for suite_name, suite in report["suites"].items():
        lines.append(
            f"| {suite_name} | {len(suite['tests'])} | {_format_seconds(suite['seconds'])} |"
        )

    lines.extend(
        [
            "",
            "## Slowest Tests",
            "",
            "| Test | Seconds |",
            "| --- | ---: |",
        ]
    )
    for item in report["slowest_tests"]:
        lines.append(f"| `{item['test_id']}` | {_format_seconds(item['seconds'])} |")

    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                "## Slower Than Baseline",
                "",
                "| Test | Current | Baseline | Delta |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for item in comparison["slower"]:
            lines.append(
                f"| `{item['test_id']}` | {_format_seconds(item['current_seconds'])} | "
                f"{_format_seconds(item['baseline_seconds'])} | {_format_seconds(item['delta_seconds'])} |"
            )
        if not comparison["slower"]:
            lines.append("| _No slower tests_ | n/a | n/a | n/a |")

    return "\n".join(lines)


def _psycopg_dsn(settings: Settings) -> str:
    return make_url(settings.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _category_family(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return "unknown"
    return normalized.split(".", 1)[0] or "unknown"


def _published_year(value: str | None) -> str:
    normalized = (value or "").strip()
    year = normalized[:4]
    return year if year.isdigit() else "unknown"


def _distribution(counter: Counter[str], *, denominator: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        rows.append(
            {
                "key": key,
                "count": count,
                "rate": _ratio(count, denominator),
            }
        )
    return rows


def _preview_paper_ids(*, paper_ids: list[str], object_store) -> set[str]:
    preview_ids: set[str] = set()
    for paper_id in paper_ids:
        if object_store.exists(f"papers/{paper_id}/preview/first-page-top.webp"):
            preview_ids.add(paper_id)
    return preview_ids


def _load_iq1_rows(settings: Settings) -> list[dict[str, Any]]:
    sql = """
        SELECT
            p.paper_id,
            p.arxiv_id,
            p.primary_category,
            p.published,
            p.source_type,
            p.index_status,
            p.created_at,
            p.indexed_at,
            (p.embedding IS NOT NULL) AS has_paper_embedding,
            COALESCE(chunk_stats.total_chunks, 0) AS total_chunks,
            COALESCE(chunk_stats.chunks_with_embedding, 0) AS chunks_with_embedding,
            COALESCE(chunk_stats.section_count, 0) AS section_count,
            COALESCE(chunk_stats.figure_caption_count, 0) AS figure_caption_count,
            COALESCE(chunk_stats.table_count, 0) AS table_count,
            COALESCE(chunk_stats.full_text_count, 0) AS full_text_count
        FROM papers p
        LEFT JOIN (
            SELECT
                paper_id,
                COUNT(*) AS total_chunks,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS chunks_with_embedding,
                COUNT(DISTINCT path) FILTER (WHERE path LIKE 'sections/%') AS section_count,
                COUNT(*) FILTER (WHERE chunk_kind = 'figure_caption') AS figure_caption_count,
                COUNT(*) FILTER (WHERE chunk_kind = 'table') AS table_count,
                COUNT(*) FILTER (WHERE chunk_kind = 'full_text') AS full_text_count
            FROM text_chunks
            GROUP BY paper_id
        ) AS chunk_stats
          ON chunk_stats.paper_id = p.paper_id
        ORDER BY p.paper_id ASC
    """
    with psycopg.connect(_psycopg_dsn(settings), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def _load_iq1_index_job_stats(settings: Settings) -> dict[str, int]:
    sql = """
        SELECT
            COUNT(*) AS total_jobs,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed_jobs,
            COUNT(*) FILTER (WHERE status = 'dead') AS dead_jobs,
            COUNT(*) FILTER (WHERE status = 'failed') AS legacy_failed_jobs,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending_jobs,
            COUNT(*) FILTER (WHERE status = 'running') AS running_jobs,
            COUNT(*) FILTER (WHERE attempts > 1) AS retried_jobs
        FROM jobs
        WHERE job_type = 'index_paper'
    """
    with psycopg.connect(_psycopg_dsn(settings), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone() or {}
    return {
        "total_jobs": _safe_int(row.get("total_jobs")),
        "completed_jobs": _safe_int(row.get("completed_jobs")),
        "dead_jobs": _safe_int(row.get("dead_jobs")),
        "legacy_failed_jobs": _safe_int(row.get("legacy_failed_jobs")),
        "pending_jobs": _safe_int(row.get("pending_jobs")),
        "running_jobs": _safe_int(row.get("running_jobs")),
        "retried_jobs": _safe_int(row.get("retried_jobs")),
    }


def _build_iq1_report(
    *,
    rows: list[dict[str, Any]],
    preview_paper_ids: set[str],
    index_job_stats: dict[str, int],
    settings: Settings,
) -> dict[str, Any]:
    total_papers = len(rows)
    status_counts = Counter(str(row.get("index_status") or "unknown") for row in rows)
    indexed_rows = [row for row in rows if str(row.get("index_status") or "") == "indexed"]
    indexed_papers = len(indexed_rows)

    source_types = Counter(str(row.get("source_type") or "unknown") for row in indexed_rows)
    category_families = Counter(_category_family(str(row.get("primary_category") or "")) for row in indexed_rows)
    published_years = Counter(_published_year(str(row.get("published") or "")) for row in indexed_rows)

    papers_with_sections = sum(1 for row in indexed_rows if _safe_int(row.get("section_count")) > 0)
    papers_with_five_sections = sum(1 for row in indexed_rows if _safe_int(row.get("section_count")) >= 5)
    papers_with_figure_captions = sum(1 for row in indexed_rows if _safe_int(row.get("figure_caption_count")) > 0)
    papers_with_tables = sum(1 for row in indexed_rows if _safe_int(row.get("table_count")) > 0)
    papers_with_full_text = sum(1 for row in indexed_rows if _safe_int(row.get("full_text_count")) > 0)
    papers_with_preview = sum(1 for row in indexed_rows if str(row.get("paper_id")) in preview_paper_ids)

    section_counts = [_safe_int(row.get("section_count")) for row in indexed_rows]
    chunk_counts = [_safe_int(row.get("total_chunks")) for row in indexed_rows]
    total_chunks = sum(chunk_counts)
    chunks_with_embedding = sum(_safe_int(row.get("chunks_with_embedding")) for row in indexed_rows)
    papers_with_embedding = sum(1 for row in indexed_rows if bool(row.get("has_paper_embedding")))

    latency_seconds: list[float] = []
    for row in indexed_rows:
        created_at = row.get("created_at")
        indexed_at = row.get("indexed_at")
        if created_at is None or indexed_at is None:
            continue
        delta = (indexed_at - created_at).total_seconds()
        if delta >= 0:
            latency_seconds.append(delta)

    rich_source_count = sum(source_types.get(kind, 0) for kind in ("html", "latex", "pdf-docling"))
    terminal_failed_jobs = index_job_stats["dead_jobs"] + index_job_stats.get("legacy_failed_jobs", 0)
    terminal_jobs = index_job_stats["completed_jobs"] + terminal_failed_jobs
    active_jobs = index_job_stats["pending_jobs"] + index_job_stats["running_jobs"]

    return {
        "metadata": _metadata("iq-1-indexing-census"),
        "runtime": _benchmark_runtime(),
        "fixture": {
            "database_backend": make_url(settings.database_url).get_backend_name(),
            "storage_backend": settings.storage_backend,
            "population_window": "current-local-snapshot",
            "reliability_window": "all-current-index_paper-job-rows",
            "preview_probe_path": "papers/<paper_id>/preview/first-page-top.webp",
        },
        "population": {
            "total_papers": total_papers,
            "indexed_papers": indexed_papers,
            "status_counts": dict(status_counts),
        },
        "source_richness": {
            "distribution": _distribution(source_types, denominator=indexed_papers),
            "rich_index_rate": _ratio(rich_source_count, indexed_papers),
            "abstract_only_rate": _ratio(source_types.get("abstract-only", 0), indexed_papers),
        },
        "artifact_completeness": {
            "papers_with_sections": papers_with_sections,
            "papers_with_sections_rate": _ratio(papers_with_sections, indexed_papers),
            "papers_with_five_sections": papers_with_five_sections,
            "papers_with_five_sections_rate": _ratio(papers_with_five_sections, indexed_papers),
            "papers_with_figure_captions": papers_with_figure_captions,
            "papers_with_figure_captions_rate": _ratio(papers_with_figure_captions, indexed_papers),
            "papers_with_tables": papers_with_tables,
            "papers_with_tables_rate": _ratio(papers_with_tables, indexed_papers),
            "papers_with_full_text": papers_with_full_text,
            "papers_with_full_text_rate": _ratio(papers_with_full_text, indexed_papers),
            "papers_with_preview": papers_with_preview,
            "papers_with_preview_rate": _ratio(papers_with_preview, indexed_papers),
            "median_sections_per_paper": float(median(section_counts)) if section_counts else None,
            "median_chunks_per_paper": float(median(chunk_counts)) if chunk_counts else None,
        },
        "embedding_coverage": {
            "papers_with_embedding": papers_with_embedding,
            "paper_embedding_rate": _ratio(papers_with_embedding, indexed_papers),
            "papers_missing_embedding": max(indexed_papers - papers_with_embedding, 0),
            "total_chunks": total_chunks,
            "chunks_with_embedding": chunks_with_embedding,
            "chunk_embedding_rate": _ratio(chunks_with_embedding, total_chunks),
            "chunks_missing_embedding": max(total_chunks - chunks_with_embedding, 0),
        },
        "freshness": {
            "measured_papers": len(latency_seconds),
            "created_to_indexed_p50_seconds": _percentile(latency_seconds, 50),
            "created_to_indexed_p95_seconds": _percentile(latency_seconds, 95),
            "created_to_indexed_p99_seconds": _percentile(latency_seconds, 99),
        },
        "reliability": {
            **index_job_stats,
            "terminal_failed_jobs": terminal_failed_jobs,
            "active_jobs": active_jobs,
            "terminal_success_rate": _ratio(index_job_stats["completed_jobs"], terminal_jobs),
            "terminal_failure_rate": _ratio(terminal_failed_jobs, terminal_jobs),
            "retry_rate": _ratio(index_job_stats.get("retried_jobs", 0), index_job_stats["total_jobs"]),
            "active_rate": _ratio(active_jobs, index_job_stats["total_jobs"]),
        },
        "strata": {
            "source_type": _distribution(source_types, denominator=indexed_papers),
            "category_family": _distribution(category_families, denominator=indexed_papers),
            "published_year": _distribution(published_years, denominator=indexed_papers),
        },
        "notes": [
            "IQ-1 is a census-style corpus health check over the current local paper snapshot.",
            "Freshness is measured as papers.created_at -> papers.indexed_at; this approximates first-seen to indexed for the current schema.",
            "Reliability metrics currently reflect all current job-backed index_paper rows only; direct non-job indexing paths are not counted there.",
            "Legacy jobs with status=failed are included in the terminal failure counts so older rows do not make the census look healthier than it is.",
            "Preview coverage is measured via object-store existence checks on preview/first-page-top.webp.",
        ],
    }


def _render_iq1_markdown(report: dict[str, Any]) -> str:
    population = report["population"]
    source = report["source_richness"]
    artifacts = report["artifact_completeness"]
    embeddings = report["embedding_coverage"]
    freshness = report["freshness"]
    reliability = report["reliability"]

    lines = [
        "# IQ-1 Indexing Census",
        "",
        f"- Generated: `{report['metadata']['generated_at']}`",
        f"- Git SHA: `{report['metadata'].get('git_sha') or 'unknown'}`",
        f"- Storage backend: `{report['fixture']['storage_backend']}`",
        "",
        "## ELI5",
        "",
        "IQ-1 is a census of the current library. It asks: how many papers are really indexed, how rich are those indexed artifacts, how much of the corpus is searchable by embeddings, and how healthy is the indexing pipeline overall?",
        "",
        "## Population",
        "",
        f"- Total papers: `{population['total_papers']}`",
        f"- Indexed papers: `{population['indexed_papers']}`",
        f"- Status counts: `{population['status_counts']}`",
        "",
        "## Headline Metrics",
        "",
        f"- Rich-source rate: `{_format_ratio(source['rich_index_rate'])}`",
        f"- Abstract-only rate: `{_format_ratio(source['abstract_only_rate'])}`",
        f"- Papers with >=1 section: `{_format_ratio(artifacts['papers_with_sections_rate'])}`",
        f"- Papers with >=5 sections: `{_format_ratio(artifacts['papers_with_five_sections_rate'])}`",
        f"- Papers with figure captions: `{_format_ratio(artifacts['papers_with_figure_captions_rate'])}`",
        f"- Papers with tables: `{_format_ratio(artifacts['papers_with_tables_rate'])}`",
        f"- Preview coverage: `{_format_ratio(artifacts['papers_with_preview_rate'])}`",
        f"- Paper embedding coverage: `{_format_ratio(embeddings['paper_embedding_rate'])}`",
        f"- Chunk embedding coverage: `{_format_ratio(embeddings['chunk_embedding_rate'])}`",
        f"- Median sections per indexed paper: `{artifacts['median_sections_per_paper']}`",
        f"- Median chunks per indexed paper: `{artifacts['median_chunks_per_paper']}`",
        "",
        "## Freshness",
        "",
        f"- Measured papers: `{freshness['measured_papers']}`",
        f"- created->indexed p50: `{_format_seconds(freshness['created_to_indexed_p50_seconds'] or 0.0) if freshness['created_to_indexed_p50_seconds'] is not None else 'n/a'}`",
        f"- created->indexed p95: `{_format_seconds(freshness['created_to_indexed_p95_seconds'] or 0.0) if freshness['created_to_indexed_p95_seconds'] is not None else 'n/a'}`",
        f"- created->indexed p99: `{_format_seconds(freshness['created_to_indexed_p99_seconds'] or 0.0) if freshness['created_to_indexed_p99_seconds'] is not None else 'n/a'}`",
        "",
        "## Job Reliability",
        "",
        f"- index_paper jobs: `{reliability['total_jobs']}`",
        f"- Completed: `{reliability['completed_jobs']}`",
        f"- Dead: `{reliability['dead_jobs']}`",
        f"- Legacy failed: `{reliability['legacy_failed_jobs']}`",
        f"- Pending: `{reliability['pending_jobs']}`",
        f"- Running: `{reliability['running_jobs']}`",
        f"- Retried at least once: `{reliability['retried_jobs']}`",
        f"- Terminal success rate: `{_format_ratio(reliability['terminal_success_rate'])}`",
        f"- Terminal failure rate: `{_format_ratio(reliability['terminal_failure_rate'])}`",
        f"- Retry rate: `{_format_ratio(reliability['retry_rate'])}`",
        f"- Active backlog rate: `{_format_ratio(reliability['active_rate'])}`",
        "",
        "## Source Types",
        "",
        "| Source type | Count | Rate |",
        "| --- | ---: | ---: |",
    ]
    for item in report["strata"]["source_type"]:
        lines.append(f"| `{item['key']}` | {item['count']} | {_format_ratio(item['rate'])} |")
    if not report["strata"]["source_type"]:
        lines.append("| _No indexed papers_ | 0 | n/a |")

    lines.extend(
        [
            "",
            "## Category Families",
            "",
            "| Family | Count | Rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for item in report["strata"]["category_family"]:
        lines.append(f"| `{item['key']}` | {item['count']} | {_format_ratio(item['rate'])} |")
    if not report["strata"]["category_family"]:
        lines.append("| _No indexed papers_ | 0 | n/a |")

    lines.extend(
        [
            "",
            "## Published Years",
            "",
            "| Year | Count | Rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for item in report["strata"]["published_year"]:
        lines.append(f"| `{item['key']}` | {item['count']} | {_format_ratio(item['rate'])} |")
    if not report["strata"]["published_year"]:
        lines.append("| _No indexed papers_ | 0 | n/a |")

    lines.extend(["", "## Notes", ""])
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines)


def run_indexing_census(*, settings: Settings) -> int:
    _ensure_benchmark_dirs()
    rows = _load_iq1_rows(settings)
    object_store = build_object_store(settings)
    preview_paper_ids = _preview_paper_ids(
        paper_ids=[str(row.get("paper_id")) for row in rows if row.get("paper_id")],
        object_store=object_store,
    )
    index_job_stats = _load_iq1_index_job_stats(settings)
    report = _build_iq1_report(
        rows=rows,
        preview_paper_ids=preview_paper_ids,
        index_job_stats=index_job_stats,
        settings=settings,
    )
    _write_json(_iq1_current_path(), report)
    _write_text(_iq1_current_md_path(), _render_iq1_markdown(report))

    population = report["population"]
    print(f"Wrote indexing census JSON: {_iq1_current_path()}")
    print(f"Wrote indexing census Markdown: {_iq1_current_md_path()}")
    print(f"Indexed papers:         {population['indexed_papers']}/{population['total_papers']}")
    print(
        "Rich-source rate:      "
        f"{_format_ratio(report['source_richness']['rich_index_rate'])}"
    )
    print(
        "Paper embeddings:      "
        f"{_format_ratio(report['embedding_coverage']['paper_embedding_rate'])}"
    )
    print(
        "Chunk embeddings:      "
        f"{_format_ratio(report['embedding_coverage']['chunk_embedding_rate'])}"
    )
    print(
        "Preview coverage:      "
        f"{_format_ratio(report['artifact_completeness']['papers_with_preview_rate'])}"
    )
    print(
        "Index job success:     "
        f"{_format_ratio(report['reliability']['terminal_success_rate'])}"
    )
    print(
        "Index job retry rate:  "
        f"{_format_ratio(report['reliability']['retry_rate'])}"
    )
    return 0


def record_test_times(
    *,
    backend_junit: Path,
    plugin_junit: Path,
    chat_json: Path,
    update_baseline: bool,
) -> int:
    _ensure_benchmark_dirs()
    suites = {
        "backend": _parse_pytest_junit(backend_junit, "backend"),
        "plugin": _parse_pytest_junit(plugin_junit, "plugin"),
        "chat": _parse_vitest_json(chat_json),
    }
    flat_tests: dict[str, float] = {}
    for suite in suites.values():
        flat_tests.update(suite["tests"])

    report = {
        "metadata": _metadata("test-times"),
        "suites": suites,
        "tests": flat_tests,
        "slowest_tests": _top_slowest_tests(flat_tests),
    }

    baseline_path = _test_baseline_path()
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        report["comparison"] = _test_deltas(report["tests"], baseline.get("tests", {}))

    _write_json(_test_current_path(), report)
    _write_text(_test_current_md_path(), _render_test_markdown(report))

    if update_baseline or not baseline_path.exists():
        _write_json(baseline_path, report)
        _write_text(_test_baseline_md_path(), _render_test_markdown(report))
        print(f"Updated test timing baseline at {baseline_path}")
        return 0

    comparison = report.get("comparison", {})
    slower = comparison.get("slower", [])
    if slower:
        print("Slower tests versus baseline:")
        for item in slower[:10]:
            print(
                f"  {item['test_id']}: {_format_seconds(item['current_seconds'])} "
                f"(baseline {_format_seconds(item['baseline_seconds'])}, "
                f"+{_format_seconds(item['delta_seconds'])})"
            )
    else:
        print("No slower tests versus the committed baseline.")
    return 0


@dataclass(slots=True)
class _ProfileContext:
    settings: Settings
    service: PaperbaseService
    root: Path


@dataclass(slots=True)
class _VectorDimensions:
    paper: int
    chunk: int


class _DeterministicEmbeddings:
    def __init__(self, *, query_dimension: int, paper_dimension: int, chunk_dimension: int) -> None:
        self.query_dimension = query_dimension
        self.paper_dimension = paper_dimension
        self.chunk_dimension = chunk_dimension

    def _vector(self, text: str, *, dimension: int) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], byteorder="big", signed=False) or 1
        return [((seed + (index * 1315423911)) % 1000) / 1000.0 for index in range(dimension)]

    def encode(self, text: str) -> list[float] | None:
        return self._vector(text, dimension=self.query_dimension)

    def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        return [self._vector(text, dimension=self.chunk_dimension) for text in texts]

    def encode_paper(self, title: str, body: str) -> list[float] | None:
        return self._vector(f"{title}\n{body}", dimension=self.paper_dimension)

    def encode_paper_batch(self, pairs: list[tuple[str, str]]) -> list[list[float]] | None:
        return [
            self._vector(f"{title}\n{body}", dimension=self.paper_dimension)
            for title, body in pairs
        ]


class _DeterministicReranker:
    def __init__(self, top_k: int = 8) -> None:
        self.top_k = top_k

    def enabled(self) -> bool:
        return True

    def score(self, query: str, documents: list[str]) -> list[float] | None:
        query_terms = {token for token in query.lower().split() if token}
        scores: list[float] = []
        for document in documents:
            terms = document.lower().split()
            overlap = sum(1 for token in terms if token in query_terms)
            scores.append(float(overlap) + (len(document) / 10_000.0))
        return scores


class _ProfileChatModel:
    def create_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, str] | None = None,
        parallel_tool_calls: bool | None = None,
    ) -> dict[str, Any]:
        del api_key, system_prompt, tool_choice, parallel_tool_calls
        has_tool_results = any(
            message.get("role") == "user"
            and isinstance(message.get("content"), list)
            and any(block.get("type") == "tool_result" for block in message.get("content"))
            for message in messages
        )
        if tools and not has_tool_results:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_profile_search_sections",
                        "name": "paperbase_search_sections",
                        "input": {"query": f"{BENCHMARK_QUERY_TOKEN} grounding", "limit": 3},
                    }
                ],
                "stop_reason": "tool_use",
            }
        return {
            "content": [{"type": "text", "text": "Profiled grounded answer [S1]."}],
            "stop_reason": "end_turn",
        }

    def stream_message(
        self,
        *,
        api_key: str,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, str] | None = None,
        parallel_tool_calls: bool | None = None,
    ):
        del api_key, messages, system_prompt, tools, tool_choice, parallel_tool_calls
        yield "Profiled grounded "
        yield "answer [S1]."


def _benchmark_metadata(arxiv_id: str, *, title: str, abstract: str, published: str) -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors="Bench User, Profile User",
        abstract=abstract,
        tldr=abstract,
        categories="cs.AI",
        primary_category="cs.AI",
        published=published,
        updated=published,
        pdf_url=f"https://example.test/{arxiv_id}.pdf",
        html_url=f"https://example.test/{arxiv_id}",
        citation_count=25,
        influential_citation_count=10,
    )


def _benchmark_extraction(tag: str, *, include_table: bool = False, include_figure: bool = False) -> ExtractionOutput:
    title = f"{BENCHMARK_QUERY_TOKEN} {tag}"
    body = (
        f"## Methods\n\n{title} methods describe grounding, retrieval, tables, and profiling.\n\n"
        f"## Results\n\n{title} results compare benchmarks, worker scheduling, and regression tracking."
    )
    tables = [
        TableArtifact(
            table_id="table-1",
            caption=f"{title} table",
            rows=[["Metric", "Score"], [f"{BENCHMARK_QUERY_TOKEN}-metric", "1.0"]],
        )
    ] if include_table else []
    figures = [
        FigureArtifact(
            figure_id="figure-1",
            caption=f"{title} figure",
            file_extension="png",
            content_type="image/png",
            image_bytes=b"PNG",
        )
    ] if include_figure else []
    return ExtractionOutput(
        source_type="html",
        text_markdown=body,
        sections=[
            SectionArtifact(title="Methods", slug="methods", markdown=body),
            SectionArtifact(title="Results", slug="results", markdown=body),
        ],
        tables=tables,
        figures=figures,
        raw_artifacts=[RawArtifact(path="raw/source.txt", data=title.encode("utf-8"), content_type="text/plain")],
    )


@contextmanager
def _patched_attr(target: Any, attribute: str, replacement: Any):
    original = getattr(target, attribute)
    setattr(target, attribute, replacement)
    try:
        yield
    finally:
        setattr(target, attribute, original)


@contextmanager
def _patched_indexing(extractions: dict[str, ExtractionOutput]):
    with (
        _patched_attr(sources, "enrich_with_semantic_scholar", lambda papers, **_: papers),
        _patched_attr(
            service_module,
            "index_paper_artifacts",
            lambda paper: extractions[paper.arxiv_id],
        ),
    ):
        yield


def _profile_rows(stats: pstats.Stats, *, key: str, limit: int = 15) -> list[dict[str, Any]]:
    rows = []
    for (filename, line_no, function_name), (primitive_calls, total_calls, total_time, cumulative_time, _) in stats.stats.items():
        rows.append(
            {
                "function": function_name,
                "filename": filename,
                "line": line_no,
                "primitive_calls": primitive_calls,
                "total_calls": total_calls,
                "total_time": total_time,
                "cumulative_time": cumulative_time,
            }
        )
    sort_field = "cumulative_time" if key == "cumulative" else "total_time"
    rows.sort(key=lambda item: item[sort_field], reverse=True)
    return rows[:limit]


def _profile_scenario(name: str, func) -> tuple[dict[str, Any], Any]:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    result = profiler.runcall(func)
    elapsed = time.perf_counter() - started
    stats = pstats.Stats(profiler)
    scenario = {
        "wall_time_seconds": elapsed,
        "top_cumulative": _profile_rows(stats, key="cumulative"),
        "top_self_time": _profile_rows(stats, key="self"),
    }
    print(f"Profiled {name}: {_format_seconds(elapsed)}")
    return scenario, result


def _vector_literal(dimension: int) -> str:
    return "[" + ",".join("0.0" for _ in range(dimension)) + "]"


def _connect(settings: Settings) -> psycopg.Connection:
    url = make_url(settings.database_url)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    return psycopg.connect(dsn, autocommit=True)


def _detect_vector_dimensions(settings: Settings) -> _VectorDimensions:
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'papers'::regclass AND attname = 'embedding'"
            )
            paper_row = cursor.fetchone()
            cursor.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'text_chunks'::regclass AND attname = 'embedding'"
            )
            chunk_row = cursor.fetchone()
    paper_dimension = int(paper_row[0]) if paper_row and paper_row[0] and paper_row[0] > 0 else settings.embedding_dim
    chunk_dimension = int(chunk_row[0]) if chunk_row and chunk_row[0] and chunk_row[0] > 0 else settings.embedding_dim
    return _VectorDimensions(paper=paper_dimension, chunk=chunk_dimension)


def _cleanup_benchmark_rows(settings: Settings, *, object_store_prefix: str = "papers/arxiv:bench") -> None:
    object_store = build_object_store(settings)
    object_store.delete_prefix(object_store_prefix)

    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM chat_messages WHERE thread_id IN (SELECT thread_id FROM chat_threads WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local'))")
            cursor.execute("DELETE FROM chat_threads WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM uploaded_document_chunks WHERE document_id IN (SELECT document_id FROM uploaded_documents WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local'))")
            cursor.execute("DELETE FROM uploaded_documents WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM browser_sessions WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM anthropic_api_keys WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM google_identities WHERE user_id IN (SELECT user_id FROM browser_users WHERE email LIKE 'bench+%@paperbase.local')")
            cursor.execute("DELETE FROM browser_users WHERE email LIKE 'bench+%@paperbase.local'")
            cursor.execute("DELETE FROM result_items WHERE result_set_id IN (SELECT result_set_id FROM result_sets WHERE query LIKE 'zzbench%' OR pattern LIKE 'zzbench%')")
            cursor.execute("DELETE FROM result_sets WHERE query LIKE 'zzbench%' OR pattern LIKE 'zzbench%'")
            cursor.execute("DELETE FROM jobs WHERE payload::text LIKE '%bench/%'")
            cursor.execute("DELETE FROM paper_refs WHERE src_paper_id LIKE 'arxiv:bench/%' OR dst_paper_id LIKE 'arxiv:bench/%' OR dst_arxiv_id LIKE 'bench/%'")
            cursor.execute("DELETE FROM text_chunks WHERE paper_id LIKE 'arxiv:bench/%'")
            cursor.execute("DELETE FROM papers WHERE paper_id LIKE 'arxiv:bench/%' OR arxiv_id LIKE 'bench/%'")


def _build_sql_profile_context(
    settings: Settings,
    *,
    vector_dimensions: _VectorDimensions,
    query_dimension: int,
) -> _ProfileContext:
    repository = build_repository(settings)
    repository.init()
    service = PaperbaseService(
        settings=settings,
        repository=repository,
        object_store=build_object_store(settings),
        chat_model=_ProfileChatModel(),
    )
    service.embeddings = _DeterministicEmbeddings(
        query_dimension=query_dimension,
        paper_dimension=vector_dimensions.paper,
        chunk_dimension=vector_dimensions.chunk,
    )
    service.reranker = _DeterministicReranker(top_k=min(8, settings.rerank_top_k))
    return _ProfileContext(settings=settings, service=service, root=_repo_root())


def _build_memory_profile_service(
    settings: Settings,
    *,
    worker_settings: dict[str, Any] | None = None,
) -> PaperbaseService:
    memory_settings = settings.model_copy(
        update={
            "database_url": "memory://",
            "storage_backend": "memory",
            "rerank_model": "",
            **(worker_settings or {}),
        }
    )
    service = PaperbaseService(
        settings=memory_settings,
        repository=build_repository(memory_settings),
        object_store=build_object_store(memory_settings),
        chat_model=_ProfileChatModel(),
    )
    service.embeddings = _DeterministicEmbeddings(
        query_dimension=settings.embedding_dim,
        paper_dimension=settings.embedding_dim,
        chunk_dimension=settings.embedding_dim,
    )
    service.reranker = _DeterministicReranker()
    return service


def _seed_search_profile_data(service: PaperbaseService) -> dict[str, Any]:
    repository = service.repository
    seeded_ids: list[str] = []
    for index in range(1, 7):
        arxiv_id = f"bench/search/{index:04d}"
        title = f"{BENCHMARK_QUERY_TOKEN} grounding search paper {index}"
        abstract = f"{BENCHMARK_QUERY_TOKEN} grounding abstract {index} with retrieval and regression tracking."
        metadata = _benchmark_metadata(
            arxiv_id,
            title=title,
            abstract=abstract,
            published=f"2026-01-{index:02d}",
        )
        stored = repository.upsert_paper(
            metadata,
            search_document=f"{title}\n\n{abstract}\n\n{BENCHMARK_QUERY_TOKEN} retrieval benchmark profile",
            embedding=None,
        )
        chunk_rows = [
            {
                "path": "sections/methods.md",
                "chunk_kind": "section",
                "content": f"{BENCHMARK_QUERY_TOKEN} grounding methods section {index}",
                "chunk_index": 0,
                "section_depth": 1,
                "section_order": 0,
            },
            {
                "path": "sections/results.md",
                "chunk_kind": "section",
                "content": f"{BENCHMARK_QUERY_TOKEN} grounding results section {index}",
                "chunk_index": 1,
                "section_depth": 1,
                "section_order": 1,
            },
        ]
        if index % 2 == 0:
            chunk_rows.append(
                {
                    "path": "tables/table-1.json",
                    "chunk_kind": "table",
                    "content": f"{BENCHMARK_QUERY_TOKEN} table benchmark rows {index}",
                    "chunk_index": 0,
                    "section_depth": 1,
                    "section_order": 2,
                }
            )
        if index % 3 == 0:
            chunk_rows.append(
                {
                    "path": "figures/figure-1.txt",
                    "chunk_kind": "figure_caption",
                    "content": f"{BENCHMARK_QUERY_TOKEN} figure caption {index}",
                    "chunk_index": 0,
                    "section_depth": 1,
                    "section_order": 3,
                }
            )
        embeddings = service.embeddings.encode_batch([chunk["content"] for chunk in chunk_rows]) or []
        for chunk, embedding in zip(chunk_rows, embeddings, strict=False):
            chunk["embedding"] = embedding
        repository.replace_text_chunks(stored.paper_id, chunk_rows)
        seeded_ids.append(stored.paper_id)

    return {
        "seeded_paper_ids": seeded_ids,
        "search_query": f"{BENCHMARK_QUERY_TOKEN} grounding",
        "section_query": f"{BENCHMARK_QUERY_TOKEN} table",
        "result_query": f"{BENCHMARK_QUERY_TOKEN} results",
    }


def _seed_sql_profile_data(context: _ProfileContext) -> dict[str, Any]:
    return _seed_search_profile_data(context.service)


def _seed_memory_reindex_profile(service: PaperbaseService) -> None:
    repository = service.repository
    paper = _benchmark_metadata(
        "bench/reindex/0001",
        title=f"{BENCHMARK_QUERY_TOKEN} reindex paper",
        abstract=f"{BENCHMARK_QUERY_TOKEN} reindex abstract",
        published="2026-02-01",
    )
    paper.index_status = "indexed"
    stored = repository.upsert_paper(paper, search_document=paper.abstract, embedding=None)
    repository.replace_text_chunks(
        stored.paper_id,
        [
            {
                "path": "sections/methods.md",
                "chunk_kind": "section",
                "content": f"{BENCHMARK_QUERY_TOKEN} reindex chunk methods",
                "embedding": None,
                "chunk_index": 0,
                "section_depth": 1,
                "section_order": 0,
            },
            {
                "path": "tables/table-1.json",
                "chunk_kind": "table",
                "content": f"{BENCHMARK_QUERY_TOKEN} reindex table",
                "embedding": None,
                "chunk_index": 0,
                "section_depth": 1,
                "section_order": 1,
            },
        ],
    )


def _make_explain_summary(explain_output: Any) -> dict[str, Any]:
    raw_plan = explain_output
    if isinstance(raw_plan, str):
        raw_plan = json.loads(raw_plan)
    if isinstance(raw_plan, list):
        raw_plan = raw_plan[0]
    plan = raw_plan.get("Plan", {})

    def summarize(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_type": node.get("Node Type"),
            "relation_name": node.get("Relation Name"),
            "index_name": node.get("Index Name"),
            "actual_rows": node.get("Actual Rows"),
            "actual_total_time": node.get("Actual Total Time"),
            "plans": [summarize(child) for child in node.get("Plans", [])],
        }

    return {
        "planning_time_ms": raw_plan.get("Planning Time"),
        "execution_time_ms": raw_plan.get("Execution Time"),
        "plan": summarize(plan),
    }


def _explain_one(connection: psycopg.Connection, sql_text: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_text}", params)
        raw = cursor.fetchone()[0]
    return _make_explain_summary(raw)


def _collect_sql_explains(
    settings: Settings,
    *,
    vector_dimensions: _VectorDimensions,
    search_query: str,
    paper_ids: list[str],
    result_set_id: str,
) -> dict[str, Any]:
    paper_vector_literal = _vector_literal(vector_dimensions.paper)
    chunk_vector_literal = _vector_literal(vector_dimensions.chunk)
    explains: dict[str, Any] = {}
    with _connect(settings) as connection:
        weighted_document = """
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(tldr, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(summary_short, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(summary_eli5, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(authors, '')), 'C') ||
            setweight(to_tsvector('english', coalesce(search_document, '')), 'C')
        """
        explains["search_local"] = {
            "lexical": _explain_one(
                connection,
                f"""
                SELECT paper_id
                FROM papers
                WHERE
                    {weighted_document} @@ websearch_to_tsquery('english', %s)
                    OR similarity(coalesce(title, '') || ' ' || coalesce(tldr, ''), %s) > 0.15
                ORDER BY
                    ts_rank_cd({weighted_document}, websearch_to_tsquery('english', %s)) DESC,
                    citation_count DESC,
                    published DESC
                LIMIT %s
                """,
                (search_query, search_query, search_query, 50),
            ),
            "vector": _explain_one(
                connection,
                """
                SELECT paper_id
                FROM papers
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(%s AS vector)
                LIMIT %s
                """,
                (paper_vector_literal, 50),
            ),
        }
        explains["search_sections"] = {
            "lexical": _explain_one(
                connection,
                """
                SELECT tc.chunk_id
                FROM text_chunks tc
                JOIN papers p ON p.paper_id = tc.paper_id
                WHERE tc.paper_id = ANY(%s)
                  AND (
                    to_tsvector('english', coalesce(tc.content, '')) @@ websearch_to_tsquery('english', %s)
                    OR similarity(coalesce(tc.content, ''), %s) > 0.1
                  )
                ORDER BY
                  ts_rank_cd(to_tsvector('english', coalesce(tc.content, '')), websearch_to_tsquery('english', %s)) DESC,
                  p.citation_count DESC,
                  p.published DESC
                LIMIT %s
                """,
                (paper_ids, search_query, search_query, search_query, 50),
            ),
            "vector": _explain_one(
                connection,
                """
                SELECT tc.chunk_id
                FROM text_chunks tc
                WHERE tc.paper_id = ANY(%s)
                  AND tc.embedding IS NOT NULL
                ORDER BY tc.embedding <=> CAST(%s AS vector)
                LIMIT %s
                """,
                (paper_ids, chunk_vector_literal, 50),
            ),
        }
        explains["suggest_papers"] = _explain_one(
            connection,
            """
            WITH scored AS (
                SELECT
                    paper_id,
                    arxiv_id,
                    title,
                    authors,
                    summary_short,
                    tldr,
                    citation_count,
                    published,
                    similarity(coalesce(title, ''), %s) AS title_sim,
                    similarity(coalesce(authors, ''), %s) AS authors_sim,
                    GREATEST(similarity(coalesce(summary_short, ''), %s), similarity(coalesce(tldr, ''), %s)) AS summary_sim,
                    (title ILIKE %s) AS title_contains,
                    (authors ILIKE %s) AS authors_contains,
                    (summary_short ILIKE %s OR tldr ILIKE %s) AS summary_contains
                FROM papers
                WHERE title ILIKE %s OR authors ILIKE %s OR summary_short ILIKE %s OR tldr ILIKE %s
            )
            SELECT paper_id
            FROM scored
            ORDER BY
                (CASE WHEN title ILIKE %s THEN 1 ELSE 0 END) DESC,
                GREATEST(
                    CASE WHEN title_contains THEN 1.0 ELSE title_sim END,
                    CASE WHEN authors_contains THEN 0.9 ELSE authors_sim END,
                    CASE WHEN summary_contains THEN 0.7 ELSE summary_sim END
                ) DESC,
                citation_count DESC NULLS LAST,
                published DESC
            LIMIT %s
            """,
            (
                search_query,
                search_query,
                search_query,
                search_query,
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"%{search_query}%",
                f"{search_query}%",
                10,
            ),
        )
        explains["best_chunk_for_papers"] = _explain_one(
            connection,
            """
            SELECT DISTINCT ON (paper_id)
                paper_id,
                content
            FROM (
                SELECT
                    tc.paper_id,
                    tc.content,
                    tc.chunk_kind,
                    tc.section_depth,
                    ts_rank_cd(
                        to_tsvector('english', coalesce(tc.content, '')),
                        websearch_to_tsquery('english', %s)
                    ) AS lexical_score,
                    CASE tc.chunk_kind
                        WHEN 'section' THEN 0.30
                        WHEN 'table' THEN 0.20
                        WHEN 'figure_caption' THEN 0.15
                        WHEN 'full_text' THEN 0.05
                        ELSE 0.0
                    END AS kind_score,
                    1 - (embedding <=> CAST(%s AS vector)) AS vector_score
                FROM text_chunks tc
                WHERE tc.paper_id = ANY(%s)
                  AND tc.chunk_kind <> 'full_text'
            ) scored
            ORDER BY paper_id, (0.5 * coalesce(vector_score, 0) + 0.5 * lexical_score) DESC, kind_score DESC
            """,
            (search_query, chunk_vector_literal, paper_ids),
        )
        explains["grep_result_set"] = _explain_one(
            connection,
            """
            SELECT p.paper_id, p.title
            FROM text_chunks tc
            JOIN result_items ri ON ri.paper_id = tc.paper_id
            JOIN papers p ON p.paper_id = tc.paper_id
            WHERE ri.result_set_id = %s
              AND (tc.content ~* %s OR similarity(tc.content, %s) > 0.05)
            LIMIT %s
            """,
            (result_set_id, BENCHMARK_QUERY_TOKEN, BENCHMARK_QUERY_TOKEN, 30),
        )
        explains["claim_pending_job"] = _explain_one(
            connection,
            """
            SELECT job_id
            FROM jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (),
        )
    return explains


def _profile_deltas(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    scenario_deltas: list[dict[str, Any]] = []
    for name, scenario in current.get("scenarios", {}).items():
        baseline_scenario = baseline.get("scenarios", {}).get(name)
        if not baseline_scenario:
            continue
        delta = scenario["wall_time_seconds"] - baseline_scenario.get("wall_time_seconds", 0.0)
        scenario_deltas.append(
            {
                "scenario": name,
                "current_seconds": scenario["wall_time_seconds"],
                "baseline_seconds": baseline_scenario.get("wall_time_seconds", 0.0),
                "delta_seconds": delta,
            }
        )
    scenario_deltas.sort(key=lambda item: item["delta_seconds"], reverse=True)

    sql_deltas: list[dict[str, Any]] = []
    for name, current_explain in current.get("sql_explains", {}).items():
        baseline_explain = baseline.get("sql_explains", {}).get(name)
        if not baseline_explain:
            continue
        if "execution_time_ms" in current_explain:
            current_value = _safe_float(current_explain.get("execution_time_ms"))
            baseline_value = _safe_float(baseline_explain.get("execution_time_ms"))
            sql_deltas.append(
                {
                    "name": name,
                    "current_ms": current_value,
                    "baseline_ms": baseline_value,
                    "delta_ms": current_value - baseline_value,
                }
            )
            continue
        for branch_name, branch_explain in current_explain.items():
            baseline_branch = baseline_explain.get(branch_name, {})
            current_value = _safe_float(branch_explain.get("execution_time_ms"))
            baseline_value = _safe_float(baseline_branch.get("execution_time_ms"))
            sql_deltas.append(
                {
                    "name": f"{name}.{branch_name}",
                    "current_ms": current_value,
                    "baseline_ms": baseline_value,
                    "delta_ms": current_value - baseline_value,
                }
            )
    sql_deltas.sort(key=lambda item: item["delta_ms"], reverse=True)
    return {"scenario_deltas": scenario_deltas[:20], "sql_deltas": sql_deltas[:20]}


def _render_profile_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hotpath Profile Report",
        "",
        f"- Generated: `{report['metadata']['generated_at']}`",
        f"- Git SHA: `{report['metadata'].get('git_sha') or 'unknown'}`",
        "",
        "## Scenario Wall Times",
        "",
        "| Scenario | Wall Time |",
        "| --- | ---: |",
    ]
    for name, scenario in report["scenarios"].items():
        lines.append(f"| {name} | {_format_seconds(scenario['wall_time_seconds'])} |")

    for name, scenario in report["scenarios"].items():
        lines.extend(
            [
                "",
                f"## {name}",
                "",
                "| Function | Cumulative | Self Time |",
                "| --- | ---: | ---: |",
            ]
        )
        for row in scenario["top_cumulative"][:10]:
            lines.append(
                f"| `{row['function']}` | {_format_seconds(row['cumulative_time'])} | {_format_seconds(row['total_time'])} |"
            )

    lines.extend(
        [
            "",
            "## SQL Plans",
            "",
            "| Query | Execution | Planning |",
            "| --- | ---: | ---: |",
        ]
    )
    for name, explain in report["sql_explains"].items():
        if "execution_time_ms" in explain:
            lines.append(
                f"| {name} | {_format_milliseconds(explain.get('execution_time_ms'))} | "
                f"{_format_milliseconds(explain.get('planning_time_ms'))} |"
            )
            continue
        for branch_name, branch_explain in explain.items():
            lines.append(
                f"| {name}.{branch_name} | {_format_milliseconds(branch_explain.get('execution_time_ms'))} | "
                f"{_format_milliseconds(branch_explain.get('planning_time_ms'))} |"
            )

    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                "## Scenario Deltas",
                "",
                "| Scenario | Current | Baseline | Delta |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for item in comparison["scenario_deltas"]:
            lines.append(
                f"| {item['scenario']} | {_format_seconds(item['current_seconds'])} | "
                f"{_format_seconds(item['baseline_seconds'])} | {_format_seconds(item['delta_seconds'])} |"
            )
        if not comparison["scenario_deltas"]:
            lines.append("| _No scenario deltas_ | n/a | n/a | n/a |")

    return "\n".join(lines)


def profile_hotpaths(*, settings: Settings, update_baseline: bool) -> int:
    _ensure_benchmark_dirs()
    _cleanup_benchmark_rows(settings)
    vector_dimensions = _detect_vector_dimensions(settings)
    sql_context = _build_sql_profile_context(
        settings,
        vector_dimensions=vector_dimensions,
        query_dimension=vector_dimensions.paper,
    )
    seeded = _seed_sql_profile_data(sql_context)
    section_context = _build_sql_profile_context(
        settings,
        vector_dimensions=vector_dimensions,
        query_dimension=vector_dimensions.chunk,
    )
    memory_service = _build_memory_profile_service(settings)
    memory_search_service = _build_memory_profile_service(settings)
    memory_search_seeded = _seed_search_profile_data(memory_search_service)

    extractions = {
        "bench/index/0001": _benchmark_extraction("index", include_table=True, include_figure=True),
    }
    index_paper = _benchmark_metadata(
        "bench/index/0001",
        title=f"{BENCHMARK_QUERY_TOKEN} index paper",
        abstract=f"{BENCHMARK_QUERY_TOKEN} index abstract",
        published="2026-03-01",
    )

    with _patched_indexing(extractions):
        index_profile, _ = _profile_scenario(
            "index_single_paper",
            lambda: memory_service.index_arxiv_paper(index_paper.arxiv_id, paper=index_paper, force=True),
        )

    search_profile, search_response = _profile_scenario(
        "search_local_only",
        lambda: memory_search_service.search(
            SearchRequest(query=memory_search_seeded["search_query"], limit=5, include_live_sources=False)
        ),
    )
    section_profile, _ = _profile_scenario(
        "search_sections",
        lambda: section_context.service.search_sections(
            SearchSectionRequest(
                query=seeded["section_query"],
                limit=5,
                paper_ids=seeded["seeded_paper_ids"][:3],
            )
        ),
    )
    reindex_service = _build_memory_profile_service(settings)
    _seed_memory_reindex_profile(reindex_service)
    reindex_profile, _ = _profile_scenario(
        "reindex_missing_embeddings",
        lambda: reindex_service.reindex_missing_embeddings(limit=10, include_chunks=True),
    )
    chat_extractions = {
        "bench/chat/0001": _benchmark_extraction("chat", include_table=True),
    }
    chat_paper = _benchmark_metadata(
        "bench/chat/0001",
        title=f"{BENCHMARK_QUERY_TOKEN} chat paper",
        abstract=f"{BENCHMARK_QUERY_TOKEN} chat abstract",
        published="2026-03-02",
    )
    with _patched_indexing(chat_extractions):
        memory_service.index_arxiv_paper(chat_paper.arxiv_id, paper=chat_paper, force=True)
    session, _ = memory_service.register_browser_user(
        SignupRequest(email=BENCHMARK_USER_EMAIL, password="bench-secret-123", display_name="Bench User")
    )
    if session.user is None:
        raise RuntimeError("Failed to create benchmark browser user")
    thread = memory_service.create_chat_thread(session.user, CreateChatThreadRequest(title="Bench Chat"))
    chat_profile, _ = _profile_scenario(
        "chat_grounded_stream",
        lambda: list(
            memory_service.stream_chat_message(
                session.user,
                thread.thread_id,
                SendChatMessageRequest(content=f"{BENCHMARK_QUERY_TOKEN} grounding citations"),
            )
        ),
    )

    worker_idle_settings = memory_service.settings.model_copy(
        update={"worker_auto_sync_latest": False, "worker_initial_sync_latest": False}
    )
    worker_idle_service = _build_memory_profile_service(
        settings,
        worker_settings={"worker_auto_sync_latest": False, "worker_initial_sync_latest": False},
    )

    from .worker import run_one_iteration

    worker_idle_profile, _ = _profile_scenario(
        "worker_idle_iteration",
        lambda: run_one_iteration(
            settings=worker_idle_settings.model_copy(update={"worker_poll_interval_seconds": 0}),
            service=worker_idle_service,
            last_sync=0.0,
            now=10.0,
            sleep_fn=lambda _: None,
        ),
    )

    worker_process_service = _build_memory_profile_service(
        settings,
        worker_settings={"worker_auto_sync_latest": False, "worker_initial_sync_latest": False},
    )
    worker_extractions = {
        "bench/worker/0001": _benchmark_extraction("worker"),
    }
    worker_paper = _benchmark_metadata(
        "bench/worker/0001",
        title=f"{BENCHMARK_QUERY_TOKEN} worker paper",
        abstract=f"{BENCHMARK_QUERY_TOKEN} worker abstract",
        published="2026-03-03",
    )
    worker_process_service.enqueue_index_job(worker_paper.arxiv_id, force=True)
    with _patched_indexing(worker_extractions), _patched_attr(
        sources,
        "get_paper_details",
        lambda arxiv_id: worker_paper if arxiv_id == worker_paper.arxiv_id else None,
    ):
        worker_process_profile, _ = _profile_scenario(
            "worker_process_job",
            worker_process_service.process_next_job,
        )

    sql_search_results = sql_context.service.repository.search_local(
        seeded["search_query"],
        limit=5,
        query_embedding=None,
    )
    sql_result_set = sql_context.service.repository.save_result_set(
        query=seeded["search_query"],
        papers=sql_search_results,
        ttl_days=max(1, settings.result_ttl_days),
    )
    sql_explains = _collect_sql_explains(
        settings,
        vector_dimensions=vector_dimensions,
        search_query=seeded["search_query"],
        paper_ids=[paper.paper_id for paper in sql_search_results[:3]],
        result_set_id=sql_result_set.result_set_id,
    )
    report = {
        "metadata": _metadata("hotpaths"),
        "fixture": {
            "database_backend": "sqlalchemy",
            "storage_backend": settings.storage_backend,
            "paper_embedding_dimension": vector_dimensions.paper,
            "chunk_embedding_dimension": vector_dimensions.chunk,
            "seeded_papers": len(seeded["seeded_paper_ids"]),
            "query_token": BENCHMARK_QUERY_TOKEN,
        },
        "scenarios": {
            "index_single_paper": index_profile,
            "search_local_only": search_profile,
            "search_sections": section_profile,
            "reindex_missing_embeddings": reindex_profile,
            "chat_grounded_stream": chat_profile,
            "worker_idle_iteration": worker_idle_profile,
            "worker_process_job": worker_process_profile,
        },
        "sql_explains": sql_explains,
    }

    baseline_path = _profile_baseline_path()
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        report["comparison"] = _profile_deltas(report, baseline)

    _write_json(_profile_current_path(), report)
    _write_text(_profile_current_md_path(), _render_profile_markdown(report))

    if update_baseline or not baseline_path.exists():
        _write_json(baseline_path, report)
        _write_text(_profile_baseline_md_path(), _render_profile_markdown(report))
        print(f"Updated hotpath baseline at {baseline_path}")
    else:
        comparison = report.get("comparison", {})
        scenario_deltas = comparison.get("scenario_deltas", [])
        sql_deltas = comparison.get("sql_deltas", [])
        if scenario_deltas:
            print("Scenario deltas versus baseline:")
            for item in scenario_deltas[:10]:
                print(
                    f"  {item['scenario']}: {_format_seconds(item['current_seconds'])} "
                    f"(baseline {_format_seconds(item['baseline_seconds'])}, "
                    f"{_format_seconds(item['delta_seconds'])})"
                )
        if sql_deltas:
            print("SQL execution deltas versus baseline:")
            for item in sql_deltas[:10]:
                delta = item["delta_ms"]
                print(
                    f"  {item['name']}: {_format_milliseconds(item['current_ms'])} "
                    f"(baseline {_format_milliseconds(item['baseline_ms'])}, "
                    f"{delta:+.2f}ms)"
                )

    _cleanup_benchmark_rows(settings)
    return 0
