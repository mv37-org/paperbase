"""Shared helpers used across paperbase."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def canonical_paper_id(arxiv_id: str) -> str:
    return f"arxiv:{arxiv_id.strip()}"


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "section"


def make_slug(base: str, seen: set[str]) -> str:
    candidate = slugify(base)
    if candidate not in seen:
        seen.add(candidate)
        return candidate
    index = 2
    while f"{candidate}-{index}" in seen:
        index += 1
    resolved = f"{candidate}-{index}"
    seen.add(resolved)
    return resolved


def literal_hint_from_pattern(pattern: str) -> str:
    text = re.sub(r"\\[wWdDsS.*+?^$|(){}\[\]]", "", pattern)
    text = re.sub(r"[.*+?^$|(){}\[\]\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:128]


def csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
