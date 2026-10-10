"""Resolve and safely cache a full upstream GitHub source snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from .engine import BuildError


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024


def _get(url: str, limit: int, github_token: str | None = None) -> bytes:
    headers = {"User-Agent": "dns-rule-builder/0.1", "Accept": "application/vnd.github+json"}
    if github_token:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com" or parsed.port not in (None, 443):
            raise BuildError("GitHub token may only be sent to https://api.github.com")
        headers["Authorization"] = f"Bearer {github_token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise BuildError("upstream response exceeds configured size limit")
            result = bytearray()
            while chunk := response.read(1024 * 1024):
                result.extend(chunk)
                if len(result) > limit:
                    raise BuildError("upstream response exceeds configured size limit")
            return bytes(result)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BuildError(f"upstream download failed: {type(exc).__name__}") from exc


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    root_name = None
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or not path.parts:
                    raise BuildError(f"unsafe path in upstream archive: {member.name!r}")
                if root_name is None:
                    root_name = path.parts[0]
                if path.parts[0] != root_name:
                    raise BuildError("upstream archive contains multiple root directories")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise BuildError(f"unsupported link or special file in archive: {member.name!r}")
                total += member.size
                if total > MAX_EXTRACTED_BYTES:
                    raise BuildError("upstream archive expands beyond configured size limit")
                target = destination.joinpath(*path.parts)
                resolved = target.resolve()
                if not resolved.is_relative_to(destination.resolve()):
                    raise BuildError(f"archive path escapes extraction directory: {member.name!r}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        raise BuildError(f"cannot read archive file: {member.name!r}")
                    with target.open("wb") as stream:
                        while chunk := extracted.read(1024 * 1024):
                            stream.write(chunk)
    except (OSError, tarfile.TarError) as exc:
        raise BuildError(f"invalid upstream archive: {type(exc).__name__}") from exc
    if not root_name:
        raise BuildError("upstream archive is empty")
    return destination / root_name


def fetch_master_snapshot(cache_root: Path, repository: str = "blackmatrix7/ios_rule_script", github_token: str | None = None) -> tuple[str, str, Path, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise BuildError("invalid upstream repository name")
    commit_url = f"https://api.github.com/repos/{repository}/commits/master"
    commit_data = json.loads(_get(commit_url, 2 * 1024 * 1024, github_token))
    revision = commit_data.get("sha") if isinstance(commit_data, dict) else None
    source_date = commit_data.get("commit", {}).get("committer", {}).get("date") if isinstance(commit_data, dict) else None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise BuildError("upstream master did not resolve to a full commit SHA")
    if not isinstance(source_date, str) or not source_date.endswith("Z"):
        raise BuildError("upstream commit response omitted its UTC committer date")

    cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    archive = cache_root / f"ios_rule_script-{revision}.tar.gz"
    extracted = cache_root / f"ios_rule_script-{revision}"
    digest_path = cache_root / f"ios_rule_script-{revision}.sha256"
    if not archive.is_file():
        payload = _get(f"https://codeload.github.com/{repository}/tar.gz/{revision}", MAX_ARCHIVE_BYTES)
        temporary = archive.with_suffix(".download")
        temporary.write_bytes(payload)
        os.replace(temporary, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not extracted.is_dir() or not (extracted / ".source-revision").is_file() or (extracted / ".source-revision").read_text().strip() != revision:
        temp_dir = cache_root / f".extract-{revision}"
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir)
        source_root = _safe_extract(archive, temp_dir)
        (source_root / ".source-revision").write_text(revision + "\n", encoding="ascii")
        if extracted.exists():
            import shutil
            shutil.rmtree(extracted)
        os.replace(source_root, extracted)
        temp_dir.rmdir()
    digest_path.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return revision, digest, extracted, source_date
