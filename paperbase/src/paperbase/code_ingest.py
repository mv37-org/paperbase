"""Paper-code discovery, fetch, and Python eligibility (#5).

Scope for PR 1 is a read-only, paper-scoped ingestion pipeline:

1. ``parse_github_url`` — turn a ``papers.code_url`` into a ``GitHubRepoRef``
   or return ``None`` for unsupported hosts. Only ``github.com`` is supported
   in v1; GitLab / Bitbucket / HuggingFace URLs fall through.

2. ``fetch_github_archive`` — tarball fetch via ``api.github.com`` using an
   optional service-level ``PAPERBASE_GITHUB_TOKEN`` (distinct from the user
   OAuth token used for project repo sync). Streams the response and aborts
   if the archive exceeds ``Settings.code_repo_max_archive_bytes``.

3. ``extract_archive_safely`` — tarfile extraction with path-traversal guard,
   symlink rejection, per-file byte cap, and a hard cap on scanned member
   count. Any member that fails a check is dropped with a structured reason
   so the manifest can surface ``skipped_files`` counts.

4. ``scan_tree`` + ``classify_python`` — produce the manifest described in
   issue #5. Classifier implements the four heuristics from the spec; repos
   that only ship notebooks are rejected.

Indexing the extracted files into ``text_chunks`` is PR 2.
"""

from __future__ import annotations

import io
import os
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

import requests


# --------------------------------------------------------------------------- #
# URL parsing
# --------------------------------------------------------------------------- #

