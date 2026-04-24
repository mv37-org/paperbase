"""Unit tests for the paper-code ingestion module (#5).

Covers URL parsing, archive-safety guards (path traversal, symlink, oversize,
binary, excluded dirs), Python eligibility classification across the four
heuristics, and manifest shape. Network is never touched — archives are built
in-memory with :mod:`tarfile`.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from paperbase import code_ingest
from paperbase.code_ingest import (
    GitHubRepoRef,
    ScanReport,
    build_manifest,
    classify_python,
    extract_archive_safely,
    parse_github_url,
    skipped_manifest,
)


# --------------------------------------------------------------------------- #
# URL parser
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/owner/repo", GitHubRepoRef("owner", "repo")),
        ("http://github.com/owner/repo", GitHubRepoRef("owner", "repo")),
        ("https://github.com/owner/repo/", GitHubRepoRef("owner", "repo")),
        ("https://github.com/owner/repo.git", GitHubRepoRef("owner", "repo")),
        ("https://www.github.com/owner/repo", GitHubRepoRef("owner", "repo")),
        ("github.com/owner/repo", GitHubRepoRef("owner", "repo")),
        ("https://github.com/owner/repo/tree/main", GitHubRepoRef("owner", "repo", "main")),
        (
            "https://github.com/owner/repo/blob/v1.0/README.md",
            GitHubRepoRef("owner", "repo", "v1.0"),
        ),
        (
            "https://github.com/owner/repo/commit/0123456789abcdef0123456789abcdef01234567",
            GitHubRepoRef("owner", "repo", "0123456789abcdef0123456789abcdef01234567"),
        ),
    ],
)
def test_parse_github_url_accepts_supported_variants(raw: str, expected: GitHubRepoRef) -> None:
    assert parse_github_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not-a-url",
        "https://gitlab.com/owner/repo",
        "https://bitbucket.org/owner/repo",
        "https://huggingface.co/owner/repo",
        "https://gist.github.com/owner/abc",
        "ftp://github.com/owner/repo",
        "https://github.com/",
        "https://github.com/owner",
    ],
)
def test_parse_github_url_rejects_unsupported(raw: str) -> None:
    assert parse_github_url(raw) is None


def test_parse_github_url_handles_none_and_whitespace() -> None:
    assert parse_github_url(None) is None
    assert parse_github_url("   \n  ") is None


# --------------------------------------------------------------------------- #
# Archive extraction safety
# --------------------------------------------------------------------------- #


def _build_tarball(entries: list[tuple[str, bytes | str | None, str]]) -> bytes:
    """Build a GitHub-shaped tarball in memory.

    Each entry is ``(name, payload, kind)`` where kind is ``"file"``,
    ``"dir"``, or ``"symlink"`` (payload holds the link target). Names are
    expected to include the top-level ``owner-repo-sha/`` prefix so the
    extractor's prefix-stripping path is exercised.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload, kind in entries:
            info = tarfile.TarInfo(name=name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload if isinstance(payload, str) else ""
                tar.addfile(info)
            else:
                data = payload.encode("utf-8") if isinstance(payload, str) else (payload or b"")
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_archive_strips_top_level_prefix_and_stores_text_files(tmp_path: Path) -> None:
    archive = _build_tarball(
        [
            ("owner-repo-abc123/", None, "dir"),
            ("owner-repo-abc123/README.md", "# hello\n", "file"),
            ("owner-repo-abc123/src/", None, "dir"),
            ("owner-repo-abc123/src/mod.py", "x = 1\n", "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    assert sorted(report.stored_paths) == ["README.md", "src/mod.py"]
    assert (tmp_path / "README.md").read_text() == "# hello\n"
    assert (tmp_path / "src/mod.py").read_text() == "x = 1\n"


def test_extract_archive_drops_symlinks_and_path_traversal(tmp_path: Path) -> None:
    archive = _build_tarball(
        [
            ("owner-repo-abc123/README.md", "ok\n", "file"),
            ("owner-repo-abc123/link", "/etc/passwd", "symlink"),
            ("owner-repo-abc123/../escape.py", "danger\n", "file"),
            ("owner-repo-abc123/nested/../../escape2.py", "danger\n", "file"),
            ("/tmp/absolute.py", "danger\n", "file"),
            ("../outside.py", "danger\n", "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    # Neither escape attempt nor the symlink should land on disk.
    assert (tmp_path / "escape.py").exists() is False
    assert (tmp_path / "escape2.py").exists() is False
    assert (tmp_path / "absolute.py").exists() is False
    assert (tmp_path / "outside.py").exists() is False
    assert report.skipped_files.get("symlink") == 1
    assert report.skipped_files.get("path_traversal", 0) >= 4
    assert "README.md" in report.stored_paths


def test_extract_archive_enforces_per_file_cap(tmp_path: Path) -> None:
    big = "x" * 4096
    archive = _build_tarball(
        [
            ("owner-repo-abc123/small.txt", "ok\n", "file"),
            ("owner-repo-abc123/big.txt", big, "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    assert "small.txt" in report.stored_paths
    assert "big.txt" not in report.stored_paths
    assert report.skipped_files.get("oversize") == 1


def test_extract_archive_caps_total_scanned_files(tmp_path: Path) -> None:
    entries = [("owner-repo-abc123/", None, "dir")]
    for idx in range(10):
        entries.append((f"owner-repo-abc123/f{idx}.py", "x = 1\n", "file"))
    archive = _build_tarball(entries)
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=3,
    )
    assert len(report.scanned_paths) == 3
    assert report.skipped_files.get("scan_cap", 0) >= 1


def test_extract_archive_skips_excluded_dirs(tmp_path: Path) -> None:
    archive = _build_tarball(
        [
            ("owner-repo-abc123/.git/", None, "dir"),
            ("owner-repo-abc123/.git/HEAD", "ref: refs/heads/main\n", "file"),
            ("owner-repo-abc123/node_modules/", None, "dir"),
            ("owner-repo-abc123/node_modules/foo/index.js", "module.exports={}\n", "file"),
            ("owner-repo-abc123/__pycache__/ignore.pyc", b"\x00\x01\x02", "file"),
            ("owner-repo-abc123/src/mod.py", "x = 1\n", "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    assert "src/mod.py" in report.stored_paths
    # None of the excluded-dir files should land on disk or count toward python stats.
    assert not any(p.startswith(".git/") for p in report.stored_paths)
    assert not any(p.startswith("node_modules/") for p in report.stored_paths)
    assert not any(p.startswith("__pycache__/") for p in report.stored_paths)
    assert set(report.excluded_directories) == {".git", "node_modules", "__pycache__"}
    assert report.skipped_files.get("excluded_dir", 0) >= 3


def test_extract_archive_drops_binary_content(tmp_path: Path) -> None:
    archive = _build_tarball(
        [
            ("owner-repo-abc123/README.md", "ok\n", "file"),
            ("owner-repo-abc123/binary.dat", b"\x00\x01\x02\x03\x04hello", "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    assert "binary.dat" not in report.stored_paths
    assert report.skipped_files.get("binary") == 1
    # Scanned list must still include it so extension_counts stay honest.
    assert "binary.dat" in report.scanned_paths
    assert report.extension_counts.get(".dat") == 1


def test_extract_archive_captures_readme_text_and_entrypoints(tmp_path: Path) -> None:
    archive = _build_tarball(
        [
            (
                "owner-repo-abc123/README.md",
                "# Demo\nRun with `python main.py` (install via pip).\n",
                "file",
            ),
            ("owner-repo-abc123/main.py", "print('hi')\n", "file"),
            ("owner-repo-abc123/scripts/train.py", "pass\n", "file"),
            ("owner-repo-abc123/pyproject.toml", "[project]\n", "file"),
        ]
    )
    report = extract_archive_safely(
        archive,
        tmp_path,
        max_stored_file_bytes=1024,
        max_scanned_files=100,
    )
    assert "README.md" in report.readme_paths
    assert "pyproject.toml" in report.detected_manifests
    assert "main.py" in report.candidate_entrypoints
    assert "scripts/train.py" in report.candidate_entrypoints
    assert "python main.py" in report.readme_text


# --------------------------------------------------------------------------- #
# Python classifier
# --------------------------------------------------------------------------- #


def test_classify_python_accepts_python_with_manifest() -> None:
    report = ScanReport(python_file_count=1, detected_manifests=["pyproject.toml"])
    assert classify_python(report) == (True, "python_source_with_manifest")


def test_classify_python_accepts_multiple_python_sources() -> None:
    report = ScanReport(python_file_count=7)
    assert classify_python(report) == (True, "multiple_python_sources")


def test_classify_python_accepts_entrypoint_plus_readme_hint() -> None:
    report = ScanReport(
        python_file_count=2,
        candidate_entrypoints=["main.py"],
        readme_text="Install with `pip install foo`, then run pytest.",
    )
    assert classify_python(report) == (True, "entrypoint_plus_readme_hint")


def test_classify_python_rejects_notebook_only_repo() -> None:
    report = ScanReport(notebook_count=12)
    assert classify_python(report) == (False, "notebooks_only")


def test_classify_python_rejects_empty_repo() -> None:
    report = ScanReport()
    assert classify_python(report) == (False, "no_python_sources")


def test_classify_python_rejects_entrypoint_without_readme_hint() -> None:
    report = ScanReport(
        python_file_count=2,
        candidate_entrypoints=["main.py"],
        readme_text="Some generic prose with no install instructions.",
    )
    assert classify_python(report) == (False, "insufficient_python_signals")


# --------------------------------------------------------------------------- #
# Manifest shape
# --------------------------------------------------------------------------- #


def test_build_manifest_records_all_required_fields() -> None:
    report = ScanReport(
        scanned_paths=["README.md", "main.py"],
        stored_paths=["README.md", "main.py"],
        extension_counts={".md": 1, ".py": 1},
        excluded_directories=["node_modules"],
        skipped_files={"symlink": 1},
        candidate_entrypoints=["main.py"],
        detected_manifests=["pyproject.toml"],
        readme_paths=["README.md"],
        python_file_count=1,
        notebook_count=0,
    )
    manifest = build_manifest(
        code_url="https://github.com/owner/repo",
        ref=GitHubRepoRef("owner", "repo", None),
        resolved_sha="deadbeef",
        report=report,
        is_python=True,
        python_reason="python_source_with_manifest",
    )
    assert manifest["code_url"] == "https://github.com/owner/repo"
    assert manifest["source_kind"] == "github"
    assert manifest["owner"] == "owner"
    assert manifest["repo"] == "repo"
    assert manifest["resolved_sha"] == "deadbeef"
    assert manifest["scanned_paths"] == ["README.md", "main.py"]
    assert manifest["stored_paths"] == ["README.md", "main.py"]
    assert manifest["extension_counts"] == {".md": 1, ".py": 1}
    assert manifest["excluded_directories"] == ["node_modules"]
    assert manifest["skipped_files"] == {"symlink": 1}
    assert manifest["candidate_entrypoints"] == ["main.py"]
    assert manifest["detected_manifests"] == ["pyproject.toml"]
    assert manifest["readme_paths"] == ["README.md"]
    assert manifest["python_file_count"] == 1
    assert manifest["notebook_count"] == 0
    assert manifest["code_is_python"] is True
    assert manifest["code_python_reason"] == "python_source_with_manifest"


def test_skipped_manifest_has_same_top_level_shape_as_build_manifest() -> None:
    report = ScanReport()
    happy = build_manifest(
        code_url="https://github.com/owner/repo",
        ref=GitHubRepoRef("owner", "repo"),
        resolved_sha=None,
        report=report,
        is_python=False,
        python_reason="no_python_sources",
    )
    skipped = skipped_manifest(code_url="https://gitlab.com/x/y", reason="unsupported_host")
    # Top-level key set must match so downstream consumers can rely on a
    # single shape regardless of skip vs real ingestion.
    assert set(skipped.keys()) == set(happy.keys())
    assert skipped["source_kind"] == "unsupported"
    assert skipped["code_python_reason"] == "unsupported_host"
    assert skipped["code_is_python"] is None


# --------------------------------------------------------------------------- #
# Fetch helper (no network — injected http_get)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks: list[bytes] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self.url = url
        self.headers = headers or {}

    def iter_content(self, chunk_size: int = 65536):
        for chunk in self._chunks:
            yield chunk

    def close(self) -> None:
        pass


def test_fetch_github_archive_enforces_size_cap() -> None:
    payload = b"x" * 2048

    def http_get(url, **kwargs):
        return _FakeResponse(chunks=[payload])

    with pytest.raises(code_ingest.ArchiveTooLargeError):
        code_ingest.fetch_github_archive(
            GitHubRepoRef("owner", "repo"),
            token=None,
            max_bytes=1024,
            http_get=http_get,
        )


def test_fetch_github_archive_extracts_resolved_sha_from_redirect_url() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    redirect_url = f"https://codeload.github.com/owner/repo/archive/{sha}.tar.gz"

    def http_get(url, **kwargs):
        return _FakeResponse(chunks=[b"tarball-bytes"], url=redirect_url)

    result = code_ingest.fetch_github_archive(
        GitHubRepoRef("owner", "repo"),
        token=None,
        max_bytes=1 << 20,
        http_get=http_get,
    )
    assert result.resolved_sha == sha
    assert result.data == b"tarball-bytes"


def test_fetch_github_archive_raises_on_non_200() -> None:
    def http_get(url, **kwargs):
        return _FakeResponse(status_code=404, chunks=[], url=url)

    with pytest.raises(code_ingest.ArchiveFetchError):
        code_ingest.fetch_github_archive(
            GitHubRepoRef("owner", "repo"),
            token=None,
            max_bytes=1 << 20,
            http_get=http_get,
        )


def test_fetch_github_archive_sends_auth_header_when_token_given() -> None:
    captured: dict[str, dict[str, str]] = {}

    def http_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(chunks=[b"ok"], url=url)

    code_ingest.fetch_github_archive(
        GitHubRepoRef("owner", "repo"),
        token="gho_test",
        max_bytes=1 << 20,
        http_get=http_get,
    )
    assert captured["headers"]["Authorization"] == "Bearer gho_test"


# --------------------------------------------------------------------------- #
# Code chunk extraction (PR 2)
# --------------------------------------------------------------------------- #


def _extract_for_chunks(tmp_path: Path, entries: list[tuple[str, bytes | str | None, str]]) -> tuple[Path, ScanReport]:
    archive = _build_tarball(entries)
    dest = tmp_path / "repo"
    report = extract_archive_safely(
        archive, dest, max_stored_file_bytes=1 << 20, max_scanned_files=1000
    )
    return dest, report


def test_build_code_chunks_emits_readme_and_docs(tmp_path: Path) -> None:
    root, report = _extract_for_chunks(
        tmp_path,
        [
            ("owner-repo-abc/README.md", "# Demo\nUseful.\n", "file"),
            ("owner-repo-abc/docs/guide.md", "# Guide\nContent.\n", "file"),
            ("owner-repo-abc/src/mod.py", "x = 1\n", "file"),
        ],
    )
    chunks = code_ingest.build_code_chunks(root, report)
    by_kind: dict[str, list[code_ingest.CodeChunk]] = {}
    for c in chunks:
        by_kind.setdefault(c.chunk_kind, []).append(c)
    assert {c.repo_path for c in by_kind["code_readme"]} == {"README.md"}
    assert {c.repo_path for c in by_kind["code_doc"]} == {"docs/guide.md"}


def test_build_code_chunks_emits_manifests(tmp_path: Path) -> None:
    root, report = _extract_for_chunks(
        tmp_path,
        [
            ("owner-repo-abc/pyproject.toml", "[project]\nname='x'\n", "file"),
            ("owner-repo-abc/requirements.txt", "torch\n", "file"),
            ("owner-repo-abc/setup.py", "from setuptools import setup\n", "file"),
        ],
    )
    chunks = code_ingest.build_code_chunks(root, report)
    manifests = {c.repo_path for c in chunks if c.chunk_kind == "code_manifest"}
    assert manifests == {"pyproject.toml", "requirements.txt", "setup.py"}


def test_build_code_chunks_emits_entrypoints_and_signatures(tmp_path: Path) -> None:
    root, report = _extract_for_chunks(
        tmp_path,
        [
            (
                "owner-repo-abc/main.py",
                '"""Entry.\n\nLong module doc."""\n\n'
                "def run(x: int = 1) -> bool:\n"
                '    """Runs the demo."""\n'
                "    return True\n"
                "\n"
                "class Worker:\n"
                '    """Orchestrates."""\n'
                "    def work(self, task):\n"
                '        """Do one task."""\n'
                "        return task\n",
                "file",
            ),
            ("owner-repo-abc/scripts/train.py", "def train():\n    pass\n", "file"),
        ],
    )
    chunks = code_ingest.build_code_chunks(root, report)
    entrypoints = [c for c in chunks if c.chunk_kind == "code_entrypoint"]
    signatures = [c for c in chunks if c.chunk_kind == "code_signature"]
    assert {c.repo_path for c in entrypoints} == {"main.py", "scripts/train.py"}
    main_sig = next(c for c in signatures if c.repo_path == "main.py")
    # Signature chunk must capture: module docstring, fn signature + doc,
    # class signature + doc, method signature + doc.
    assert "def run(x: int=1) -> bool:" in main_sig.content
    assert "Runs the demo" in main_sig.content
    assert "class Worker:" in main_sig.content
    assert "Orchestrates" in main_sig.content
    assert "def work(self, task):" in main_sig.content
    assert "Do one task" in main_sig.content


def test_build_code_chunks_truncates_oversized_content(tmp_path: Path) -> None:
    huge_readme = "a" * 10_000
    root, report = _extract_for_chunks(
        tmp_path,
        [
            ("owner-repo-abc/README.md", huge_readme, "file"),
        ],
    )
    chunks = code_ingest.build_code_chunks(root, report)
    readme = next(c for c in chunks if c.chunk_kind == "code_readme")
    assert len(readme.content) < len(huge_readme)
    assert readme.content.endswith("[truncated]")


def test_build_code_chunks_skips_unparseable_python(tmp_path: Path) -> None:
    root, report = _extract_for_chunks(
        tmp_path,
        [
            ("owner-repo-abc/good.py", "def ok():\n    pass\n", "file"),
            ("owner-repo-abc/broken.py", "def not python syntax at all: [\n", "file"),
        ],
    )
    # extract_archive_safely only populates candidate_entrypoints for names
    # matching the entrypoint hint; signature chunks are produced for any
    # stored .py regardless. Confirm broken.py yields no signature chunk.
    # We also confirm good.py is unaffected.
    report.candidate_entrypoints.clear()  # isolate to the signature path
    chunks = code_ingest.build_code_chunks(root, report)
    sig_paths = {c.repo_path for c in chunks if c.chunk_kind == "code_signature"}
    assert "good.py" in sig_paths
    assert "broken.py" not in sig_paths
