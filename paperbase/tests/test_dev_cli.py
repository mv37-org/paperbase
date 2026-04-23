from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from paperbase.config import Settings
from paperbase.dev_cli import (
    CheckResult,
    DoctorMode,
    _assert_local_database_url,
    _assert_safe_storage_root,
    _missing_required_env_vars,
    doctor,
    init_local,
    main,
    verify_run,
)


def test_doctor_returns_success_when_all_checks_pass(monkeypatch, capsys):
    monkeypatch.setattr(
        "paperbase.dev_cli._check_results",
        lambda settings, *, mode: [
            CheckResult("vp", "ok", "found"),
            CheckResult("postgres", "warn", "already running"),
        ],
    )

    exit_code = doctor(mode=DoctorMode.TEST)

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Running doctor in test mode." in captured
    assert "Doctor finished with 1 warning(s)." in captured


def test_doctor_returns_failure_when_any_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        "paperbase.dev_cli._check_results",
        lambda settings, *, mode: [
            CheckResult("env file", "fail", "missing"),
            CheckResult("postgres", "warn", "already running"),
        ],
    )

    exit_code = doctor(mode=DoctorMode.PROFILE)

    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "Running doctor in profile mode." in captured
    assert "Doctor found 1 failure(s) and 1 warning(s)." in captured


def test_verify_run_returns_success_when_env_and_schema_checks_pass(monkeypatch, capsys):
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )
    monkeypatch.setattr("paperbase.dev_cli._settings", lambda: settings)
    monkeypatch.setattr(
        "paperbase.dev_cli._verify_run_results",
        lambda current, *, mode: [
            CheckResult("required env vars", "ok", "resolved"),
            CheckResult("database schema", "ok", "ready"),
        ],
    )

    exit_code = verify_run(mode=DoctorMode.TEST)

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Verifying test run prerequisites." in captured
    assert "Run verification finished with 0 warning(s)." in captured


def test_verify_run_returns_failure_when_run_prereqs_are_missing(monkeypatch, capsys):
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )
    monkeypatch.setattr("paperbase.dev_cli._settings", lambda: settings)
    monkeypatch.setattr(
        "paperbase.dev_cli._verify_run_results",
        lambda current, *, mode: [
            CheckResult("required env vars", "fail", "missing PAPERBASE_API_KEY"),
            CheckResult("database schema", "ok", "ready"),
        ],
    )

    exit_code = verify_run(mode=DoctorMode.PROFILE)

    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "Verifying profile run prerequisites." in captured
    assert "Run verification found 1 failure(s) and 0 warning(s)." in captured


def test_assert_local_database_url_rejects_remote_hosts():
    with pytest.raises(ValueError, match="non-local database host"):
        _assert_local_database_url(make_url("postgresql+psycopg://postgres:postgres@db.example.com:5432/paperbase"))


def test_assert_safe_storage_root_rejects_repo_root_and_paths_outside_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ValueError, match="repository root"):
        _assert_safe_storage_root(root, root)

    outside = tmp_path / "outside"
    with pytest.raises(ValueError, match="outside the repo"):
        _assert_safe_storage_root(outside, root)


def test_init_local_bootstraps_database_and_storage(monkeypatch, tmp_path, capsys):
    storage_root = tmp_path / "local-storage"
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase_test",
        storage_backend="local",
        local_storage_root=str(storage_root),
    )
    repo_calls: list[str] = []

    class FakeRepository:
        def init(self) -> None:
            repo_calls.append("init")

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("paperbase.dev_cli._settings", lambda: settings)
    monkeypatch.setattr("paperbase.dev_cli._ensure_local_database", lambda current: "paperbase_test")
    monkeypatch.setattr("paperbase.dev_cli.build_repository", lambda current: FakeRepository())
    monkeypatch.setattr(
        "paperbase.dev_cli._extension_installed",
        lambda connection, extension_name: extension_name == "vector",
    )
    monkeypatch.setattr("paperbase.dev_cli.psycopg.connect", lambda *args, **kwargs: FakeConnection())

    exit_code = init_local()

    assert exit_code == 0
    assert storage_root.exists()
    assert repo_calls == ["init"]
    output = capsys.readouterr().out
    assert "Initialized local database: paperbase_test" in output
    assert "vector installed:          yes" in output
    assert "pg_trgm installed:         no" in output