_SUPPORTED_HOST = "github.com"
_GITHUB_SUBPATH_RE = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/(?:tree|blob|commit)/(?P<ref>[^/]+)(?:/.*)?)?/?$"
)


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str
    ref: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_github_url(raw: str | None) -> GitHubRepoRef | None:
    """Return ``GitHubRepoRef`` for a GitHub URL, or ``None`` for anything
    else (unsupported host, malformed, empty).

    Accepts ``https://github.com/owner/repo``, ``http://...``, a bare
    ``github.com/owner/repo``, trailing ``.git``, and common sub-paths like
    ``/tree/{ref}`` / ``/blob/{ref}/...`` / ``/commit/{sha}``. Rejects URLs
    whose host is not literally ``github.com`` (gist, enterprise hosts,
    gitlab, etc. — PR 1 is strictly vanilla github.com).
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    # Normalize bare ``github.com/...`` to a parseable URL so urlparse sees a host.
    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if host != _SUPPORTED_HOST:
        return None
    match = _GITHUB_SUBPATH_RE.match(parsed.path or "/")
    if not match:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    ref = match.group("ref")
    if not owner or not repo:
        return None
    return GitHubRepoRef(owner=owner, repo=repo, ref=ref)


# --------------------------------------------------------------------------- #
# Archive fetch
# --------------------------------------------------------------------------- #


class CodeIngestError(RuntimeError):
    """Base class for ingestion-layer failures that should be recorded in
    ``code_manifest`` rather than propagated as a worker crash."""


class ArchiveTooLargeError(CodeIngestError):
    """Raised when the streamed archive exceeds the configured byte cap."""


class ArchiveFetchError(CodeIngestError):
    """Raised for non-200 responses from api.github.com."""


@dataclass(frozen=True)
class FetchedArchive:
    data: bytes
    resolved_sha: str | None
    etag: str | None


HttpGet = Callable[..., requests.Response]


def fetch_github_archive(
    ref: GitHubRepoRef,
    *,
    token: str | None = None,
    max_bytes: int,
    http_get: HttpGet = requests.get,
    timeout: float = 30.0,
) -> FetchedArchive:
    """Fetch the repo tarball from ``api.github.com``.

    ``http_get`` is injectable so tests can stub the network without
    monkeypatching ``requests``. The caller is responsible for retries /
    rate-limit handling — this function returns on the first 200 and raises
    on the first non-200.
    """
    segments = ["https://api.github.com/repos", ref.owner, ref.repo, "tarball"]
    if ref.ref:
        segments.append(ref.ref)
    url = "/".join(segments)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "paperbase-code-ingest"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = http_get(url, headers=headers, stream=True, allow_redirects=True, timeout=timeout)
    try:
        if response.status_code != 200:
            raise ArchiveFetchError(
                f"GitHub archive fetch returned {response.status_code} for {ref.slug}"
            )
        buffer = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise ArchiveTooLargeError(
                    f"GitHub archive for {ref.slug} exceeded cap "
                    f"({len(buffer)} > {max_bytes} bytes)"
                )
        # The resolved commit SHA is exposed either in the redirect URL
        # (``/archive/{sha}.tar.gz``) or in the ETag (``"{sha}"``). Check
        # both since requests elides one or the other depending on the
        # response shape.
        resolved_sha = _sha_from_redirect(response) or _sha_from_etag(response.headers.get("ETag"))
        etag = response.headers.get("ETag")
        return FetchedArchive(data=bytes(buffer), resolved_sha=resolved_sha, etag=etag)
    finally:
        response.close()


def _sha_from_redirect(response: requests.Response) -> str | None:
    final_url = getattr(response, "url", "") or ""
    match = re.search(r"/archive/(?:refs/\w+/)?([0-9a-f]{40})", final_url)
    return match.group(1) if match else None


def _sha_from_etag(etag: str | None) -> str | None:
    if not etag:
        return None
    match = re.search(r"([0-9a-f]{40})", etag)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# Safe extraction
# --------------------------------------------------------------------------- #


EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "vendor",
        "third_party",
    }
)


@dataclass
class ScannedFile:
    repo_path: str
    size_bytes: int
    stored: bool


@dataclass
class ScanReport:
    """Output of ``extract_archive_safely``. Everything the manifest and the
    Python classifier need is precomputed here — callers do not re-walk the
    extracted tree."""

    scanned_paths: list[str] = field(default_factory=list)
    stored_paths: list[str] = field(default_factory=list)
    extension_counts: dict[str, int] = field(default_factory=dict)
    excluded_directories: list[str] = field(default_factory=list)
    skipped_files: dict[str, int] = field(default_factory=dict)
    readme_paths: list[str] = field(default_factory=list)
    detected_manifests: list[str] = field(default_factory=list)
    candidate_entrypoints: list[str] = field(default_factory=list)
    python_file_count: int = 0
    notebook_count: int = 0
    readme_text: str = ""


_PYTHON_MANIFEST_FILES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "environment.yml",
    }
)

_PYTHON_REQUIREMENTS_RE = re.compile(r"^requirements.*\.txt$", re.IGNORECASE)
_ENTRYPOINT_HINT_RE = re.compile(r"^(main|train|run|cli|app|server|__main__)\.py$", re.IGNORECASE)
_ENTRYPOINT_PARENTS: frozenset[str] = frozenset({"", "scripts", "examples"})


def extract_archive_safely(
    archive_bytes: bytes,
    dest: Path,
    *,
    max_stored_file_bytes: int,
    max_scanned_files: int,
) -> ScanReport:
    """Extract the tarball at ``archive_bytes`` into ``dest`` with safety
    guards. Members that violate a guard are dropped with a structured
    reason recorded in the returned :class:`ScanReport`.

    The first path segment of a GitHub tarball is always
    ``{owner}-{repo}-{sha}``; we strip it so ``dest/README.md`` ends up at
    ``dest/README.md`` instead of ``dest/repo-abc123/README.md``.
    """
    dest.mkdir(parents=True, exist_ok=True)
    report = ScanReport()
    excluded_seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            if len(report.scanned_paths) >= max_scanned_files:
                report.skipped_files["scan_cap"] = report.skipped_files.get("scan_cap", 0) + 1
                continue
            if _is_path_unsafe(member.name):
                _bump(report.skipped_files, "path_traversal")
                continue
            rel_path = _strip_archive_prefix(member.name)
            if rel_path is None or rel_path in {"", "."}:
                continue
            if member.issym() or member.islnk():
                _bump(report.skipped_files, "symlink")
                continue
            if _is_path_unsafe(rel_path):
                _bump(report.skipped_files, "path_traversal")
                continue
            excluded_dir = _first_excluded_segment(rel_path)
            if excluded_dir is not None:
                if excluded_dir not in excluded_seen:
                    excluded_seen.add(excluded_dir)
                    report.excluded_directories.append(excluded_dir)
                _bump(report.skipped_files, "excluded_dir")
                continue
            if member.isdir():
                continue
            if not member.isfile():
                _bump(report.skipped_files, "non_regular")
                continue
            if member.size > max_stored_file_bytes:
                report.scanned_paths.append(rel_path)
                _bump(report.skipped_files, "oversize")
                _record_extension(report.extension_counts, rel_path)
                _update_classification_hints(report, rel_path)
                continue
            try:
                extracted = tar.extractfile(member)
            except KeyError:  # pragma: no cover — malformed tarball
                _bump(report.skipped_files, "unreadable")
                continue
            if extracted is None:
                _bump(report.skipped_files, "unreadable")
                continue
            payload = extracted.read()
            if _looks_binary(payload):
                report.scanned_paths.append(rel_path)
                _bump(report.skipped_files, "binary")
                _record_extension(report.extension_counts, rel_path)
                _update_classification_hints(report, rel_path)
                continue
            target = (dest / rel_path).resolve()
            if not _is_within(target, dest.resolve()):
                _bump(report.skipped_files, "path_traversal")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            report.scanned_paths.append(rel_path)
            report.stored_paths.append(rel_path)
            _record_extension(report.extension_counts, rel_path)
            _update_classification_hints(report, rel_path, payload=payload)
    return report


def _strip_archive_prefix(name: str) -> str | None:
    """GitHub tarballs wrap everything in a single top-level directory named
    ``{owner}-{repo}-{sha}/``. Drop that prefix so callers see repo-relative
    paths like ``README.md`` directly."""
    if not name:
        return None
    parts = PurePosixPath(name).parts
    if len(parts) < 2:
        return None
    stripped = parts[1:] if len(parts) > 1 else ()
    return "/".join(stripped)


def _is_path_unsafe(rel_path: str) -> bool:
    if rel_path.startswith("/") or os.path.isabs(rel_path):
        return True
    parts = PurePosixPath(rel_path).parts
    return any(part == ".." for part in parts)


def _first_excluded_segment(rel_path: str) -> str | None:
    for segment in PurePosixPath(rel_path).parts:
        if segment in EXCLUDED_DIRS:
            return segment
    return None


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _looks_binary(payload: bytes) -> bool:
    sample = payload[:8192]
    return b"\x00" in sample


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _record_extension(counts: dict[str, int], rel_path: str) -> None:
    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix:
        counts[suffix] = counts.get(suffix, 0) + 1


def _update_classification_hints(
    report: ScanReport, rel_path: str, *, payload: bytes | None = None
) -> None:
    posix = PurePosixPath(rel_path)
    name_lower = posix.name.lower()
    parent = "/".join(posix.parts[:-1])
    top_parent = posix.parts[0] if len(posix.parts) > 1 else ""
    if name_lower.endswith(".py"):
        report.python_file_count += 1
        if _ENTRYPOINT_HINT_RE.match(posix.name) and top_parent in _ENTRYPOINT_PARENTS:
            report.candidate_entrypoints.append(rel_path)
    if name_lower.endswith(".ipynb"):
        report.notebook_count += 1
    if name_lower in _PYTHON_MANIFEST_FILES or _PYTHON_REQUIREMENTS_RE.match(name_lower):
        if parent == "":  # root-level manifests only
            report.detected_manifests.append(rel_path)
    if name_lower.startswith("readme"):
        report.readme_paths.append(rel_path)
        if payload is not None and not report.readme_text and parent == "":
            try:
                report.readme_text = payload.decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover — decode fallback
                report.readme_text = ""


# --------------------------------------------------------------------------- #
# Python classifier
# --------------------------------------------------------------------------- #


_README_HINT_RE = re.compile(r"\b(python|pip|uv|pytest)\b", re.IGNORECASE)


def classify_python(report: ScanReport) -> tuple[bool, str]:
    """Apply the four heuristics from issue #5 to a :class:`ScanReport`.

    Returns ``(is_python, reason)`` where ``reason`` is a short,
    stable-across-releases tag that explains the verdict. Callers persist
    this pair on the paper via ``PaperORM.code_is_python`` and
    ``code_python_reason`` so later tools can surface it without rerunning
    the scan.
    """
    if report.python_file_count >= 1 and report.detected_manifests:
        return True, "python_source_with_manifest"
    if report.python_file_count >= 5:
        return True, "multiple_python_sources"
    if report.candidate_entrypoints and _README_HINT_RE.search(report.readme_text or ""):
        return True, "entrypoint_plus_readme_hint"
    if report.notebook_count > 0 and report.python_file_count == 0:
        return False, "notebooks_only"
    if report.python_file_count == 0:
        return False, "no_python_sources"
    return False, "insufficient_python_signals"


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


def build_manifest(
    *,
    code_url: str,
    ref: GitHubRepoRef,
    resolved_sha: str | None,
    report: ScanReport,
    is_python: bool | None,
    python_reason: str,
) -> dict[str, Any]:
    """Shape the manifest that gets persisted to ``papers.code_manifest``.

    Field list matches the spec in issue #5 (arslnb comment at
    2026-04-23T21:16). ``indexed_paths`` is deliberately omitted — PR 2
    will add it when selective code indexing lands.
    """
    return {
        "code_url": code_url,
        "source_kind": "github",
        "owner": ref.owner,
        "repo": ref.repo,
        "ref": ref.ref,
        "resolved_sha": resolved_sha,
        "scanned_paths": list(report.scanned_paths),
        "stored_paths": list(report.stored_paths),
        "extension_counts": dict(report.extension_counts),
        "excluded_directories": list(report.excluded_directories),
        "skipped_files": dict(report.skipped_files),
        "candidate_entrypoints": list(report.candidate_entrypoints),
        "detected_manifests": list(report.detected_manifests),
        "readme_paths": list(report.readme_paths),
        "python_file_count": report.python_file_count,
        "notebook_count": report.notebook_count,
        "code_is_python": is_python,
        "code_python_reason": python_reason,
    }


def skipped_manifest(*, code_url: str, reason: str) -> dict[str, Any]:
    """Minimal manifest for a paper whose ``code_url`` is unsupported or
    malformed. Produces the same top-level shape as :func:`build_manifest`
    so consumers can rely on ``source_kind`` / ``code_python_reason``
    without branching."""
    return {
        "code_url": code_url,
        "source_kind": "unsupported",
        "owner": None,
        "repo": None,
        "ref": None,
        "resolved_sha": None,
        "scanned_paths": [],
        "stored_paths": [],
        "extension_counts": {},
        "excluded_directories": [],
        "skipped_files": {},
        "candidate_entrypoints": [],
        "detected_manifests": [],
        "readme_paths": [],
        "python_file_count": 0,
        "notebook_count": 0,
        "code_is_python": None,
        "code_python_reason": reason,
    }


__all__ = [
    "ArchiveFetchError",
    "ArchiveTooLargeError",
    "CodeIngestError",
    "EXCLUDED_DIRS",
    "FetchedArchive",
    "GitHubRepoRef",
    "ScanReport",
    "ScannedFile",
    "build_manifest",
    "classify_python",
    "extract_archive_safely",
    "fetch_github_archive",
    "parse_github_url",
    "skipped_manifest",
]
