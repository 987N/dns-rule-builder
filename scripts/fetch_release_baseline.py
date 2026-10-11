#!/usr/bin/env python3
"""Fetch the latest verified published rules release into a clean build tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from dns_rule_builder.controller import MAX_RELEASE_BYTES, validate_release_zip
from dns_rule_builder.engine import BuildError, verify_manifest
from dns_rule_builder.github import GitHubClient, GitHubError


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SHA256SUMS_BYTES = 1024 * 1024
RELEASE_TAG_RE = re.compile(r"^rules-([0-9a-f]{64})$")
REQUIRED_ASSET_LIMITS = {
    "rules.zip": MAX_RELEASE_BYTES,
    "manifest.json": MAX_MANIFEST_BYTES,
    "SHA256SUMS": MAX_SHA256SUMS_BYTES,
}
HASHED_ASSETS = {"rules.zip", "manifest.json"}


def _build_purpose(release: dict) -> str | None:
    body = release.get("body")
    if not isinstance(body, str):
        return None
    matches = re.findall(r"(?m)^build_purpose=(rules|smoke)\s*$", body)
    return matches[0] if len(matches) == 1 else None


def _release_is_published(release: dict) -> bool:
    if not isinstance(release.get("draft"), bool) or not isinstance(release.get("prerelease"), bool):
        raise BuildError("GitHub release metadata has invalid draft/prerelease flags")
    return release.get("draft") is False and release.get("prerelease") is False


def _list_releases(client: GitHubClient) -> list[dict]:
    releases: list[dict] = []
    for page in range(1, 1001):
        current = client._release_page(page)
        releases.extend(current)
        if len(current) < 100:
            return releases
    raise GitHubError(0, "GitHub release history exceeds the safe lookup limit")


def _select_release(client: GitHubClient) -> dict | None:
    latest = None
    try:
        latest = client._json("GET", f"{client.repo_path}/releases/latest")
    except GitHubError as exc:
        if exc.status != 404:
            raise

    if latest is not None:
        if not _release_is_published(latest):
            raise BuildError("GitHub latest endpoint returned a draft or prerelease")
        if _build_purpose(latest) == "smoke":
            latest = None
        elif _build_purpose(latest) != "rules":
            raise BuildError("GitHub latest release has no unambiguous rules build_purpose")
        else:
            return latest

    for release in _list_releases(client):
        if not isinstance(release.get("tag_name"), str) or not RELEASE_TAG_RE.fullmatch(release["tag_name"]):
            continue
        if not _release_is_published(release):
            continue
        purpose = _build_purpose(release)
        if purpose == "smoke":
            continue
        if purpose != "rules":
            raise BuildError("Published rules release has no unambiguous build_purpose")
        return release
    return None


def _required_assets(release: dict) -> dict[str, dict]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise BuildError("Published rules release has no asset list")
    selected: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        name = asset["name"]
        if name not in REQUIRED_ASSET_LIMITS:
            continue
        if name in selected:
            raise BuildError(f"Published rules release has duplicate {name} assets")
        selected[name] = asset
    if set(selected) != set(REQUIRED_ASSET_LIMITS):
        raise BuildError("Published rules release is missing a mandatory asset")
    for name, asset in selected.items():
        size = asset.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= REQUIRED_ASSET_LIMITS[name]:
            raise BuildError(f"Published rules release has an invalid {name} size")
        if not isinstance(asset.get("browser_download_url"), str):
            raise BuildError(f"Published rules release has no download URL for {name}")
    return selected


def _parse_sha256sums(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BuildError("SHA256SUMS must be ASCII") from exc
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match:
            raise BuildError("SHA256SUMS contains a malformed entry")
        digest, name = match.groups()
        if name in entries:
            raise BuildError(f"SHA256SUMS contains duplicate entry: {name}")
        entries[name] = digest
    if not HASHED_ASSETS.issubset(entries):
        raise BuildError("SHA256SUMS does not cover all mandatory payload assets")
    return entries


def _download_release(client: GitHubClient, release: dict) -> dict[str, bytes]:
    assets = _required_assets(release)
    payloads = {
        name: client.download_public_release_asset(asset["browser_download_url"], REQUIRED_ASSET_LIMITS[name])
        for name, asset in assets.items()
    }
    for name, payload in payloads.items():
        if not payload or len(payload) > REQUIRED_ASSET_LIMITS[name] or len(payload) != assets[name]["size"]:
            raise BuildError(f"Downloaded {name} size does not match release metadata")
    sums = _parse_sha256sums(payloads["SHA256SUMS"])
    for name in HASHED_ASSETS:
        if hashlib.sha256(payloads[name]).hexdigest() != sums[name]:
            raise BuildError(f"Downloaded {name} hash does not match SHA256SUMS")
    return payloads


def _validated_release(payloads: dict[str, bytes], tag: str, temp_root: Path) -> tuple[Path, dict]:
    try:
        external_manifest = json.loads(payloads["manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("Published release manifest is invalid JSON") from exc
    if not isinstance(external_manifest, dict):
        raise BuildError("Published release manifest is not an object")

    extraction = temp_root / "extract"
    release_dir, manifest = validate_release_zip(payloads["rules.zip"], extraction)
    internal_manifest = (release_dir / "manifest.json").read_bytes()
    if internal_manifest != payloads["manifest.json"]:
        raise BuildError("External manifest bytes do not match the rules.zip manifest")
    tag_match = RELEASE_TAG_RE.fullmatch(tag)
    if not tag_match or manifest.get("release_id") != tag_match.group(1):
        raise BuildError("Published release tag does not match manifest identity")
    if external_manifest != manifest:
        raise BuildError("External manifest differs from the verified rules manifest")
    if manifest.get("build_purpose") != "rules":
        raise BuildError("Published release is not a formal rules build")
    if manifest.get("review_required") is not False or manifest.get("publish_allowed") is not True:
        raise BuildError("Published release manifest does not satisfy publication review gates")
    return release_dir, manifest


def _assert_formal_manifest(manifest: dict, release_id: str) -> None:
    if manifest.get("release_id") != release_id or manifest.get("build_purpose") != "rules":
        raise BuildError("Baseline release directory identity or purpose is invalid")
    if manifest.get("review_required") is not False or manifest.get("publish_allowed") is not True:
        raise BuildError("Baseline release directory does not satisfy publication review gates")


def fetch_release_baseline(client: GitHubClient, output_root: Path) -> dict | None:
    output_root = output_root.absolute()
    if output_root.is_symlink():
        raise BuildError("Build output root must not be a symlink")
    if (output_root / "current").exists() or (output_root / "current").is_symlink():
        raise BuildError("Refusing to replace an existing build current pointer")

    release = _select_release(client)
    if release is None:
        return None
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not RELEASE_TAG_RE.fullmatch(tag):
        raise BuildError("Published rules release has an invalid tag")
    release_id = RELEASE_TAG_RE.fullmatch(tag).group(1)
    payloads = _download_release(client, release)

    output_root.mkdir(parents=True, exist_ok=True)
    releases_root = output_root / "releases"
    if releases_root.is_symlink():
        raise BuildError("Build releases path must not be a symlink")
    releases_root.mkdir(exist_ok=True)
    if not releases_root.is_dir():
        raise BuildError("Build releases path is not a directory")
    final = releases_root / release_id
    installed = False
    with tempfile.TemporaryDirectory(prefix="dns-rules-baseline-") as tmp:
        temp_root = Path(tmp)
        validated_dir, manifest = _validated_release(payloads, tag, temp_root)
        if final.exists() or final.is_symlink():
            if final.is_symlink() or not final.is_dir():
                raise BuildError("Existing baseline release path is not a real directory")
            existing_manifest = verify_manifest(final)
            _assert_formal_manifest(existing_manifest, release_id)
            if (final / "manifest.json").read_bytes() != payloads["manifest.json"]:
                raise BuildError("Existing baseline release manifest differs from published asset")
        else:
            stage_parent = Path(tempfile.mkdtemp(prefix=".baseline-stage-", dir=output_root))
            staging = stage_parent / release_id
            try:
                shutil.copytree(validated_dir, staging)
                verify_manifest(staging)
                _assert_formal_manifest(manifest, release_id)
                if final.exists() or final.is_symlink():
                    raise BuildError("Refusing to replace an existing baseline release directory")
                os.replace(staging, final)
                installed = True
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(stage_parent, ignore_errors=True)

    try:
        # Creating the final pointer directly is atomic and refuses an existing
        # path, including a dangling symlink.
        (output_root / "current").symlink_to(final.relative_to(output_root), target_is_directory=True)
    except BaseException:
        if installed:
            shutil.rmtree(final, ignore_errors=True)
        raise
    return {"release_id": release_id, "path": str(final), "build_purpose": "rules"}


def main() -> int:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo or "/" in repo:
        raise BuildError("GITHUB_REPOSITORY must be owner/repository")
    output_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/build")
    client = GitHubClient(owner, repo, token)
    result = fetch_release_baseline(client, output_root)
    if result is None:
        print(json.dumps({"baseline": None, "reason": "no published rules release exists"}))
    else:
        print(json.dumps({"baseline": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, GitHubError, KeyError, ValueError, OSError) as exc:
        print(f"fetch-release-baseline: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