def test_missing_required_env_vars_reads_shell_and_repo_env(monkeypatch):
    monkeypatch.delenv("PAPERBASE_DATABASE_URL", raising=False)
    monkeypatch.delenv("PAPERBASE_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("PAPERBASE_LOCAL_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("PAPERBASE_API_KEY", raising=False)
    monkeypatch.setenv("PAPERBASE_API_KEY", "token-from-shell")

    missing = _missing_required_env_vars(
        DoctorMode.TEST,
        env_values={
            "PAPERBASE_DATABASE_URL": "postgresql+psycopg:///paperbase",
            "PAPERBASE_STORAGE_BACKEND": "local",
            "PAPERBASE_LOCAL_STORAGE_ROOT": ".paperbase-data",
        },
    )

    assert missing == []


def test_missing_required_env_vars_reports_unset_values(monkeypatch):
    monkeypatch.delenv("PAPERBASE_DATABASE_URL", raising=False)
    monkeypatch.delenv("PAPERBASE_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("PAPERBASE_LOCAL_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("PAPERBASE_API_KEY", raising=False)

    missing = _missing_required_env_vars(DoctorMode.PROFILE, env_values={})

    assert missing == [
        "PAPERBASE_DATABASE_URL",
        "PAPERBASE_STORAGE_BACKEND",
        "PAPERBASE_LOCAL_STORAGE_ROOT",
        "PAPERBASE_API_KEY",
    ]


def test_main_dispatches_record_test_times(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_record_test_times(**kwargs):
        captured.update(kwargs)
        return 17

    monkeypatch.setattr("paperbase.dev_cli.record_test_times", fake_record_test_times)

    exit_code = main(
        [
            "record-test-times",
            "--backend-junit",
            str(tmp_path / "backend.xml"),
            "--plugin-junit",
            str(tmp_path / "plugin.xml"),
            "--chat-json",
            str(tmp_path / "chat.json"),
            "--update-baseline",
        ]
    )

    assert exit_code == 17
    assert captured["update_baseline"] is True
    assert captured["backend_junit"] == (tmp_path / "backend.xml").resolve()
    assert captured["plugin_junit"] == (tmp_path / "plugin.xml").resolve()
    assert captured["chat_json"] == (tmp_path / "chat.json").resolve()


def test_main_dispatches_profile_with_current_settings(monkeypatch):
    settings = Settings(
        database_url="postgresql+psycopg:///paperbase",
        storage_backend="local",
        local_storage_root=".paperbase-data",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr("paperbase.dev_cli._settings", lambda: settings)
    monkeypatch.setattr(
        "paperbase.dev_cli.profile_hotpaths",
        lambda *, settings, update_baseline: captured.update(
            {"settings": settings, "update_baseline": update_baseline}
        )
        or 23,
    )

    exit_code = main(["profile", "--update-baseline"])

    assert exit_code == 23
    assert captured == {"settings": settings, "update_baseline": True}


def test_main_dispatches_reindex_embeddings(monkeypatch):
    captured: dict[str, object] = {}

    def fake_reindex_embeddings(**kwargs):
        captured.update(kwargs)
        return 29

    monkeypatch.setattr("paperbase.dev_cli.reindex_embeddings", fake_reindex_embeddings)

    exit_code = main(["reindex-embeddings", "--limit", "42", "--skip-chunks", "--missing-only"])

    assert exit_code == 29
    assert captured == {
        "limit": 42,
        "include_chunks": False,
        "rewrite_existing": False,
    }


def test_main_dispatches_doctor_mode(monkeypatch):
    captured: list[DoctorMode] = []
    monkeypatch.setattr("paperbase.dev_cli.doctor", lambda *, mode: captured.append(mode) or 0)

    exit_code = main(["doctor", "--mode", "profile"])

    assert exit_code == 0
    assert captured == [DoctorMode.PROFILE]


def test_main_dispatches_verify_run_mode(monkeypatch):
    captured: list[DoctorMode] = []
    monkeypatch.setattr("paperbase.dev_cli.verify_run", lambda *, mode: captured.append(mode) or 0)

    exit_code = main(["verify-run", "--mode", "test"])

    assert exit_code == 0
    assert captured == [DoctorMode.TEST]
