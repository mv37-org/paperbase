"""Object storage adapters for paperbase artifacts."""

from __future__ import annotations

import json
import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
import shutil

from google.cloud import storage
from google.oauth2 import service_account

from .config import Settings


@dataclass(slots=True)
class StoredObject:
    path: str
    size: int
    content_type: str | None = None


class ObjectStore(ABC):
    @abstractmethod
    def put_bytes(self, path: str, data: bytes, *, content_type: str | None = None) -> None:
        raise NotImplementedError

    def put_text(self, path: str, text: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
        self.put_bytes(path, text.encode("utf-8"), content_type=content_type)

    @abstractmethod
    def get_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    def get_text(self, path: str) -> str:
        return self.get_bytes(path).decode("utf-8")

    @abstractmethod
    def exists(self, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[StoredObject]:
        raise NotImplementedError

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        raise NotImplementedError


class MemoryObjectStore(ObjectStore):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str | None]] = {}

    def put_bytes(self, path: str, data: bytes, *, content_type: str | None = None) -> None:
        self._objects[path] = (data, content_type)

    def get_bytes(self, path: str) -> bytes:
        return self._objects[path][0]

    def exists(self, path: str) -> bool:
        return path in self._objects

    def list_prefix(self, prefix: str) -> list[StoredObject]:
        files = []
        for path, (data, content_type) in sorted(self._objects.items()):
            if path.startswith(prefix):
                files.append(StoredObject(path=path, size=len(data), content_type=content_type))
        return files

    def delete_prefix(self, prefix: str) -> None:
        for path in list(self._objects):
            if path == prefix or path.startswith(f"{prefix}/"):
                del self._objects[path]

    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        if not self.exists(path):
            return None
        return f"memory://{path}"


class LocalFileObjectStore(ObjectStore):
    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.local_storage_root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _normalized_path(self, path: str) -> str:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            raise ValueError(f"Absolute object paths are not allowed: {path}")
        parts = [part for part in candidate.parts if part not in ("",)]
        if not parts:
            raise ValueError("Object path must not be empty")
        if any(part in (".", "..") for part in parts):
            raise ValueError(f"Unsafe object path: {path}")
        return PurePosixPath(*parts).as_posix()

    def _resolve(self, path: str) -> tuple[str, Path]:
        normalized = self._normalized_path(path)
        return normalized, self._root.joinpath(*PurePosixPath(normalized).parts)

    _EXTRA_CONTENT_TYPES = {".md": "text/markdown"}

    def _content_type_for(self, normalized: str) -> str | None:
        content_type, _ = mimetypes.guess_type(normalized)
        if content_type is None:
            suffix = PurePosixPath(normalized).suffix.lower()
            return self._EXTRA_CONTENT_TYPES.get(suffix)
        return content_type

    def put_bytes(self, path: str, data: bytes, *, content_type: str | None = None) -> None:
        _, target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_bytes(self, path: str) -> bytes:
        _, target = self._resolve(path)
        return target.read_bytes()

    def exists(self, path: str) -> bool:
        try:
            _, target = self._resolve(path)
        except ValueError:
            return False
        return target.is_file()

    def list_prefix(self, prefix: str) -> list[StoredObject]:
        try:
            normalized_prefix = self._normalized_path(prefix)
        except ValueError:
            return []
        prefix_prefix = normalized_prefix + "/"
        if not self._root.exists():
            return []
        files: list[StoredObject] = []
        for target in sorted(path for path in self._root.rglob("*") if path.is_file()):
            object_path = target.relative_to(self._root).as_posix()
            if object_path == normalized_prefix or object_path.startswith(prefix_prefix):
                files.append(
                    StoredObject(
                        path=object_path,
                        size=target.stat().st_size,
                        content_type=self._content_type_for(object_path),
                    )
                )
        return files

    def delete_prefix(self, prefix: str) -> None:
        try:
            _, target = self._resolve(prefix)
        except ValueError:
            return
        if target.is_file():
            target.unlink()
            return
        if target.is_dir():
            shutil.rmtree(target)
            return
        # If the exact file/directory path is absent, no child under that
        # local filesystem prefix can exist. Returning here avoids scanning
        # large storage roots for every missing test namespace.
        return

    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        try:
            _, target = self._resolve(path)
        except ValueError:
            return None
        if not target.is_file():
            return None
        return str(target)


def _build_gcs_client(settings: Settings) -> storage.Client:
    if settings.gcs_service_account_json:
        info = json.loads(settings.gcs_service_account_json)
        credentials = service_account.Credentials.from_service_account_info(info)
        project = settings.gcs_project or info.get("project_id")
        return storage.Client(project=project, credentials=credentials)
    if settings.gcs_project:
        return storage.Client(project=settings.gcs_project)
    return storage.Client()


class GoogleCloudStorageObjectStore(ObjectStore):
    def __init__(self, settings: Settings) -> None:
        self._bucket_name = settings.gcs_bucket
        self._public_base_url = settings.gcs_public_base_url.rstrip("/") if settings.gcs_public_base_url else None
        self._client = _build_gcs_client(settings)
        self._bucket = self._client.bucket(self._bucket_name)

    def _blob(self, path: str):
        return self._bucket.blob(path)

    def put_bytes(self, path: str, data: bytes, *, content_type: str | None = None) -> None:
        blob = self._blob(path)
        blob.upload_from_string(data, content_type=content_type)

    def get_bytes(self, path: str) -> bytes:
        return self._blob(path).download_as_bytes()

    def exists(self, path: str) -> bool:
        return bool(self._blob(path).exists(self._client))

    def list_prefix(self, prefix: str) -> list[StoredObject]:
        files: list[StoredObject] = []
        for blob in self._client.list_blobs(self._bucket_name, prefix=prefix):
            files.append(
                StoredObject(
                    path=blob.name,
                    size=blob.size or 0,
                    content_type=blob.content_type,
                )
            )
        return files

    def delete_prefix(self, prefix: str) -> None:
        blobs = list(self._client.list_blobs(self._bucket_name, prefix=prefix))
        if blobs:
            self._bucket.delete_blobs(blobs)

    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        if not self.exists(path):
            return None
        if self._public_base_url:
            return f"{self._public_base_url}/{path}"
        return self._blob(path).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in),
            method="GET",
        )


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "memory":
        return MemoryObjectStore()
    if settings.storage_backend == "local":
        return LocalFileObjectStore(settings)
    if settings.storage_backend == "gcs":
        return GoogleCloudStorageObjectStore(settings)
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
