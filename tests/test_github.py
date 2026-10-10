from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dns_rule_builder.github import ConcurrentUpdate, GitHubClient, GitHubError, Response


SHA = "a" * 40
RELEASE_SHA = "b" * 64


def response(status: int, value: object | None = None, headers: dict[str, str] | None = None) -> Response:
    return Response(status, headers or {}, json.dumps(value).encode() if value is not None else b"")


def test_contents_update_is_sha_conditional_and_conflict_is_actionable():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, json.loads(body) if body else None))
        return response(409, {"message": "sha conflict"})

    client = GitHubClient("owner", "repo", "secret-token", transport)
    with pytest.raises(ConcurrentUpdate) as error:
        client.update_contents("rules-data/state.yaml", b"schema_version: 1\n", "rules-data", "c" * 40, "update")
    assert error.value.status == 409
    assert "secret-token" not in str(error.value)
    assert calls[0][2]["sha"] == "c" * 40
    assert calls[0][2]["branch"] == "rules-data"


@pytest.mark.parametrize("status", [200, 204])
def test_workflow_dispatch_accepts_200_and_204_with_correlation_id(status: int):
    seen = {}

    def transport(method, url, headers, body):
        seen.update(method=method, url=url, payload=json.loads(body))
        return response(status)

    client = GitHubClient("owner", "repo", "token", transport)
    assert client.dispatch_workflow("rules.yml", "main", SHA, "12345678-1234-4234-a234-123456789012", smoke_test=True) == status
    assert seen["payload"]["inputs"]["request_id"].startswith("1234")
    assert seen["payload"]["inputs"]["smoke_test"] == "true"


def test_existing_release_asset_is_not_uploaded_again_when_digest_matches(tmp_path: Path):
    asset_path = tmp_path / "manifest.json"
    asset_path.write_text("{}\n")
    digest = "sha256:" + hashlib.sha256(asset_path.read_bytes()).hexdigest()
    calls = []

    def transport(method, url, headers, body):
        calls.append(method)
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    result = client.ensure_release_asset({"assets": [{"name": asset_path.name, "digest": digest, "state": "uploaded"}]}, "https://uploads.example/release{?name,label}", asset_path)
    assert result["digest"] == digest
    assert calls == []


def test_existing_release_asset_mismatch_fails_without_overwrite(tmp_path: Path):
    asset_path = tmp_path / "rules.zip"
    asset_path.write_text("new")
    client = GitHubClient("owner", "repo", "token", lambda *_: response(500))
    with pytest.raises(GitHubError, match="does not match"):
        client.ensure_release_asset({"assets": [{"name": "rules.zip", "digest": "sha256:" + "0" * 64, "state": "uploaded"}]}, "https://uploads.example/release", asset_path)


def test_existing_published_tag_is_found_before_post_even_if_post_would_create_duplicate_draft():
    calls = []
    tag = "rules-" + RELEASE_SHA
    published = {"tag_name": tag, "id": 15, "draft": False, "assets": []}

    def transport(method, url, headers, body):
        calls.append(method)
        if "/releases/tags/" in url:
            return response(200, published)
        if method == "POST":
            return response(201, {"tag_name": tag, "id": 99, "draft": True, "assets": []})
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    release = client.create_or_get_draft_release(tag, "release", "body")
    assert release["id"] == 15
    assert release["draft"] is False
    assert calls == ["GET"]


def test_draft_missing_from_tag_lookup_is_reused_from_release_list_page():
    tag = "rules-" + RELEASE_SHA
    existing = {"tag_name": tag, "id": 15, "draft": True, "assets": [{"name": "rules.zip"}]}
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        if "/releases/tags/" in url:
            return response(404, {})
        if "/releases?per_page=100&page=1" in url:
            return response(200, [{"tag_name": "rules-" + f"{index:064x}", "draft": False} for index in range(100)])
        if "/releases?per_page=100&page=2" in url:
            return response(200, [existing])
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    release = client.create_or_get_draft_release(tag, "release", "body")
    assert release == existing
    assert not any(method == "POST" for method, _ in calls)
    assert any("page=2" in url for _, url in calls)


def test_release_is_created_only_after_tag_lookup_and_release_pages_are_empty():
    tag = "rules-" + RELEASE_SHA
    created = {"tag_name": tag, "id": 20, "draft": True, "assets": []}
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        if "/releases/tags/" in url:
            return response(404, {})
        if "/releases?per_page=100&page=1" in url:
            return response(200, [])
        if method == "POST":
            return response(201, created)
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    release = client.create_or_get_draft_release(tag, "release", "body")
    assert release == created
    assert [method for method, _ in calls] == ["GET", "GET", "POST"]


