"""GitHub sync and Daytona execution helpers for browser code files."""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .utils import slugify, utcnow

_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_API_VERSION = "2022-11-28"


class GitHubSyncError(RuntimeError):
    """Raised when a Paperbase-managed GitHub sync cannot complete."""


class DaytonaExecutionError(RuntimeError):
    """Raised when Daytona execution cannot be started."""


@dataclass(slots=True)
class GitHubRepoInfo:
    repo_name: str
    repo_url: str
    default_branch: str = "main"


@dataclass(slots=True)
class GitHubCommitInfo:
    repo_name: str
    repo_url: str
    default_branch: str
    commit_sha: str
    commit_url: str


@dataclass(slots=True)
class DaytonaRunResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int
    started_at: object
    finished_at: object


def build_project_repo_name(project_name: str, project_id: str) -> str:
    stem = slugify(project_name or "project")
    stem = (stem[:32] or "project").strip("-") or "project"
    suffix = project_id.split("_", 1)[-1][:6]
    return f"paperbase-{stem}-{suffix}"


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
        return GitHubRepoInfo(
            repo_name=str(payload.get("name") or repo_name),
            repo_url=str(payload.get("html_url") or ""),
            default_branch=str(payload.get("default_branch") or "main"),
        )

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
            return GitHubRepoInfo(
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

        with tempfile.TemporaryDirectory(prefix="paperbase-sync-") as tempdir:
            worktree = Path(tempdir)
            run_git("init", "--initial-branch", default_branch, cwd=tempdir)
            run_git("remote", "add", "origin", remote_url, cwd=tempdir)

            remote_branch_exists = False
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
                raise GitHubSyncError(
                    remote_probe.stderr.strip() or "GitHub repository state could not be checked."
                )

            if remote_branch_exists:
                fetch = run_git(
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    default_branch,
                    cwd=tempdir,
                    use_auth=True,
                    check=False,
                )
                if fetch.returncode != 0:
                    raise GitHubSyncError(fetch.stderr.strip() or "GitHub repository could not be fetched.")
                checkout = run_git(
                    "checkout",
                    "-B",
                    default_branch,
                    "FETCH_HEAD",
                    cwd=tempdir,
                    check=False,
                )
                if checkout.returncode != 0:
                    raise GitHubSyncError(
                        checkout.stderr.strip() or "GitHub repository checkout could not be prepared."
                    )

            for child in worktree.iterdir():
                if child.name == ".git":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

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

            push = run_git("push", "-u", "origin", default_branch, cwd=tempdir, use_auth=True, check=False)
            if push.returncode != 0:
                detail = push.stderr.strip() or push.stdout.strip()
                if "non-fast-forward" in detail or "[rejected]" in detail:
                    raise GitHubSyncError(
                        "The project repository changed on GitHub. Refresh and try saving again."
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


class DaytonaPythonRunner:
    def run(self, *, api_key: str, code: str) -> DaytonaRunResult:
        started_at = utcnow()
        try:
            from daytona import Daytona, DaytonaConfig
        except ImportError as exc:
            raise DaytonaExecutionError(
                "Daytona SDK is not installed. Run setup again before using the sandbox."
            ) from exc

        sandbox = None
        try:
            daytona = Daytona(DaytonaConfig(api_key=api_key))
            sandbox = daytona.create()
            response = sandbox.process.code_run(code)
            exit_code = int(getattr(response, "exit_code", 0) or 0)
            result_text = str(getattr(response, "result", "") or "")
            finished_at = utcnow()
            return DaytonaRunResult(
                status="completed" if exit_code == 0 else "failed",
                stdout=result_text if exit_code == 0 else "",
                stderr=result_text if exit_code != 0 else "",
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            finished_at = utcnow()
            return DaytonaRunResult(
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
                    sandbox.delete()
                except Exception:
                    pass
