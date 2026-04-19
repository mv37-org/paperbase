from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from paperbase.config import Settings
from paperbase.storage import GoogleCloudStorageObjectStore, LocalFileObjectStore, _build_gcs_client


class _FakeBlob:
    def __init__(self, name: str, objects: dict[str, tuple[bytes, str | None]]):
        self.name = name
        self._objects = objects

    @property
    def size(self) -> int | None:
        if self.name not in self._objects:
            return None
        return len(self._objects[self.name][0])

    @property
    def content_type(self) -> str | None:
        if self.name not in self._objects:
            return None
        return self._objects[self.name][1]

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._objects[self.name] = (data, content_type)

    def download_as_bytes(self) -> bytes:
        return self._objects[self.name][0]

    def exists(self, client) -> bool:
        return self.name in self._objects

    def generate_signed_url(self, *, version: str, expiration: timedelta, method: str) -> str:
        return f"https://signed.example/{self.name}?seconds={int(expiration.total_seconds())}&method={method}&version={version}"


class _FakeBucket:
    def __init__(self, objects: dict[str, tuple[bytes, str | None]]):
        self._objects = objects

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._objects)


class _FakeClient:
    def __init__(self):
        self._objects: dict[str, tuple[bytes, str | None]] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._objects)

    def list_blobs(self, bucket_name: str, prefix: str):
        for path in sorted(self._objects):
            if path.startswith(prefix):
                yield _FakeBlob(path, self._objects)


def test_build_gcs_client_uses_service_account_json(monkeypatch):
    calls: dict[str, object] = {}

    class _FakeCredentials:
        pass

    def fake_from_service_account_info(info):
        calls["info"] = info
        return _FakeCredentials()

    def fake_client(*, project=None, credentials=None):
        calls["project"] = project
        calls["credentials"] = credentials
        return "client"

    monkeypatch.setattr("paperbase.storage.service_account.Credentials.from_service_account_info", fake_from_service_account_info)
    monkeypatch.setattr("paperbase.storage.storage.Client", fake_client)

    settings = Settings(
        gcs_service_account_json='{"project_id":"paperbase-prod","client_email":"svc@example.com"}',
        gcs_project=None,
    )

    client = _build_gcs_client(settings)

    assert client == "client"
    assert calls["info"] == {"project_id": "paperbase-prod", "client_email": "svc@example.com"}
    assert calls["project"] == "paperbase-prod"
    assert isinstance(calls["credentials"], _FakeCredentials)


def test_gcs_object_store_round_trip_and_public_url(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr("paperbase.storage._build_gcs_client", lambda settings: fake_client)

    store = GoogleCloudStorageObjectStore(
        Settings(
            gcs_bucket="paperbase-test",
            gcs_public_base_url="https://storage.googleapis.com/paperbase-test",
        )
    )

    store.put_bytes("papers/arxiv:1/text/full.md", b"hello", content_type="text/markdown")

    assert store.get_bytes("papers/arxiv:1/text/full.md") == b"hello"
    assert store.exists("papers/arxiv:1/text/full.md") is True

    files = store.list_prefix("papers/arxiv:1/")
    assert len(files) == 1
    assert files[0].path == "papers/arxiv:1/text/full.md"
    assert files[0].content_type == "text/markdown"
    assert store.presign_get("papers/arxiv:1/text/full.md", expires_in=60) == "https://storage.googleapis.com/paperbase-test/papers/arxiv:1/text/full.md"


def test_gcs_object_store_generates_signed_url_when_not_public(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr("paperbase.storage._build_gcs_client", lambda settings: fake_client)

    store = GoogleCloudStorageObjectStore(Settings(gcs_bucket="paperbase-test"))
    store.put_bytes("papers/arxiv:2/tables/table-1.csv", b"a,b\n1,2\n", content_type="text/csv")

    url = store.presign_get("papers/arxiv:2/tables/table-1.csv", expires_in=120)

    assert url == "https://signed.example/papers/arxiv:2/tables/table-1.csv?seconds=120&method=GET&version=v4"


def test_local_file_object_store_round_trip_and_listing(tmp_path: Path):
    store = LocalFileObjectStore(Settings(storage_backend="local", local_storage_root=str(tmp_path)))

    store.put_bytes("papers/arxiv:1/text/full.md", b"hello", content_type="text/markdown")
    store.put_bytes("papers/arxiv:10/text/full.md", b"other", content_type="text/markdown")

    assert store.get_bytes("papers/arxiv:1/text/full.md") == b"hello"
    assert store.exists("papers/arxiv:1/text/full.md") is True

    files = store.list_prefix("papers/arxiv:1/")
    assert len(files) == 1
    assert files[0].path == "papers/arxiv:1/text/full.md"
    assert files[0].content_type == "text/markdown"
    assert store.presign_get("papers/arxiv:1/text/full.md", expires_in=60) == str(tmp_path / "papers/arxiv:1/text/full.md")


def test_local_file_object_store_rejects_path_escape(tmp_path: Path):
    store = LocalFileObjectStore(Settings(storage_backend="local", local_storage_root=str(tmp_path)))

    with pytest.raises(ValueError, match="Unsafe object path"):
        store.put_bytes("../escape.txt", b"nope")

    assert store.exists("../escape.txt") is False
    assert store.list_prefix("../") == []
    assert store.presign_get("../escape.txt", expires_in=60) is None