def test_post_422_race_rechecks_tag_and_reuses_the_new_draft():
    tag = "rules-" + RELEASE_SHA
    existing = {"tag_name": tag, "id": 21, "draft": True, "assets": []}
    tag_lookups = 0
    list_lookups = 0

    def transport(method, url, headers, body):
        nonlocal tag_lookups, list_lookups
        if "/releases/tags/" in url:
            tag_lookups += 1
            return response(404, {})
        if "/releases?per_page=100&page=1" in url:
            list_lookups += 1
            return response(200, [existing] if list_lookups == 2 else [])
        if method == "POST":
            return response(422, {"message": "tag raced"})
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    release = client.create_or_get_draft_release(tag, "release", "body")
    assert release == existing
    assert tag_lookups == 2
    assert list_lookups == 2


def test_existing_all_release_assets_with_matching_digests_are_idempotent(tmp_path: Path):
    paths = []
    assets = []
    for name, content in (("rules.zip", b"bundle"), ("manifest.json", b"{}\n"), ("SHA256SUMS", b"sum\n")):
        path = tmp_path / name
        path.write_bytes(content)
        paths.append(path)
        assets.append({"name": name, "digest": "sha256:" + hashlib.sha256(content).hexdigest(), "state": "uploaded"})
    calls = []
    client = GitHubClient("owner", "repo", "token", lambda *args: calls.append(args) or response(500))
    release = {"draft": True, "assets": assets}
    for path in paths:
        client.ensure_release_asset(release, "https://uploads.github.com/repos/owner/repo/releases/15/assets", path)
    assert calls == []


def test_missing_asset_on_published_release_is_never_uploaded(tmp_path: Path):
    asset_path = tmp_path / "manifest.json"
    asset_path.write_text("{}\n")
    calls = []
    client = GitHubClient("owner", "repo", "token", lambda *args: calls.append(args) or response(500))
    with pytest.raises(GitHubError, match="published immutable release is missing"):
        client.ensure_release_asset({"draft": False, "assets": []}, "https://uploads.github.com/repos/owner/repo/releases/15/assets", asset_path)
    assert calls == []


def test_publish_refuses_stale_data_commit_before_release_mutation():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        if "/compare/" in url:
            return response(200, {"status": "behind"})
        if url.endswith("/commits/rules-data"):
            return response(200, {"sha": "d" * 40})
        if url.endswith("/releases/1"):
            return response(200, {"tag_name": "rules-" + RELEASE_SHA, "draft": True, "assets": [{"name": "rules.zip", "state": "uploaded"}]})
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    with pytest.raises(GitHubError, match="stale data snapshot"):
        client.publish_release(1, "rules-" + RELEASE_SHA, "rules-data", SHA, ["rules.zip"])
    assert not any(method == "PATCH" for method, _ in calls)


def test_published_identical_release_retry_is_a_noop():
    calls = []

    def transport(method, url, headers, body):
        calls.append(method)
        if "/compare/" in url:
            return response(200, {"status": "identical"})
        if url.endswith("/commits/rules-data"):
            return response(200, {"sha": SHA})
        if url.endswith("/releases/1"):
            return response(200, {"tag_name": "rules-" + RELEASE_SHA, "draft": False, "assets": [{"name": "rules.zip", "state": "uploaded"}]})
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    release = client.publish_release(1, "rules-" + RELEASE_SHA, "rules-data", SHA, ["rules.zip"])
    assert release["draft"] is False
    assert "PATCH" not in calls


def test_nonreachable_data_commit_cannot_publish():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        if "/compare/" in url:
            return response(200, {"status": "diverged"})
        return response(500)

    client = GitHubClient("owner", "repo", "token", transport)
    with pytest.raises(GitHubError, match="no longer reachable"):
        client.publish_release(1, "rules-" + RELEASE_SHA, "rules-data", SHA, ["rules.zip"])
    assert len(calls) == 1


def test_release_upload_url_rejects_external_hosts(tmp_path: Path):
    asset = tmp_path / "safe.txt"
    asset.write_text("safe")
    client = GitHubClient("owner", "repo", "secret", lambda *_: response(201, {"name": "safe.txt", "state": "uploaded"}))
    with pytest.raises(ValueError, match="expected GitHub upload endpoint"):
        client.upload_release_asset("https://attacker.example/repos/owner/repo/releases/1/assets", asset)
