"""GitHub sync and Modal execution helpers for browser code files."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from threading import Lock
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from .utils import slugify, utcnow

_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_API_VERSION = "2022-11-28"


class GitHubSyncError(RuntimeError):
    """Raised when a Paperbase-managed GitHub sync cannot complete."""


class ModalExecutionError(RuntimeError):
    """Raised when Modal execution cannot be started."""


@dataclass(slots=True)
class GitHubRepoInfo:
    repo_name: str
    repo_url: str
    default_branch: str = "main"
    owner_login: str = ""


@dataclass(slots=True)
class GitHubRepoBranch:
    name: str
    commit_sha: str = ""


@dataclass(slots=True)
class GitHubRepoFileCandidate:
    path: str
    file_kind: str
    size: int = 0


@dataclass(slots=True)
class GitHubCommitInfo:
    repo_name: str
    repo_url: str
    default_branch: str
    commit_sha: str
    commit_url: str


@dataclass(slots=True)
class GitHubConflictFile:
    path: str
    base_content: str
    local_content: str
    remote_content: str


@dataclass(slots=True)
class ModalRunResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int
    started_at: object
    finished_at: object


class GitHubSyncConflictError(GitHubSyncError):
    def __init__(
        self,
        message: str,
        *,
        repo_name: str,
        repo_url: str,
        default_branch: str,
        base_commit_sha: str,
        remote_head_sha: str,
        conflicts: list[GitHubConflictFile],
    ) -> None:
        super().__init__(message)
        self.repo_name = repo_name
        self.repo_url = repo_url
        self.default_branch = default_branch
        self.base_commit_sha = base_commit_sha
        self.remote_head_sha = remote_head_sha
        self.conflicts = conflicts


def build_project_repo_name(project_name: str, project_id: str) -> str:
    stem = slugify(project_name or "project")
    stem = (stem[:32] or "project").strip("-") or "project"
    suffix = project_id.split("_", 1)[-1][:6]
    return f"paperbase-{stem}-{suffix}"


_MODAL_ENV_LOCK = Lock()


class GitHubProjectRepoManager:
    def __init__(
        self,
        *,
        api_base_url: str = _GITHUB_API_BASE,
        api_version: str = _GITHUB_API_VERSION,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Accept": _GITHUB_ACCEPT,
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": self.api_version,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        ok_statuses: set[int],
        json_body: dict[str, object] | None = None,
    ) -> requests.Response:
        response = self._session.request(
            method,
            f"{self.api_base_url}{path}",
            headers=self._headers(access_token),
            json=json_body,
            timeout=self.timeout_seconds,
        )
        if response.status_code in ok_statuses:
            return response
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("error") or "").strip()
        except Exception:
            detail = response.text.strip()
        raise GitHubSyncError(detail or f"GitHub request failed with status {response.status_code}.")

    def get_repo(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
    ) -> GitHubRepoInfo | None:
        response = self._session.get(
            f"{self.api_base_url}/repos/{owner_login}/{repo_name}",
            headers=self._headers(access_token),
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            detail = ""
            try:
                detail = str((response.json() or {}).get("message") or "").strip()
            except Exception:
                detail = response.text.strip()
            raise GitHubSyncError(detail or "GitHub repository metadata could not be loaded.")
        payload = response.json()
        owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
        return GitHubRepoInfo(
            owner_login=str(owner.get("login") or owner_login),
            repo_name=str(payload.get("name") or repo_name),
            repo_url=str(payload.get("html_url") or ""),
            default_branch=str(payload.get("default_branch") or "main"),
        )

    def list_branches(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
    ) -> list[GitHubRepoBranch]:
        response = self._request(
            "GET",
            f"/repos/{owner_login}/{repo_name}/branches?per_page=100",
            access_token=access_token,
            ok_statuses={200},
        )
        branches = []
        for item in response.json() or []:
            commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            branches.append(
                GitHubRepoBranch(
                    name=str(item.get("name") or ""),
                    commit_sha=str(commit.get("sha") or ""),
                )
            )
        return [branch for branch in branches if branch.name]

    def get_branch_head(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
        branch: str,
    ) -> str:
        encoded_branch = quote(branch, safe="")
        response = self._request(
            "GET",
            f"/repos/{owner_login}/{repo_name}/branches/{encoded_branch}",
            access_token=access_token,
            ok_statuses={200},
        )
        payload = response.json() or {}
        commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
        return str(commit.get("sha") or "")

    def list_importable_files(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
        branch: str,
    ) -> list[GitHubRepoFileCandidate]:
        encoded_branch = quote(branch, safe="")
        response = self._request(
            "GET",
            f"/repos/{owner_login}/{repo_name}/git/trees/{encoded_branch}?recursive=1",
            access_token=access_token,
            ok_statuses={200},
        )
        candidates: list[GitHubRepoFileCandidate] = []
        for item in (response.json() or {}).get("tree") or []:
            if item.get("type") != "blob":
                continue
            path = str(item.get("path") or "")
            lower_path = path.lower()
            if lower_path.endswith(".py"):
                candidates.append(
                    GitHubRepoFileCandidate(path=path, file_kind="python", size=int(item.get("size") or 0))
                )
            elif lower_path.endswith(".ipynb"):
                candidates.append(
                    GitHubRepoFileCandidate(path=path, file_kind="notebook", size=int(item.get("size") or 0))
                )
        return candidates

    def get_file_text(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
        path: str,
        ref: str,
    ) -> str:
        encoded_path = quote(path, safe="/")
        encoded_ref = quote(ref, safe="")
        response = self._request(
            "GET",
            f"/repos/{owner_login}/{repo_name}/contents/{encoded_path}?ref={encoded_ref}",
            access_token=access_token,
            ok_statuses={200},
        )
        payload = response.json() or {}
        if payload.get("type") != "file":
            raise GitHubSyncError(f"GitHub path '{path}' is not a file.")
        raw_content = str(payload.get("content") or "")
        encoding = str(payload.get("encoding") or "")
        if encoding != "base64":
            raise GitHubSyncError(f"GitHub path '{path}' uses unsupported encoding.")
        return base64.b64decode(raw_content.encode("ascii")).decode("utf-8")

    def create_repo(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
        description: str,
    ) -> GitHubRepoInfo:
        response = self._session.post(
            f"{self.api_base_url}/user/repos",
            headers=self._headers(access_token),
            json={
                "name": repo_name,
                "description": description,
                "private": True,
                "auto_init": False,
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code == 201:
            payload = response.json()
            owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
            return GitHubRepoInfo(
                owner_login=str(owner.get("login") or owner_login),
                repo_name=str(payload.get("name") or repo_name),
                repo_url=str(payload.get("html_url") or ""),
                default_branch=str(payload.get("default_branch") or "main"),
            )
        detail = ""
        try:
            detail = str((response.json() or {}).get("message") or "").strip()
        except Exception:
            detail = response.text.strip()
        raise GitHubSyncError(detail or "Paperbase could not create the project repository on GitHub.")

    def ensure_repo(
        self,
        *,
        access_token: str,
        owner_login: str,
        project_name: str,
        project_id: str,
        description: str,
    ) -> GitHubRepoInfo:
        repo_name = build_project_repo_name(project_name, project_id)
        existing = self.get_repo(access_token=access_token, owner_login=owner_login, repo_name=repo_name)
        if existing is not None:
            return existing
        response = self._session.post(
            f"{self.api_base_url}/user/repos",
            headers=self._headers(access_token),
            json={
                "name": repo_name,
                "description": description,
                "private": True,
                "auto_init": False,
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code == 201:
            payload = response.json()
            owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
            return GitHubRepoInfo(
                owner_login=str(owner.get("login") or owner_login),
                repo_name=str(payload.get("name") or repo_name),
                repo_url=str(payload.get("html_url") or ""),
                default_branch="main",
            )
        if response.status_code == 422:
            existing = self.get_repo(
                access_token=access_token,
                owner_login=owner_login,
                repo_name=repo_name,
            )
            if existing is not None:
                return existing
        detail = ""
        try:
            detail = str((response.json() or {}).get("message") or "").strip()
        except Exception:
            detail = response.text.strip()
        raise GitHubSyncError(detail or "Paperbase could not create the project repository on GitHub.")

    def sync_project_files(
        self,
        *,
        access_token: str,
        owner_login: str,
        repo_name: str,
        repo_url: str,
        default_branch: str,
        files: dict[str, str],
        commit_message: str,
        author_name: str,
        author_email: str,
        base_commit_sha: str | None = None,
        expected_remote_head_sha: str | None = None,
        previous_managed_paths: set[str] | None = None,
    ) -> GitHubCommitInfo:
        remote_url = f"https://github.com/{owner_login}/{repo_name}.git"
        auth = base64.b64encode(f"x-access-token:{access_token}".encode("utf-8")).decode("ascii")
        auth_arg = f"http.extraHeader=Authorization: Basic {auth}"
        env = {"GIT_TERMINAL_PROMPT": "0"}

        def run_git(
            *args: str,
            cwd: str,
            use_auth: bool = False,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            command = ["git"]
            if use_auth:
                command.extend(["-c", auth_arg])
            command.extend(args)
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
                env=env,
            )
            return completed

        def git_show_file(cwd: str, revision: str, path: str) -> str:
            if not revision:
                return ""
            result = run_git("show", f"{revision}:{path}", cwd=cwd, check=False)
            if result.returncode != 0:
                return ""
            return result.stdout

        with tempfile.TemporaryDirectory(prefix="paperbase-sync-") as tempdir:
            worktree = Path(tempdir)
            run_git("init", "--initial-branch", default_branch, cwd=tempdir)
            run_git("remote", "add", "origin", remote_url, cwd=tempdir)

            remote_branch_exists = False
            remote_head_sha = ""
            remote_probe = run_git(
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                default_branch,
                cwd=tempdir,
                use_auth=True,
                check=False,
            )
            if remote_probe.returncode == 0:
                remote_branch_exists = True
            elif remote_probe.returncode not in {2}:
                raise GitHubSyncError(remote_probe.stderr.strip() or "GitHub repository state could not be checked.")

            if remote_branch_exists:
                fetch = run_git(
                    "fetch",
                    "origin",
                    default_branch,
                    cwd=tempdir,
                    use_auth=True,
                    check=False,
                )
                if fetch.returncode != 0:
                    raise GitHubSyncError(fetch.stderr.strip() or "GitHub repository could not be fetched.")
                remote_head_sha = run_git("rev-parse", "FETCH_HEAD", cwd=tempdir).stdout.strip()
                if expected_remote_head_sha and remote_head_sha != expected_remote_head_sha:
                    raise GitHubSyncError(
                        "The project repository changed again on GitHub. Refresh conflicts and try saving again."
                    )
                effective_base_commit = remote_head_sha
                if base_commit_sha:
                    base_exists = run_git(
                        "rev-parse",
                        "--verify",
                        f"{base_commit_sha}^{{commit}}",
                        cwd=tempdir,
                        check=False,
                    )
                    if base_exists.returncode != 0:
                        raise GitHubSyncError(
                            "The project repository history changed on GitHub. Refresh and try saving again."
                        )
                    base_is_ancestor = run_git(
                        "merge-base",
                        "--is-ancestor",
                        base_commit_sha,
                        "FETCH_HEAD",
                        cwd=tempdir,
                        check=False,
                    )
                    if base_is_ancestor.returncode != 0 and base_commit_sha != remote_head_sha:
                        raise GitHubSyncError(
                            "The project repository history changed on GitHub. Refresh and try saving again."
                        )
                    effective_base_commit = base_commit_sha
                checkout = run_git(
                    "checkout",
                    "-B",
                    "paperbase-local",
                    effective_base_commit,
                    cwd=tempdir,
                    check=False,
                )
                if checkout.returncode != 0:
                    raise GitHubSyncError(
                        checkout.stderr.strip() or "GitHub repository checkout could not be prepared."
                    )
            else:
                run_git("checkout", "-B", "paperbase-local", cwd=tempdir)
                effective_base_commit = base_commit_sha or ""

            previous_managed_paths = previous_managed_paths or set()
            for relative_path in sorted(previous_managed_paths - set(files)):
                if not relative_path or relative_path == ".git" or ".." in Path(relative_path).parts:
                    continue
                target = worktree / relative_path
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                except FileNotFoundError:
                    continue

            for relative_path, content in sorted(files.items()):
                target = worktree / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            run_git("config", "user.name", author_name, cwd=tempdir)
            run_git("config", "user.email", author_email, cwd=tempdir)
            run_git("add", "-A", cwd=tempdir)
            status = run_git("status", "--porcelain", cwd=tempdir)
            if not status.stdout.strip():
                current_sha = run_git("rev-parse", "HEAD", cwd=tempdir).stdout.strip()
                return GitHubCommitInfo(
                    repo_name=repo_name,
                    repo_url=repo_url,
                    default_branch=default_branch,
                    commit_sha=current_sha,
                    commit_url=f"{repo_url}/commit/{current_sha}",
                )

            commit = run_git("commit", "-m", commit_message, cwd=tempdir, check=False)
            if commit.returncode != 0:
                raise GitHubSyncError(commit.stderr.strip() or "GitHub commit could not be created.")

            if remote_branch_exists and remote_head_sha and effective_base_commit != remote_head_sha:
                checkout_remote = run_git(
                    "checkout",
                    "-B",
                    default_branch,
                    "FETCH_HEAD",
                    cwd=tempdir,
                    check=False,
                )
                if checkout_remote.returncode != 0:
                    raise GitHubSyncError(
                        checkout_remote.stderr.strip() or "GitHub repository checkout could not be prepared."
                    )
                merge = run_git(
                    "merge",
                    "--no-commit",
                    "--no-ff",
                    "paperbase-local",
                    cwd=tempdir,
                    check=False,
                )
                if merge.returncode != 0:
                    conflict_paths = [
                        line.strip()
                        for line in run_git(
                            "diff",
                            "--name-only",
                            "--diff-filter=U",
                            cwd=tempdir,
                        ).stdout.splitlines()
                        if line.strip()
                    ]
                    auto_resolve_paths = {".paperbase", "notes/.gitkeep"}
                    for path in conflict_paths:
                        if path in auto_resolve_paths and path in files:
                            target = worktree / path
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(files[path], encoding="utf-8")
                            run_git("add", "--", path, cwd=tempdir)
                    remaining_conflicts = [
                        line.strip()
                        for line in run_git(
                            "diff",
                            "--name-only",
                            "--diff-filter=U",
                            cwd=tempdir,
                        ).stdout.splitlines()
                        if line.strip()
                    ]
                    if remaining_conflicts:
                        raise GitHubSyncConflictError(
                            "The project repository changed on GitHub and needs conflict resolution.",
                            repo_name=repo_name,
                            repo_url=repo_url,
                            default_branch=default_branch,
                            base_commit_sha=effective_base_commit,
                            remote_head_sha=remote_head_sha,
                            conflicts=[
                                GitHubConflictFile(
                                    path=path,
                                    base_content=git_show_file(tempdir, effective_base_commit, path),
                                    local_content=files.get(path, ""),
                                    remote_content=git_show_file(tempdir, remote_head_sha, path),
                                )
                                for path in remaining_conflicts
                            ],
                        )
                merge_commit = run_git("commit", "-m", commit_message, cwd=tempdir, check=False)
                if merge_commit.returncode != 0:
                    raise GitHubSyncError(merge_commit.stderr.strip() or "GitHub merge commit could not be created.")
            else:
                checkout_local = run_git("checkout", "paperbase-local", cwd=tempdir, check=False)
                if checkout_local.returncode != 0:
                    raise GitHubSyncError(
                        checkout_local.stderr.strip() or "GitHub repository checkout could not be prepared."
                    )

            push = run_git(
                "push",
                "-u",
                "origin",
                f"HEAD:{default_branch}",
                cwd=tempdir,
                use_auth=True,
                check=False,
            )
            if push.returncode != 0:
                detail = push.stderr.strip() or push.stdout.strip()
                if "non-fast-forward" in detail or "[rejected]" in detail:
                    raise GitHubSyncError(
                        "The project repository changed again on GitHub. Refresh conflicts and try again."
                    )
                raise GitHubSyncError(detail or "The project repository could not be pushed to GitHub.")

            commit_sha = run_git("rev-parse", "HEAD", cwd=tempdir).stdout.strip()
            return GitHubCommitInfo(
                repo_name=repo_name,
                repo_url=repo_url,
                default_branch=default_branch,
                commit_sha=commit_sha,
                commit_url=f"{repo_url}/commit/{commit_sha}",
            )


class ModalPythonRunner:
    def __init__(
        self,
        *,
        app_name: str = "paperbase-browser-sandbox",
        python_version: str = "3.11",
        timeout_seconds: int = 300,
        idle_timeout_seconds: int = 30,
    ) -> None:
        self.app_name = app_name
        self.python_version = python_version
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def run(self, *, token_id: str, token_secret: str, code: str) -> ModalRunResult:
        started_at = utcnow()
        try:
            import modal
        except ImportError as exc:
            raise ModalExecutionError("Modal SDK is not installed. Run setup again before using the sandbox.") from exc

        sandbox = None
        client = None
        try:
            client_factory = getattr(getattr(modal, "Client", None), "from_credentials", None)
            if client_factory is not None:
                try:
                    client = client_factory(token_id=token_id, token_secret=token_secret)
                except TypeError:
                    client = client_factory(token_id, token_secret)

            image = modal.Image.debian_slim(python_version=self.python_version)
            if client is not None:
                app = modal.App.lookup(self.app_name, create_if_missing=True, client=client)
                sandbox = modal.Sandbox.create(
                    app=app,
                    image=image,
                    timeout=self.timeout_seconds,
                    idle_timeout=self.idle_timeout_seconds,
                    client=client,
                )
            else:
                with _MODAL_ENV_LOCK:
                    previous_token_id = os.environ.get("MODAL_TOKEN_ID")
                    previous_token_secret = os.environ.get("MODAL_TOKEN_SECRET")
                    os.environ["MODAL_TOKEN_ID"] = token_id
                    os.environ["MODAL_TOKEN_SECRET"] = token_secret
                    try:
                        app = modal.App.lookup(self.app_name, create_if_missing=True)
                        sandbox = modal.Sandbox.create(
                            app=app,
                            image=image,
                            timeout=self.timeout_seconds,
                            idle_timeout=self.idle_timeout_seconds,
                        )
                    finally:
                        if previous_token_id is None:
                            os.environ.pop("MODAL_TOKEN_ID", None)
                        else:
                            os.environ["MODAL_TOKEN_ID"] = previous_token_id
                        if previous_token_secret is None:
                            os.environ.pop("MODAL_TOKEN_SECRET", None)
                        else:
                            os.environ["MODAL_TOKEN_SECRET"] = previous_token_secret

            process = sandbox.exec("python", "-c", code, timeout=self.timeout_seconds)
            exit_code = int(process.wait() or 0)
            stdout = str(process.stdout.read() or "")
            stderr = str(process.stderr.read() or "")
            finished_at = utcnow()
            return ModalRunResult(
                status="completed" if exit_code == 0 else "failed",
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            finished_at = utcnow()
            return ModalRunResult(
                status="failed",
                stdout="",
                stderr=str(exc),
                exit_code=1,
                started_at=started_at,
                finished_at=finished_at,
            )
        finally:
            if sandbox is not None:
                try:
                    sandbox.terminate()
                except Exception:
                    pass
                try:
                    sandbox.detach()
                except Exception:
                    pass
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
