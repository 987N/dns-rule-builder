"""Small, credential-safe GitHub REST client for the NAS controller and CI."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ConcurrentUpdate(GitHubError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def _urlopen_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return Response(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers.items()), exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GitHubError(0, f"GitHub request failed: {type(exc).__name__}") from exc


class GitHubClient:
    """GitHub REST v3 client; token is never included in errors or repr output."""

    def __init__(self, owner: str, repository: str, token: str, transport: Transport | None = None, api_url: str = "https://api.github.com"):
        if not owner or not repository or not token.strip():
            raise ValueError("owner, repository, and token are required")
        self.owner = owner
        self.repository = repository
        self.__token = token.strip()
        if api_url.rstrip("/") != "https://api.github.com":
            raise ValueError("GitHub credential-bearing API calls must use https://api.github.com")
        self.api_url = "https://api.github.com"
        self.transport = transport or _urlopen_transport

    @property
    def repo_path(self) -> str:
        return f"/repos/{urllib.parse.quote(self.owner, safe='')}/{urllib.parse.quote(self.repository, safe='')}"

    def _request(self, method: str, path: str, payload: dict | None = None, accepted: tuple[int, ...] = (200,)) -> Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.__token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dns-rule-builder/0.1",
        }
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.transport(method, self.api_url + path, headers, body)
        if response.status not in accepted:
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            error = f"GitHub API failed (HTTP {response.status})"
            if response.status in (409, 422):
                raise ConcurrentUpdate(response.status, error, retry_after)
            raise GitHubError(response.status, error, retry_after)
        return response

    def _json(self, method: str, path: str, payload: dict | None = None, accepted: tuple[int, ...] = (200,)) -> dict:
        response = self._request(method, path, payload, accepted)
        if not response.body:
            return {}
        try:
            value = json.loads(response.body)
        except ValueError as exc:
            raise GitHubError(response.status, "GitHub API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise GitHubError(response.status, "GitHub API returned an unexpected JSON value")
        return value

    def get_contents(self, path: str, ref: str) -> tuple[bytes, str]:
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("repository content path must be relative and safe")
        query = urllib.parse.urlencode({"ref": ref})
        result = self._json("GET", f"{self.repo_path}/contents/{urllib.parse.quote(path, safe='/')}?{query}")
        if result.get("type") != "file" or not isinstance(result.get("content"), str) or not isinstance(result.get("sha"), str):
            raise GitHubError(200, "GitHub content response is not a regular file")
        try:
            return base64.b64decode(result["content"], validate=False), result["sha"]
        except ValueError as exc:
            raise GitHubError(200, "GitHub content response has invalid base64") from exc

    def update_contents(self, path: str, content: bytes, branch: str, expected_sha: str | None, message: str) -> str:
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("repository content path must be relative and safe")
        if expected_sha is not None and (len(expected_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_sha)):
            raise ValueError("expected content SHA must be a full Git blob SHA")
        payload = {"message": message, "content": base64.b64encode(content).decode("ascii"), "branch": branch}
        if expected_sha is not None:
            payload["sha"] = expected_sha
        result = self._json("PUT", f"{self.repo_path}/contents/{urllib.parse.quote(path, safe='/')}", payload, accepted=(200, 201))
        commit = result.get("commit", {}).get("sha")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise GitHubError(200, "GitHub content update response omitted its commit SHA")
        return commit

    def verify_commit_reachable(self, branch: str, commit: str) -> bool:
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            return False
        path = f"{self.repo_path}/compare/{urllib.parse.quote(branch, safe='')}...{commit}"
        comparison = self._json("GET", path)
        return comparison.get("status") in {"identical", "behind"}

    def dispatch_workflow(self, workflow: str, ref: str, data_commit: str, request_id: str, smoke_test: bool = False) -> int:
        if not workflow.endswith((".yml", ".yaml")) or "/" in workflow or ".." in workflow:
            raise ValueError("workflow must be a workflow filename")
        if ref != "main" or not re.fullmatch(r"[0-9a-f]{40}", data_commit):
            raise ValueError("dispatch requires main and a full lowercase data commit SHA")
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a UUID") from exc
        if not isinstance(smoke_test, bool):
            raise ValueError("smoke_test must be a boolean")
        payload = {"ref": ref, "inputs": {"data_commit": data_commit, "request_id": request_id, "smoke_test": str(smoke_test).lower()}}
        response = self._request("POST", f"{self.repo_path}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/dispatches", payload, accepted=(200, 204))
        return response.status

    def find_dispatch_run(self, request_id: str, branch: str) -> dict | None:
        query = urllib.parse.urlencode({"event": "workflow_dispatch", "branch": branch, "per_page": 100})
        result = self._json("GET", f"{self.repo_path}/actions/runs?{query}")
        runs = result.get("workflow_runs", [])
        for run in runs:
            if isinstance(run, dict) and (run.get("display_title") == request_id or request_id in str(run.get("display_title", ""))):
                return run
        return None

    def get_branch_head(self, branch: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or ".." in branch:
            raise ValueError("invalid branch name")
        result = self._json("GET", f"{self.repo_path}/commits/{urllib.parse.quote(branch, safe='')}")
        sha = result.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitHubError(200, "GitHub branch response omitted a full commit SHA")
        return sha

    def get_workflow_run(self, run_id: int) -> dict:
        if not isinstance(run_id, int) or run_id <= 0:
            raise ValueError("invalid workflow run ID")
        return self._json("GET", f"{self.repo_path}/actions/runs/{run_id}")

    def get_release(self, tag: str) -> dict:
        if not re.fullmatch(r"rules-[0-9a-f]{64}", tag):
            raise ValueError("invalid rules release tag")
        return self._json("GET", f"{self.repo_path}/releases/tags/{urllib.parse.quote(tag, safe='')}")

    def list_releases(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("release list limit must be between 1 and 100")
        response = self._request("GET", f"{self.repo_path}/releases?per_page={limit}")
        try:
            result = json.loads(response.body)
        except ValueError as exc:
            raise GitHubError(response.status, "GitHub release list returned invalid JSON") from exc
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise GitHubError(response.status, "GitHub release list returned an unexpected value")
        return result

    def download_public_release_asset(self, url: str, maximum_bytes: int = 128 * 1024 * 1024) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        expected = f"/{self.owner}/{self.repository}/releases/download/"
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(expected) or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
            raise ValueError("release asset URL is outside this public GitHub repository")
        request = urllib.request.Request(url, headers={"User-Agent": "dns-rule-builder/0.1", "Accept": "application/octet-stream"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > maximum_bytes:
                    raise GitHubError(413, "release asset exceeds configured size limit")
                payload = bytearray()
                while chunk := response.read(1024 * 1024):
                    payload.extend(chunk)
                    if len(payload) > maximum_bytes:
                        raise GitHubError(413, "release asset exceeds configured size limit")
                return bytes(payload)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise GitHubError(0, f"release download failed: {type(exc).__name__}") from exc

    def create_or_get_draft_release(self, tag: str, name: str, body: str) -> dict:
        if not re.fullmatch(r"rules-[0-9a-f]{64}", tag):
            raise ValueError("release tags must be rules- followed by a full release identity")
        try:
            return self._json("POST", f"{self.repo_path}/releases", {"tag_name": tag, "name": name, "body": body, "draft": True, "prerelease": False, "make_latest": "false"}, accepted=(201,))
        except ConcurrentUpdate as exc:
            if exc.status != 422:
                raise
        release = self._json("GET", f"{self.repo_path}/releases/tags/{urllib.parse.quote(tag, safe='')}")
        if release.get("tag_name") != tag:
            raise GitHubError(200, "release tag lookup returned a different tag")
        return release

    def upload_release_asset(self, upload_url: str, path: Path) -> dict:
        url = upload_url.split("{", 1)[0]
        parsed_upload_url = urllib.parse.urlsplit(url)
        expected_path = f"/repos/{self.owner}/{self.repository}/releases/"
        if parsed_upload_url.scheme != "https" or parsed_upload_url.netloc != "uploads.github.com" or parsed_upload_url.username or parsed_upload_url.password or parsed_upload_url.port or parsed_upload_url.query or parsed_upload_url.fragment or not parsed_upload_url.path.startswith(expected_path):
            raise ValueError("release asset upload URL is not the expected GitHub upload endpoint for this repository")
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode({"name": path.name})
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self.transport("POST", url, {"Authorization": f"Bearer {self.__token}", "Accept": "application/vnd.github+json", "Content-Type": content_type, "User-Agent": "dns-rule-builder/0.1"}, body)
        if response.status != 201:
            raise GitHubError(response.status, f"GitHub release asset upload failed with HTTP {response.status}")
        try:
            result = json.loads(response.body)
        except ValueError as exc:
            raise GitHubError(response.status, "GitHub asset upload returned invalid JSON") from exc
        if not isinstance(result, dict) or result.get("name") != path.name or result.get("state") != "uploaded":
            raise GitHubError(response.status, "GitHub asset upload did not finish successfully")
        return result

    def ensure_release_asset(self, release: dict, upload_url: str, path: Path) -> dict:
        local_digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for asset in release.get("assets", []):
            if not isinstance(asset, dict) or asset.get("name") != path.name:
                continue
            if asset.get("digest") != local_digest or asset.get("state") != "uploaded":
                raise GitHubError(409, f"existing draft asset {path.name} does not match this immutable release")
            return asset
        return self.upload_release_asset(upload_url, path)

    def publish_release(self, release_id: int, release_tag: str, data_branch: str, data_commit: str, assets: list[str], code_branch: str = "main", code_commit: str | None = None, make_latest: bool = True) -> dict:
        if not self.verify_commit_reachable(data_branch, data_commit):
            raise GitHubError(409, "release data commit is no longer reachable from rules-data")
        if make_latest:
            head = self._json("GET", f"{self.repo_path}/commits/{urllib.parse.quote(data_branch, safe='')}").get("sha")
            if head != data_commit:
                raise GitHubError(409, "stale data snapshot cannot become the latest published rules")
        if code_commit is not None:
            if not self.verify_commit_reachable(code_branch, code_commit):
                raise GitHubError(409, "release code commit is no longer reachable from main")
            if make_latest:
                code_head = self._json("GET", f"{self.repo_path}/commits/{urllib.parse.quote(code_branch, safe='')}").get("sha")
                if code_head != code_commit:
                    raise GitHubError(409, "stale code snapshot cannot become the latest published rules")
        release = self._json("GET", f"{self.repo_path}/releases/{release_id}")
        if release.get("tag_name") != release_tag:
            raise GitHubError(409, "release ID does not match the expected immutable tag")
        uploaded = {item.get("name") for item in release.get("assets", []) if isinstance(item, dict) and item.get("state") == "uploaded"}
        if not set(assets).issubset(uploaded):
            raise GitHubError(409, "draft release is missing one or more required assets")
        if not release.get("draft"):
            return release
        return self._json("PATCH", f"{self.repo_path}/releases/{release_id}", {"draft": False, "make_latest": "true" if make_latest else "false"})
