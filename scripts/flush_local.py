#!/usr/bin/env python3
"""Reset the local paperbase database and filesystem storage."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url


LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def _as_psycopg_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _assert_local_database_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"Only PostgreSQL URLs are supported: {url}")
    if not url.database:
        raise ValueError("Database URL must include a database name")

    host = url.host or ""
    query_host = str(url.query.get("host", "") or "")
    if host and host not in LOCAL_HOSTS:
        raise ValueError(f"Refusing to flush non-local database host: {host}")
    if query_host and not query_host.startswith("/") and query_host not in LOCAL_HOSTS:
        raise ValueError(f"Refusing to flush non-local database host: {query_host}")


def _assert_safe_storage_root(storage_root: Path, repo_root: Path) -> None:
    if storage_root == repo_root:
        raise ValueError("Refusing to delete the repository root")
    if repo_root not in storage_root.parents:
        raise ValueError(f"Refusing to clear storage outside the repo: {storage_root}")


def _reset_database(database_url: str) -> str:
    url = make_url(database_url)
    _assert_local_database_url(url)

    database_name = url.database
    admin_database = "template1" if database_name == "postgres" else "postgres"
    admin_url = url.set(database=admin_database)
    target_url = url

    with psycopg.connect(_as_psycopg_dsn(admin_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    with psycopg.connect(_as_psycopg_dsn(target_url), autocommit=True) as connection:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset local paperbase database and storage.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    storage_root = Path(args.storage_root).expanduser().resolve()

    _assert_safe_storage_root(storage_root, repo_root)
    database_name = _reset_database(args.database_url)
    _reset_storage_root(storage_root)

    print(f"Reset database schema: {database_name}")
    print(f"Reset storage:         {storage_root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"flush_local.py failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
