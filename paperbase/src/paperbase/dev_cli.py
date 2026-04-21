"""Developer-oriented local admin commands for paperbase."""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from .config import Settings, get_settings, repo_root, resolve_repo_path, settings_env_file
from .repository import build_repository


LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
API_HOST = "127.0.0.1"
API_PORT = 8080
CHAT_PORT = 5173
PLUGIN_MCP_PORT = 8090


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _repo_root() -> Path:
    root = repo_root()
    if root is None:
        raise RuntimeError("Could not locate the repo root from the installed package layout.")
    return root


def _repo_env_file() -> Path:
    return settings_env_file().resolve()


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


def _check_results(settings: Settings) -> Iterable[CheckResult]:
    env_file = _repo_env_file()
    storage_root = _local_storage_root(settings)
    database_url = make_url(settings.database_url)

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

    if settings.storage_backend != "local":
        yield CheckResult(
            "storage backend",
            "fail",
            f"Expected PAPERBASE_STORAGE_BACKEND=local for local development, got {settings.storage_backend!r}",
        )
    else:
        yield CheckResult("storage backend", "ok", f"Local storage root: {storage_root}")

    try:
        _assert_local_database_url(database_url)
    except ValueError as exc:
        yield CheckResult("database url", "fail", str(exc))
        return

    yield CheckResult("database url", "ok", str(database_url))

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


def doctor() -> int:
    env_file = _repo_env_file()
    settings = get_settings() if env_file.exists() else Settings()
    failures = 0
    warnings = 0
    for result in _check_results(settings):
        print(f"[{result.status.upper():4}] {result.name}: {result.detail}")
        if result.status == "fail":
            failures += 1
        elif result.status == "warn":
            warnings += 1
    if failures:
        print(f"Doctor found {failures} failure(s) and {warnings} warning(s).")
        return 1
    print(f"Doctor finished with {warnings} warning(s).")
    return 0


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local developer helpers for paperbase.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Verify local prerequisites and the repo env file.")
    subparsers.add_parser("init-local", help="Create the local database, storage root, and schema.")
    subparsers.add_parser("flush-local", help="Reset the local database schema and storage root.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()
    if args.command == "init-local":
        return init_local()
    if args.command == "flush-local":
        return flush_local()

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
