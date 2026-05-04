"""Developer-oriented local admin commands for paperbase."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from .config import Settings, get_settings, repo_root, resolve_repo_path, settings_env_file
from .dev_benchmarks import profile_hotpaths, record_test_times
from .repository import Base, build_repository
from .service import PaperbaseService
from .storage import build_object_store


LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
API_HOST = "127.0.0.1"
API_PORT = 8080
CHAT_PORT = 5173
PLUGIN_MCP_PORT = 8090
EXPECTED_PUBLIC_TABLES = tuple(sorted(Base.metadata.tables.keys()))


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


class DoctorMode(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROFILE = "profile"


REQUIRED_ENV_VARS_BY_MODE = {
    DoctorMode.TEST: (
        "PAPERBASE_DATABASE_URL",
        "PAPERBASE_STORAGE_BACKEND",
        "PAPERBASE_LOCAL_STORAGE_ROOT",
        "PAPERBASE_API_KEY",
    ),
    DoctorMode.PROFILE: (
        "PAPERBASE_DATABASE_URL",
        "PAPERBASE_STORAGE_BACKEND",
        "PAPERBASE_LOCAL_STORAGE_ROOT",
        "PAPERBASE_API_KEY",
    ),
}


def _repo_root() -> Path:
    root = repo_root()
    if root is None:
        raise RuntimeError("Could not locate the repo root from the installed package layout.")
    return root


def _repo_env_file() -> Path:
    return settings_env_file().resolve()


def _repo_env_values() -> dict[str, str]:
    env_file = _repo_env_file()
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def _as_psycopg_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _assert_local_database_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"Only PostgreSQL URLs are supported for local development: {url}")
    if not url.database:
        raise ValueError("Database URL must include a database name")

    host = url.host or ""
    query_host = str(url.query.get("host", "") or "")
    if host and host not in LOCAL_HOSTS:
        raise ValueError(f"Refusing to use a non-local database host for local development: {host}")
    if query_host and not query_host.startswith("/") and query_host not in LOCAL_HOSTS:
        raise ValueError(f"Refusing to use a non-local database host for local development: {query_host}")


def _assert_safe_storage_root(storage_root: Path, root: Path) -> None:
    if storage_root == root:
        raise ValueError("Refusing to delete the repository root")
    if root not in storage_root.parents:
        raise ValueError(f"Refusing to clear storage outside the repo: {storage_root}")


def _settings() -> Settings:
    env_file = _repo_env_file()
    if not env_file.exists():
        raise FileNotFoundError(f"Missing {env_file}. Copy .env.example to .env before using paperbase-dev.")
    return get_settings()


def _local_storage_root(settings: Settings) -> Path:
    return resolve_repo_path(settings.local_storage_root)


def _admin_database_url(url: URL) -> URL:
    database_name = url.database
    admin_database = "template1" if database_name == "postgres" else "postgres"
    return url.set(database=admin_database)


def _database_exists(connection: psycopg.Connection, database_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        return cursor.fetchone() is not None


def _extension_available(connection: psycopg.Connection, extension_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT installed_version, default_version FROM pg_available_extensions WHERE name = %s",
            (extension_name,),
        )
        return cursor.fetchone() is not None


def _extension_installed(connection: psycopg.Connection, extension_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (extension_name,))
        return cursor.fetchone() is not None


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _storage_checks(settings: Settings, *, root: Path, mode: DoctorMode) -> Iterable[CheckResult]:
    storage_root = _local_storage_root(settings)
    if settings.storage_backend != "local":
        if mode is DoctorMode.PROFILE:
            yield CheckResult(
                "storage backend",
                "fail",
                f"`paperbase-dev doctor --mode {mode.value}` requires PAPERBASE_STORAGE_BACKEND=local, got {settings.storage_backend!r}",
            )
            return
        yield CheckResult("storage backend", "ok", f"Configured backend: {settings.storage_backend}")
        return

    try:
        _assert_safe_storage_root(storage_root, root)
    except ValueError as exc:
        yield CheckResult("storage root", "fail", str(exc))
        return

    exists_detail = "exists" if storage_root.exists() else "will be created by `paperbase-dev init-local`"
    yield CheckResult("storage backend", "ok", "Local object storage")
    yield CheckResult("storage root", "ok", f"{storage_root} ({exists_detail})")


def _missing_required_env_vars(mode: DoctorMode, *, env_values: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for env_name in REQUIRED_ENV_VARS_BY_MODE.get(mode, ()):
        candidate = os.environ.get(env_name)
        if candidate is None or not candidate.strip():
            candidate = env_values.get(env_name, "")
        if not candidate.strip():
            missing.append(env_name)
    return missing


def _workflow_env_checks(mode: DoctorMode) -> Iterable[CheckResult]:
    required = REQUIRED_ENV_VARS_BY_MODE.get(mode, ())
    if not required:
        return
    env_values = _repo_env_values()
    missing = _missing_required_env_vars(mode, env_values=env_values)
    if missing:
        yield CheckResult(
            "required env vars",
            "fail",
            f"Missing values for: {', '.join(missing)}",
        )
        return
    yield CheckResult(
        "required env vars",
        "ok",
        f"Resolved values for: {', '.join(required)}",
    )


def _existing_public_tables(connection: psycopg.Connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        return {str(row[0]) for row in cursor.fetchall()}


def _benchmark_fixture_counts(connection: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    queries = {
        "papers": "SELECT COUNT(*) FROM papers WHERE paper_id LIKE 'arxiv:bench/%' OR arxiv_id LIKE 'bench/%'",
        "text_chunks": "SELECT COUNT(*) FROM text_chunks WHERE paper_id LIKE 'arxiv:bench/%'",
        "jobs": "SELECT COUNT(*) FROM jobs WHERE payload::text LIKE '%bench/%'",
        "result_sets": "SELECT COUNT(*) FROM result_sets WHERE query LIKE 'zzbench%' OR pattern LIKE 'zzbench%'",
    }
    with connection.cursor() as cursor:
        for name, query in queries.items():
            cursor.execute(query)
            counts[name] = int(cursor.fetchone()[0])
    return counts


def _workflow_database_checks(settings: Settings, *, mode: DoctorMode) -> Iterable[CheckResult]:
    database_url = make_url(settings.database_url)
    try:
        _assert_local_database_url(database_url)
    except ValueError as exc:
        yield CheckResult("database url", "fail", str(exc))
        return

    try:
        with psycopg.connect(_as_psycopg_dsn(database_url), autocommit=True) as connection:
            yield CheckResult(
                "vector extension",
                "ok" if _extension_installed(connection, "vector") else "fail",
                "Installed in target database" if _extension_installed(connection, "vector") else "Run `paperbase-dev init-local` first",
            )
            yield CheckResult(
                "pg_trgm extension",
                "ok" if _extension_installed(connection, "pg_trgm") else "fail",
                "Installed in target database" if _extension_installed(connection, "pg_trgm") else "Run `paperbase-dev init-local` first",
            )
            existing_tables = _existing_public_tables(connection)
            missing_tables = [table for table in EXPECTED_PUBLIC_TABLES if table not in existing_tables]
            if missing_tables:
                yield CheckResult(
                    "database schema",
                    "fail",
                    f"Missing public tables: {', '.join(missing_tables)}",
                )
            else:
                yield CheckResult(
                    "database schema",
                    "ok",
                    f"Found all expected public tables ({len(EXPECTED_PUBLIC_TABLES)})",
                )
            if mode is DoctorMode.PROFILE:
                benchmark_counts = _benchmark_fixture_counts(connection)
                fixture_total = sum(benchmark_counts.values())
                if fixture_total:
                    detail = ", ".join(f"{name}={count}" for name, count in benchmark_counts.items() if count)
                    yield CheckResult(
                        "benchmark fixtures",
                        "ok",
                        f"Found stale deterministic benchmark rows ({detail}); `paperbase-dev profile` will reseed them",
                    )
                else:
                    yield CheckResult(
                        "benchmark fixtures",
                        "ok",
                        "No deterministic benchmark rows are present; `paperbase-dev profile` will seed them",
                    )
            else:
                yield CheckResult(
                    "shared test seed data",
                    "ok",
                    "No shared seed data is required; pytest creates its own fixtures",
                )
            yield from _enrichment_health_check(connection)
    except psycopg.Error as exc:
        yield CheckResult("database schema", "fail", f"Could not inspect the target database: {exc}")


def _enrichment_health_check(connection: psycopg.Connection) -> Iterable[CheckResult]:
    """Report S2 enrichment coverage so operators notice rate-limit fallout
    before citation-boosted ranking silently degrades at scale. A warning
    rather than a failure: the corpus is still usable, just missing signal.

    "Enriched" means the paper has a populated S2 id OR a non-zero citation
    count. Using citation_count > 0 alone would misclassify freshly-published
    papers that were successfully looked up but legitimately have no
    citations yet, and warn on a fully-enriched recent corpus.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE index_status = 'indexed'),
                  COUNT(*) FILTER (
                    WHERE index_status = 'indexed'
                    AND (
                      (semantic_scholar_id IS NOT NULL AND semantic_scholar_id <> '')
                      OR citation_count > 0
                    )
                  )
                FROM papers
                """
            )
            total, enriched = cursor.fetchone() or (0, 0)
    except psycopg.Error as exc:
        yield CheckResult("S2 enrichment", "warn", f"Could not inspect enrichment state: {exc}")
        return
    if not total:
        yield CheckResult("S2 enrichment", "ok", "No indexed papers yet")
        return
    missing = total - enriched
    coverage = enriched / total * 100
    if missing == 0:
        yield CheckResult("S2 enrichment", "ok", f"{enriched}/{total} papers enriched (100%)")
    elif coverage >= 90:
        yield CheckResult(
            "S2 enrichment",
            "ok",
            f"{enriched}/{total} papers enriched ({coverage:.1f}%); `paperbase-dev enrich-missing` backfills the rest",
        )
    else:
        yield CheckResult(
            "S2 enrichment",
            "warn",
            f"Only {enriched}/{total} papers enriched ({coverage:.1f}%); run `paperbase-dev enrich-missing`",
        )


def _check_results(settings: Settings, *, mode: DoctorMode) -> Iterable[CheckResult]:
    env_file = _repo_env_file()
    database_url = make_url(settings.database_url)
    root = _repo_root()
    supports_local_profile = mode in {DoctorMode.TEST, DoctorMode.PROFILE}
    expected_db = "postgresql" if supports_local_profile else "configured"

    yield CheckResult(
        "vp",
        "ok" if shutil.which("vp") else "fail",
        "Found on PATH" if shutil.which("vp") else "Install it with: curl -fsSL https://vite.plus | bash",
    )
    yield CheckResult(
        "python",
        "ok" if sys.version_info >= (3, 11) else "fail",
        f"Using Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    yield CheckResult(
        "env file",
        "ok" if env_file.exists() else "fail",
        str(env_file),
    )
    yield from _storage_checks(settings, root=root, mode=mode)

    try:
        _assert_local_database_url(database_url)
    except ValueError as exc:
        yield CheckResult("database url", "fail", str(exc))
        return

    yield CheckResult("database url", "ok", f"{database_url} ({expected_db})")

    for name, port in (("api port", API_PORT), ("chat port", CHAT_PORT), ("plugin port", PLUGIN_MCP_PORT)):
        status = "ok" if _port_available(API_HOST, port) else "warn"
        detail = f"{API_HOST}:{port} is available" if status == "ok" else f"{API_HOST}:{port} is already in use"
        yield CheckResult(name, status, detail)

    admin_url = _admin_database_url(database_url)
    try:
        with psycopg.connect(_as_psycopg_dsn(admin_url), autocommit=True) as connection:
            yield CheckResult("postgres", "ok", f"Connected via {admin_url.render_as_string(hide_password=True)}")
            yield CheckResult(
                "pgvector",
                "ok" if _extension_available(connection, "vector") else "fail",
                "The vector extension is available"
                if _extension_available(connection, "vector")
                else "Install PostgreSQL with pgvector before running make dev",
            )
            yield CheckResult(
                "pg_trgm",
                "ok" if _extension_available(connection, "pg_trgm") else "fail",
                "The pg_trgm extension is available"
                if _extension_available(connection, "pg_trgm")
                else "Enable contrib extensions in your local PostgreSQL install",
            )
            database_name = str(database_url.database)
            yield CheckResult(
                "local database",
                "ok" if _database_exists(connection, database_name) else "warn",
                f"Database {database_name!r} exists"
                if _database_exists(connection, database_name)
                else f"Database {database_name!r} will be created by `paperbase-dev init-local`",
            )
    except psycopg.Error as exc:
        yield CheckResult(
            "postgres",
            "fail",
            f"Could not connect to PostgreSQL: {exc}",
        )


def _verify_run_results(settings: Settings, *, mode: DoctorMode) -> Iterable[CheckResult]:
    yield from _workflow_env_checks(mode)
    yield from _workflow_database_checks(settings, mode=mode)


def _print_check_suite(results: Iterable[CheckResult], *, intro: str, success_message: str, failure_label: str) -> int:
    failures = 0
    warnings = 0
    print(intro)
    for result in results:
        print(f"[{result.status.upper():4}] {result.name}: {result.detail}")
        if result.status == "fail":
            failures += 1
        elif result.status == "warn":
            warnings += 1
    if failures:
        print(f"{failure_label} found {failures} failure(s) and {warnings} warning(s).")
        return 1
    print(success_message.format(warnings=warnings))
    return 0


def doctor(*, mode: DoctorMode) -> int:
    env_file = _repo_env_file()
    settings = get_settings() if env_file.exists() else Settings()
    return _print_check_suite(
        _check_results(settings, mode=mode),
        intro=f"Running doctor in {mode.value} mode.",
        success_message="Doctor finished with {warnings} warning(s).",
        failure_label="Doctor",
    )


def verify_run(*, mode: DoctorMode) -> int:
    settings = _settings()
    return _print_check_suite(
        _verify_run_results(settings, mode=mode),
        intro=f"Verifying {mode.value} run prerequisites.",
        success_message="Run verification finished with {warnings} warning(s).",
        failure_label="Run verification",
    )


def _ensure_local_database(settings: Settings) -> str:
    url = make_url(settings.database_url)
    _assert_local_database_url(url)

    database_name = str(url.database)
    admin_url = _admin_database_url(url)
    with psycopg.connect(_as_psycopg_dsn(admin_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            if not _database_exists(connection, database_name):
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    return database_name


def init_local() -> int:
    settings = _settings()
    url = make_url(settings.database_url)
    _assert_local_database_url(url)

    database_name = _ensure_local_database(settings)
    storage_root = _local_storage_root(settings)
    storage_root.mkdir(parents=True, exist_ok=True)

    repository = build_repository(settings)
    repository.init()

    with psycopg.connect(_as_psycopg_dsn(url), autocommit=True) as connection:
        vector_installed = _extension_installed(connection, "vector")
        trgm_installed = _extension_installed(connection, "pg_trgm")

    print(f"Initialized local database: {database_name}")
    print(f"Storage root ready:         {storage_root}")
    print(f"vector installed:          {'yes' if vector_installed else 'no'}")
    print(f"pg_trgm installed:         {'yes' if trgm_installed else 'no'}")
    return 0


def _reset_database(settings: Settings) -> str:
    url = make_url(settings.database_url)
    _assert_local_database_url(url)

    database_name = str(url.database)
    admin_url = _admin_database_url(url)

    with psycopg.connect(_as_psycopg_dsn(admin_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            if not _database_exists(connection, database_name):
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    with psycopg.connect(_as_psycopg_dsn(url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cursor.execute("CREATE SCHEMA public")
            cursor.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
            cursor.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

    return database_name


def _reset_storage_root(storage_root: Path) -> None:
    if storage_root.is_file():
        storage_root.unlink()
    else:
        shutil.rmtree(storage_root, ignore_errors=True)
    storage_root.mkdir(parents=True, exist_ok=True)


def flush_local() -> int:
    settings = _settings()
    root = _repo_root()
    storage_root = _local_storage_root(settings)
    _assert_safe_storage_root(storage_root, root)

    database_name = _reset_database(settings)
    _reset_storage_root(storage_root)
    build_repository(settings).init()

    print(f"Reset database schema: {database_name}")
    print(f"Reset storage:         {storage_root}")
    return 0


def reindex_embeddings(*, limit: int, include_chunks: bool, rewrite_existing: bool) -> int:
    settings = _settings()
    service = PaperbaseService(
        settings=settings,
        repository=build_repository(settings),
        object_store=build_object_store(settings),
    )
    service.init()
    job = service.enqueue_reindex_embeddings_job(
        limit=limit,
        include_chunks=include_chunks,
        rewrite_existing=rewrite_existing,
    )
    print(f"Queued job:            {job.job_id}")
    print(f"Job type:              {job.job_type}")
    print(f"Rewrite existing:      {'yes' if rewrite_existing else 'no'}")
    print(f"Include chunks:        {'yes' if include_chunks else 'no'}")
    print(f"Limit:                 {limit}")
    return 0


def jobs_list_dead(*, limit: int) -> int:
    """Print dead-letter jobs (exhausted retries). Intended as a triage
    entry point — operator eyeballs the error text, then either retries or
    leaves dead. Reads via the service so memory-mode tests can exercise it."""
    from .service import get_service

    service = get_service()
    dead = service.list_dead_jobs(limit=limit)
    if not dead:
        print("No dead-letter jobs.")
        return 0
    print(f"{'job_id':<22} {'job_type':<28} {'attempts':>8} {'last_error':<60}")
    print("-" * 120)
    for job in dead:
        err = (job.error or "")[:58].replace("\n", " ")
        print(f"{job.job_id:<22} {job.job_type:<28} {job.attempts:>3}/{job.max_attempts:<4} {err}")
    return 0


def jobs_retry(*, job_id: str) -> int:
    from .service import get_service

    service = get_service()
    result = service.retry_dead_job(job_id)
    if result is None:
        print(f"{job_id} is not in a dead-letter state (or does not exist).")
        return 1
    print(f"Requeued {result.job_id} ({result.job_type}): status={result.status} attempts={result.attempts}")
    return 0


def backfill_code_urls(*, limit: int, page_size: int, dry_run: bool) -> int:
    """One-shot sweep: re-run the Hugging Face paper-page code crosslink
    over indexed papers and overwrite ``papers.code_url`` where the
    source is not ``admin`` (#46).

    ``--dry-run`` logs the would-be diffs without writing — use that on
    first run so an admin can eyeball changes before a live backfill
    against thousands of papers.
    """
    from .service import get_service

    service = get_service()
    result = service.backfill_code_urls_from_hf_papers(
        limit=limit, page_size=page_size, dry_run=dry_run
    )
    print(f"Scanned:        {result['scanned']}")
    print(f"Crosslink hits: {result['crosslink_hits']}")
    print(f"Updated:        {result['updated']}")
    print(f"Skipped admin:  {result['skipped_admin']}")
    print(f"Unchanged:      {result['unchanged']}")
    if dry_run:
        print("[dry-run] no writes performed; rerun without --dry-run to apply.")
    return 0


def enrich_missing(*, limit: int, min_age_seconds: int, batch_size: int, queue: bool = False) -> int:
    """Backfill Semantic Scholar metadata for indexed papers (#38).

    Two modes:

    - ``--queue`` (preferred for prod-scale backfills): enqueue an
      ``enrich_missing_metadata`` job that the worker pool processes
      asynchronously. The serving container is not tied up; this is
      what an operator would use to backfill the existing corpus
      after PR C (#39) shipped author-IDs in the inline enrich path.
    - default (synchronous): run the pass in the current process.
      Useful for spot-checks during local development; not appropriate
      for corpus-scale runs because the CLI process holds the work.
    """
    # Imported lazily so `paperbase-dev --help` does not pull in the whole
    # service graph (which loads heavy embedding/reranker model clients).
    from .service import get_service

    service = get_service()
    if queue:
        job = service.enqueue_enrich_missing_metadata_job(
            limit=limit,
            min_age_seconds=min_age_seconds,
        )
        print(
            f"Queued enrich_missing_metadata {job.job_id}: "
            f"limit={limit}, min_age_seconds={min_age_seconds}"
        )
        return 0

    stats_before = service.repository.enrichment_stats()
    result = service.enrich_missing_metadata(
        limit=limit,
        min_age_seconds=min_age_seconds,
        batch_size=batch_size,
    )
    stats_after = service.repository.enrichment_stats()

    total = stats_after.get("total_indexed", 0)
    before_cov = (stats_before.get("enriched", 0) / total * 100) if total else 0.0
    after_cov = (stats_after.get("enriched", 0) / total * 100) if total else 0.0

    print(f"Scanned:        {result['papers_scanned']}")
    print(f"Updated:        {result['papers_updated']}")
    print(f"Batches failed: {result['batches_failed']}")
    print(f"Coverage:       {before_cov:.1f}% -> {after_cov:.1f}% "
          f"({stats_after.get('enriched', 0)}/{total} papers enriched)")
    if result["batches_failed"] > 0:
        print("Incomplete: rerun paperbase-dev enrich-missing after the upstream recovers.")
        return 1
    return 0


def harvest(
    *,
    categories: list[str],
    start_date: str,
    end_date: str,
    limit_per_window: int | None,
    chunk_months: int,
) -> int:
    """Queue a historical arxiv backfill. Planner splits the range into
    ``chunk_months``-wide windows per category and enqueues one
    ``harvest_chunk`` per window; chunks run at lower priority than
    interactive work so a 5-year backfill does not starve ``sync_latest``
    or on-demand ``index_paper``.

    ``limit_per_window`` is per-(category, window), NOT per-category
    total. A 2-year range with ``chunk_months=3`` is 8 windows, so
    ``limit_per_window=1000`` means each category can ingest up to
    ``8 * 1000 = 8000`` papers over the full range.
    """
    from .errors import PermanentError
    from .service import get_service

    service = get_service()
    try:
        job = service.enqueue_harvest_range_job(
            categories=categories,
            start_date=start_date,
            end_date=end_date,
            limit_per_window=limit_per_window,
            chunk_months=chunk_months,
        )
    except PermanentError as exc:
        print(f"Invalid harvest request: {exc}")
        return 1
    print(
        f"Queued harvest_range {job.job_id}: {len(categories)} categor"
        f"{'y' if len(categories) == 1 else 'ies'} over {start_date} → {end_date}, "
        f"chunk_months={chunk_months}, limit_per_window={limit_per_window}"
    )
    return 0


def harvest_top_cited(
    *,
    min_citations: int,
    max_papers: int | None,
    chunk_size: int,
) -> int:
    """Queue a quality-curated harvest by Semantic Scholar citationCount.

    The planner streams S2's bulk-search endpoint sorted by
    ``citationCount:desc``, filters to arxiv-linked papers, and enqueues
    ``harvest_chunk`` jobs in batches of ``chunk_size`` arxiv ids.
    Chunks run at the same low priority as the date-range harvest so
    on-demand ``index_paper`` always cuts in front.

    ``min_citations=100`` is a good first run (~106k arxiv-linked CS
    papers); drop to ~25 for full ~500k-paper coverage.
    """
    from .errors import PermanentError
    from .service import get_service

    service = get_service()
    try:
        job = service.enqueue_harvest_top_cited_job(
            min_citations=min_citations,
            max_papers=max_papers,
            chunk_size=chunk_size,
        )
    except PermanentError as exc:
        print(f"Invalid harvest-top-cited request: {exc}")
        return 1
    print(
        f"Queued harvest_top_cited {job.job_id}: "
        f"min_citations={min_citations}, max_papers={max_papers}, "
        f"chunk_size={chunk_size}"
    )
    return 0


def harvest_venue(
    *,
    venue: str,
    year: int,
    max_papers: int | None,
    chunk_size: int,
) -> int:
    """Queue a venue-curated harvest from Semantic Scholar's bulk search
    filtered by ``venue`` + ``year`` (#39 PR B).

    The planner pages S2 for arxiv-linked papers accepted at the chosen
    conference + year, batches them into ``chunk_size`` groups, and
    enqueues ``harvest_chunk`` jobs (``source='s2_venue'``). The chunk
    worker indexes each paper and stamps the ``venue`` / ``venue_year``
    / ``venue_source`` columns. Chunks run at the same low priority as
    ``harvest_range`` so on-demand work always cuts in front.
    """
    from .errors import PermanentError
    from .service import get_service

    service = get_service()
    try:
        job = service.enqueue_harvest_venue_job(
            venue=venue,
            year=year,
            max_papers=max_papers,
            chunk_size=chunk_size,
        )
    except PermanentError as exc:
        print(f"Invalid harvest-venue request: {exc}")
        return 1
    print(
        f"Queued harvest_venue {job.job_id}: "
        f"venue={venue!r}, year={year}, max_papers={max_papers}, chunk_size={chunk_size}"
    )
    return 0


def harvest_author(
    *,
    min_paper_count: int,
    min_cite_mass: int,
    max_authors: int,
    max_papers_per_author: int | None,
    chunk_size: int,
) -> int:
    """Queue an author-signal harvest from the indexed corpus (#39 PR C).

    Derives top authors via ``list_top_authors`` aggregation over
    ``paper_authors`` JOIN ``papers``, then for each pages S2's
    ``/author/{id}/papers`` and enqueues ``harvest_chunk`` jobs
    (``source='s2_author'``). The chunk worker indexes each paper and
    stamps ``top_author_s2_id`` so analysis can attribute provenance.
    """
    from .errors import PermanentError
    from .service import get_service

    service = get_service()
    try:
        job = service.enqueue_harvest_author_job(
            min_paper_count=min_paper_count,
            min_cite_mass=min_cite_mass,
            max_authors=max_authors,
            max_papers_per_author=max_papers_per_author,
            chunk_size=chunk_size,
        )
    except PermanentError as exc:
        print(f"Invalid harvest-author request: {exc}")
        return 1
    print(
        f"Queued harvest_author {job.job_id}: "
        f"min_paper_count={min_paper_count}, min_cite_mass={min_cite_mass}, "
        f"max_authors={max_authors}, max_papers_per_author={max_papers_per_author}, "
        f"chunk_size={chunk_size}"
    )
    return 0


def backfill_paper_authors(*, limit: int, batch_size: int) -> int:
    """Populate ``paper_authors`` for already-indexed papers that don't
    yet have author rows (#39 PR C).

    Iterates papers ordered by ``citation_count`` desc (so high-impact
    papers get covered first), batches their arxiv ids, calls S2's
    ``/paper/batch`` with ``fields=authors.authorId,authors.name``, and
    writes ``paper_authors`` rows via ``replace_paper_authors``. Re-
    runnable: papers that already have rows are skipped.
    """
    from . import sources
    from .models import PaperMetadata
    from .service import get_service

    service = get_service()
    repo = service.repository

    # Pick papers without author rows. Use a direct SQL query via the
    # repository's engine for the SqlAlchemy backend.
    from sqlalchemy import text as _text

    with repo._engine.begin() as conn:  # type: ignore[attr-defined]
        # Scope to ``index_status='indexed'`` so live-search / related-paper
        # rows that the repo persists as ``index_status='discovered'`` don't
        # leak into ``paper_authors``. ``list_top_authors`` aggregates
        # wholesale, so without this filter the planner could be driven by
        # off-corpus papers we never actually ingested.
        rows = conn.execute(
            _text(
                """
                SELECT p.paper_id, p.arxiv_id
                FROM papers p
                LEFT JOIN paper_authors pa ON pa.paper_id = p.paper_id
                WHERE pa.paper_id IS NULL
                  AND p.index_status = 'indexed'
                ORDER BY p.citation_count DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        ).fetchall()

    if not rows:
        print("backfill-paper-authors: no papers needing backfill")
        return 0

    targets = [(row[0], row[1]) for row in rows]
    print(f"backfill-paper-authors: {len(targets)} papers to process")

    total_papers_with_authors = 0
    total_authors_written = 0
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset: offset + batch_size]
        # Construct PaperMetadata stand-ins so enrich's batch path works.
        metas = [PaperMetadata(arxiv_id=arxiv_id) for _paper_id, arxiv_id in batch]
        outcome = sources.enrich_with_semantic_scholar(metas, batch_size=len(batch), max_retries=2)
        for (paper_id, arxiv_id), meta in zip(batch, metas):
            author_rows = outcome.authors_by_arxiv.get(meta.arxiv_id) or []
            if not author_rows:
                continue
            repo.replace_paper_authors(paper_id, authors=author_rows)
            total_papers_with_authors += 1
            total_authors_written += len(author_rows)
        print(
            f"  processed {min(offset + batch_size, len(targets))}/{len(targets)} | "
            f"papers_with_authors={total_papers_with_authors} | "
            f"authors_written={total_authors_written}"
        )

    print(
        f"backfill-paper-authors done: {total_papers_with_authors}/{len(targets)} papers "
        f"got authors ({total_authors_written} rows total)"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local developer helpers for paperbase.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Verify local prerequisites and the repo env file.")
    doctor_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in DoctorMode],
        default=DoctorMode.DEV.value,
        help="Validation profile for the current workflow.",
    )
    verify_parser = subparsers.add_parser(
        "verify-run",
        help="Validate env vars and initialized database/schema prerequisites for a test or profile run.",
    )
    verify_parser.add_argument(
        "--mode",
        choices=[DoctorMode.TEST.value, DoctorMode.PROFILE.value],
        required=True,
        help="Workflow being prepared.",
    )
    subparsers.add_parser("init-local", help="Create the local database, storage root, and schema.")
    subparsers.add_parser("flush-local", help="Reset the local database schema and storage root.")

    timing_parser = subparsers.add_parser(
        "record-test-times",
        help="Capture suite timings, compare them to the committed baseline, and optionally update that baseline.",
    )
    timing_parser.add_argument("--backend-junit", required=True, type=Path)
    timing_parser.add_argument("--plugin-junit", required=True, type=Path)
    timing_parser.add_argument("--chat-json", required=True, type=Path)
    timing_parser.add_argument("--update-baseline", action="store_true")

    profile_parser = subparsers.add_parser(
        "profile",
        help="Run deterministic hotpath profiling and compare the results to the committed baseline.",
    )
    profile_parser.add_argument("--update-baseline", action="store_true")

    reindex_parser = subparsers.add_parser(
        "reindex-embeddings",
        help="Queue a paper/chunk embedding rewrite job using the current embedding model.",
    )
    reindex_parser.add_argument("--limit", type=int, default=100_000)
    reindex_parser.add_argument(
        "--skip-chunks",
        action="store_true",
        help="Rewrite paper vectors only.",
    )
    reindex_parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Backfill NULL embeddings only instead of rewriting existing vectors.",
    )

    jobs_parser = subparsers.add_parser(
        "jobs",
        help="Inspect and operate on the worker job queue (dead-letter, retry).",
    )
    jobs_sub = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    list_dead_parser = jobs_sub.add_parser(
        "list-dead",
        help="Print the most recent dead-letter jobs (exhausted retries).",
    )
    list_dead_parser.add_argument("--limit", type=int, default=50)
    retry_parser = jobs_sub.add_parser(
        "retry",
        help="Promote a dead job back to pending with attempts reset.",
    )
    retry_parser.add_argument("job_id", help="Job id (e.g. job_abc123).")

    backfill_code_parser = subparsers.add_parser(
        "backfill-code-urls",
        help=(
            "Re-run the Hugging Face paper-page code crosslink across indexed papers and "
            "overwrite code_url where source is not 'admin' (#46). Supports "
            "--dry-run for a no-write preview."
        ),
    )
    backfill_code_parser.add_argument("--limit", type=int, default=100_000)
    backfill_code_parser.add_argument("--page-size", type=int, default=200)
    backfill_code_parser.add_argument("--dry-run", action="store_true")

    enrich_parser = subparsers.add_parser(
        "enrich-missing",
        help=(
            "Backfill Semantic Scholar citation / TLDR data for any indexed paper "
            "where inline enrichment failed (usually due to transient 429s). "
            "Runs aggressively with exponential backoff; safe to re-run."
        ),
    )
    enrich_parser.add_argument(
        "--limit",
        type=int,
        default=50_000,
        help="Maximum number of papers to enrich in this pass.",
    )
    enrich_parser.add_argument(
        "--min-age-seconds",
        type=int,
        default=3600,
        help=(
            "Only enrich papers indexed more than this many seconds ago "
            "(default 3600). Prevents racing with inline enrichment on "
            "freshly-ingested papers."
        ),
    )
    enrich_parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Semantic Scholar /paper/batch size (max 500 per S2 docs).",
    )
    enrich_parser.add_argument(
        "--queue",
        action="store_true",
        help=(
            "Queue an ``enrich_missing_metadata`` job for the worker pool "
            "instead of running the backfill synchronously in this CLI "
            "process. Preferred for corpus-scale runs."
        ),
    )
    harvest_parser = subparsers.add_parser(
        "harvest",
        help="Queue a historical arxiv backfill by date range (runs at backfill priority).",
    )
    harvest_parser.add_argument(
        "--categories",
        default=None,
        help=(
            "Comma-separated arxiv categories (e.g. cs.LG,cs.CL). "
            "Defaults to the configured AI/ML category list."
        ),
    )
    harvest_parser.add_argument("--start", dest="start_date", required=True, help="YYYY-MM-DD (inclusive).")
    harvest_parser.add_argument("--end", dest="end_date", required=True, help="YYYY-MM-DD (inclusive).")
    harvest_parser.add_argument(
        "--limit-per-window",
        "--limit-per-category",  # legacy alias; kept for scripts predating the rename
        dest="limit_per_window",
        type=int,
        default=None,
        help=(
            "Cap each (category, window) at this many papers; omit for unlimited. "
            "NOTE: this is per-window, not per-category. A 2-year range with "
            "--chunk-months=3 is 8 windows, so --limit-per-window=1000 means "
            "each category can ingest up to 8000 papers."
        ),
    )
    harvest_parser.add_argument(
        "--chunk-months",
        type=int,
        default=3,
        help="Sub-job window size in months. Smaller = finer progress + more resume points.",
    )

    top_cited_parser = subparsers.add_parser(
        "harvest-top-cited",
        help=(
            "Queue a quality-curated harvest from Semantic Scholar's bulk search "
            "ordered by citationCount desc. See issue #39 for the design + corpus "
            "math. ``--min-citations=100`` yields ~106k arxiv-linked papers; "
            "drop to ~25 for the full ~500k-paper target."
        ),
    )
    top_cited_parser.add_argument(
        "--min-citations",
        type=int,
        default=100,
        help="S2 minCitationCount floor (default 100).",
    )
    top_cited_parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Stop after this many candidates; omit for full sweep until S2 is exhausted.",
    )
    top_cited_parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="arxiv_ids per harvest_chunk (default 50). Smaller = finer interactive priority bandwidth.",
    )

    venue_parser = subparsers.add_parser(
        "harvest-venue",
        help=(
            "Queue a venue-curated harvest from Semantic Scholar's bulk search "
            "filtered by venue + year (#39 PR B). The chunk worker indexes each "
            "paper and stamps the venue/venue_year/venue_source columns so "
            "retrieval can boost peer-reviewed work over preprints."
        ),
    )
    venue_parser.add_argument(
        "--venue",
        required=True,
        help="Conference venue (e.g. ICLR, NeurIPS, ICML, CVPR — short or long form).",
    )
    venue_parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="4-digit conference year.",
    )
    venue_parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Stop after this many candidates; omit for full sweep until S2 exhausts.",
    )
    venue_parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="arxiv_ids per harvest_chunk (default 50).",
    )

    author_parser = subparsers.add_parser(
        "harvest-author",
        help=(
            "Queue an author-signal harvest (#39 PR C). Derives top "
            "authors from the indexed corpus and pulls their full "
            "publication list from S2. Catches frontier work by "
            "established researchers before citations accumulate."
        ),
    )
    author_parser.add_argument(
        "--min-paper-count",
        type=int,
        default=2,
        help=(
            "Min papers in our corpus before an author counts as 'top' "
            "(default 2). Bumped from 1 so a single heavily-cited paper "
            "doesn't qualify its first author as 'established'."
        ),
    )
    author_parser.add_argument(
        "--min-cite-mass",
        type=int,
        default=5_000,
        help="Min summed citation count across an author's corpus papers (default 5000, per #39 spec).",
    )
    author_parser.add_argument(
        "--max-authors",
        type=int,
        default=100,
        help="Stop after harvesting from this many top authors (default 100).",
    )
    author_parser.add_argument(
        "--max-papers-per-author",
        type=int,
        default=None,
        help="Cap S2 paging per author; omit for full publication list.",
    )
    author_parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="arxiv_ids per harvest_chunk (default 50).",
    )

    backfill_authors_parser = subparsers.add_parser(
        "backfill-paper-authors",
        help=(
            "Populate paper_authors for already-indexed papers (#39 PR C). "
            "Required after PR C ships before harvest-author can derive "
            "top authors — earlier ingests didn't capture S2 author IDs."
        ),
    )
    backfill_authors_parser.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Process at most this many papers (default 10k).",
    )
    backfill_authors_parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="S2 batch enrichment batch size (default 20).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor(mode=DoctorMode(args.mode))
    if args.command == "verify-run":
        return verify_run(mode=DoctorMode(args.mode))
    if args.command == "init-local":
        return init_local()
    if args.command == "flush-local":
        return flush_local()
    if args.command == "record-test-times":
        return record_test_times(
            backend_junit=args.backend_junit.resolve(),
            plugin_junit=args.plugin_junit.resolve(),
            chat_json=args.chat_json.resolve(),
            update_baseline=args.update_baseline,
        )
    if args.command == "profile":
        return profile_hotpaths(settings=_settings(), update_baseline=args.update_baseline)
    if args.command == "reindex-embeddings":
        return reindex_embeddings(
            limit=args.limit,
            include_chunks=not args.skip_chunks,
            rewrite_existing=not args.missing_only,
        )
    if args.command == "jobs":
        if args.jobs_command == "list-dead":
            return jobs_list_dead(limit=args.limit)
        if args.jobs_command == "retry":
            return jobs_retry(job_id=args.job_id)
    if args.command == "backfill-code-urls":
        return backfill_code_urls(
            limit=args.limit, page_size=args.page_size, dry_run=args.dry_run
        )
    if args.command == "enrich-missing":
        return enrich_missing(
            limit=args.limit,
            min_age_seconds=args.min_age_seconds,
            batch_size=args.batch_size,
            queue=args.queue,
        )
    if args.command == "harvest":
        if args.categories:
            categories = [cat.strip() for cat in args.categories.split(",") if cat.strip()]
        else:
            categories = list(_settings().arxiv_categories)
        return harvest(
            categories=categories,
            start_date=args.start_date,
            end_date=args.end_date,
            limit_per_window=args.limit_per_window,
            chunk_months=args.chunk_months,
        )
    if args.command == "harvest-top-cited":
        return harvest_top_cited(
            min_citations=args.min_citations,
            max_papers=args.max_papers,
            chunk_size=args.chunk_size,
        )
    if args.command == "harvest-venue":
        return harvest_venue(
            venue=args.venue,
            year=args.year,
            max_papers=args.max_papers,
            chunk_size=args.chunk_size,
        )
    if args.command == "harvest-author":
        return harvest_author(
            min_paper_count=args.min_paper_count,
            min_cite_mass=args.min_cite_mass,
            max_authors=args.max_authors,
            max_papers_per_author=args.max_papers_per_author,
            chunk_size=args.chunk_size,
        )
    if args.command == "backfill-paper-authors":
        return backfill_paper_authors(
            limit=args.limit,
            batch_size=args.batch_size,
        )

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
