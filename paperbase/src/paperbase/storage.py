"""Object storage adapters for paperbase artifacts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import boto3

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

    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        if not self.exists(path):
            return None
        return f"memory://{path}"


class R2ObjectStore(ObjectStore):
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.r2_bucket
        self._public_base_url = settings.r2_public_base_url.rstrip("/") if settings.r2_public_base_url else None
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=settings.r2_region_name,
        )

    def put_bytes(self, path: str, data: bytes, *, content_type: str | None = None) -> None:
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": path,
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        self._client.put_object(**kwargs)

    def get_bytes(self, path: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=path)
        return response["Body"].read()

    def exists(self, path: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=path)
            return True
        except self._client.exceptions.ClientError:
            return False

    def list_prefix(self, prefix: str) -> list[StoredObject]:
        paginator = self._client.get_paginator("list_objects_v2")
        files: list[StoredObject] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                files.append(
                    StoredObject(
                        path=item["Key"],
                        size=item["Size"],
                    )
                )
        return files

    def presign_get(self, path: str, *, expires_in: int) -> str | None:
        if self._public_base_url:
            return f"{self._public_base_url}/{path}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": path},
            ExpiresIn=expires_in,
        )


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "memory":
        return MemoryObjectStore()
    return R2ObjectStore(settings)
