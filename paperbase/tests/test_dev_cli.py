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
    enrich_missing,
    harvest,
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


def test_harvest_cli_reports_invalid_limit_from_service(monkeypatch, capsys):
    from paperbase.errors import PermanentError

    class FakeService:
        def enqueue_harvest_range_job(self, **_kwargs):
            raise PermanentError("limit_per_window must be >= 1 when provided")

    monkeypatch.setattr("paperbase.service.get_service", lambda: FakeService())

    exit_code = harvest(
        categories=["cs.LG"],
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit_per_window=-1,
        chunk_months=3,
    )

    assert exit_code == 1
    assert "Invalid harvest request: limit_per_window must be >= 1" in capsys.readouterr().out


def test_enrich_missing_cli_reports_partial_failure_without_traceback(monkeypatch, capsys):
    class FakeRepository:
        def __init__(self) -> None:
            self.calls = 0

        def enrichment_stats(self):
            self.calls += 1
            if self.calls == 1:
                return {"total_indexed": 10, "enriched": 2}
            return {"total_indexed": 10, "enriched": 4}

    class FakeService:
        def __init__(self) -> None:
            self.repository = FakeRepository()

        def enrich_missing_metadata(self, **kwargs):
            assert kwargs == {"limit": 10, "min_age_seconds": 3600, "batch_size": 20}
            return {"papers_scanned": 10, "papers_updated": 2, "batches_failed": 1}

    monkeypatch.setattr("paperbase.service.get_service", lambda: FakeService())

    exit_code = enrich_missing(limit=10, min_age_seconds=3600, batch_size=20)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Scanned:        10" in output
    assert "Updated:        2" in output
    assert "Batches failed: 1" in output
    assert "Incomplete: rerun paperbase-dev enrich-missing" in output


def test_enrich_missing_cli_queue_mode_enqueues_job(monkeypatch, capsys):
    """``--queue`` enqueues an ``enrich_missing_metadata`` job and exits
    without running enrichment in the CLI process (#38). Lets operators
    backfill at corpus scale without tying up the serving container."""
    from datetime import datetime, timezone
    from paperbase.models import JobResponse

    class FakeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, int]]] = []

        def enqueue_enrich_missing_metadata_job(self, *, limit: int, min_age_seconds: int):
            self.calls.append(("enqueue", {"limit": limit, "min_age_seconds": min_age_seconds}))
            now = datetime(2026, 4, 28, tzinfo=timezone.utc)
            return JobResponse(
                job_id="job_enrich_q1",
                job_type="enrich_missing_metadata",
                status="pending",
                payload={"limit": limit, "min_age_seconds": min_age_seconds},
                created_at=now,
                updated_at=now,
            )

        # If we accidentally ran in synchronous mode this would be hit;
        # leaving it unimplemented makes the regression loud.
        def enrich_missing_metadata(self, **_kwargs):
            raise AssertionError("queue-mode CLI must not invoke synchronous enrichment")

    fake = FakeService()
    monkeypatch.setattr("paperbase.service.get_service", lambda: fake)

    exit_code = enrich_missing(limit=2000, min_age_seconds=600, batch_size=20, queue=True)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert fake.calls == [("enqueue", {"limit": 2000, "min_age_seconds": 600})]
    assert "Queued enrich_missing_metadata job_enrich_q1" in output
    assert "limit=2000" in output
    assert "min_age_seconds=600" in output
