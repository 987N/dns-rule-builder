from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.engine import BuildError, Builder
from dns_rule_builder.github import GitHubError
from dns_rule_builder.source_approvals import approval_scope_sha256, shared_scope_for_category
from dns_rule_builder.sources import file_legal_declarations

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fetch_release_baseline.py"
_SPEC = importlib.util.spec_from_file_location("fetch_release_baseline", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
fetch_release_baseline = _MODULE.fetch_release_baseline


SOURCE_REVISION = "83f37120c155f67c8eef43221dabe15cc3c95fec"


class FakeGitHubClient:
    repo_path = "/repos/owner/repo"

    def __init__(self, latest=None, pages=None, payloads=None):
        self.latest = latest
        self.pages = pages or [[]]
        self.payloads = payloads or {}
        self.page_calls = []

    def _json(self, method, path):
        assert method == "GET"
        assert path == f"{self.repo_path}/releases/latest"
        if self.latest is None:
            raise GitHubError(404, "not found")
        return self.latest

    def _release_page(self, page):
        self.page_calls.append(page)
        return self.pages[page - 1] if page <= len(self.pages) else []

    def download_public_release_asset(self, url, maximum_bytes):
        name = url.rsplit("/", 1)[-1]
        payload = self.payloads[name]
        assert len(payload) <= maximum_bytes
        return payload


def _make_project(root: Path) -> tuple[Path, Path, Path]:
    for name in ("config", "data", "src/dns_rule_builder", "licenses"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__ = "0.1.0"\n')
    source_root = root / ".cache/source"
    source_dir = source_root / "rule/Surge/Demo"
    source_dir.mkdir(parents=True)
    (source_root / ".source-revision").write_text(SOURCE_REVISION)
    (source_root / "LICENSE").write_text("MIT fixture license\n")
    source_file = source_dir / "Demo.list"
    source_file.write_text("".join(f"DOMAIN-SUFFIX,d{i}.example.com\n" for i in range(10)))
    license_copy = root / "licenses/SOURCE-MIT.txt"
    license_copy.write_text("MIT fixture license\n")
    sources = {
        "blackmatrix7": {
            "repository": "fixture/source", "revision": SOURCE_REVISION, "archive": "missing.tar.gz",
            "archive_sha256": None, "rules_path": "rule/Surge", "format": "surge",
            "adapter_version": 1, "license": "MIT", "license_file": "licenses/SOURCE-MIT.txt",
        }
    }
    (root / "config/sources.yaml").write_text(yaml.safe_dump({"sources": sources}, sort_keys=False))
    categories = {"demo": {"provider_name": "Demo", "policy_group": "AI", "priority": 10, "enabled": True, "source_refs": [{"source_id": "blackmatrix7", "file": "rule/Surge/Demo/Demo.list", "format": "surge"}]}}
    (root / "config/categories.yaml").write_text(yaml.safe_dump({"categories": categories}, sort_keys=False))
    (root / "config/policies.yaml").write_text("policies:\n  AI: {}\n")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/settings.yaml").write_text("schema_version: 4\ntarget_semantics_verified: false\ndeployment_enabled: false\nlicense_release_approved: true\n")
    (root / "data/custom-rules.yaml").write_text("rules: []\nforce_direct: []\n")

    ref = {
        "source_id": "blackmatrix7", "format": "surge", "adapter_version": 1,
        "file": "rule/Surge/Demo/Demo.list",
    }
    ref["include_selector_sha256"] = approval_scope_sha256([])
    ref["file_declarations"] = file_legal_declarations(source_root, [ref["file"]])
    ledger = {
        "schema_version": 1,
        "sources": {"blackmatrix7": {
            "repository": "fixture/source", "license": "MIT", "license_file": "licenses/SOURCE-MIT.txt",
            "license_sha256": hashlib.sha256(license_copy.read_bytes()).hexdigest(),
            "license_scope": "fixture only", "evidence": ["fixture"],
        }},
        "source_profiles": [{
            "profile_id": "fixture-blackmatrix7", "category": "demo", "selected_sources": [ref],
            "review_status": "approved", "reviewer": "test reviewer", "reviewed_at": "2026-10-11",
            "coverage_status": "partial", "merge_policy": "single-selected-source",
        }],
        "approved_exclusions": [], "unsupported_dispositions": [],
        "shared_scope_baseline": {"excluded_exact_rules": [], "service_exact_additions": {}, "broad_shared_suffixes_prohibited": [], "recognized_shared_roots": []},
    }
    ledger["source_profiles"][0]["shared_scope_sha256"] = approval_scope_sha256(shared_scope_for_category(ledger, "demo", []))
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    trees = {"blackmatrix7": {"root": source_root, "revision": SOURCE_REVISION, "archive_sha256": None, "repository": "fixture/source"}}
    return root, trees, source_file


def _assets(release_dir: Path, *, zip_payload: bytes | None = None, manifest_payload: bytes | None = None) -> tuple[dict, dict[str, bytes]]:
    manifest_bytes = (release_dir / "manifest.json").read_bytes() if manifest_payload is None else manifest_payload
    if zip_payload is None:
        archive_path = release_dir.parent / "test-rules.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(item for item in release_dir.rglob("*") if item.is_file()):
                relative = path.relative_to(release_dir).as_posix()
                info = zipfile.ZipInfo(f"rules/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        zip_payload = archive_path.read_bytes()
    payloads = {"rules.zip": zip_payload, "manifest.json": manifest_bytes}
    sums = "".join(f"{hashlib.sha256(payload).hexdigest()}  {name}\n" for name, payload in payloads.items())
    payloads["SHA256SUMS"] = sums.encode("ascii")
    tag = "rules-" + json.loads(manifest_bytes)["release_id"]
    release = {
        "tag_name": tag,
        "body": "build_purpose=rules\n",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": name, "size": len(payload), "browser_download_url": f"https://github.com/owner/repo/releases/download/{tag}/{name}"}
            for name, payload in payloads.items()
        ],
    }
    return release, payloads


def _build_release(root: Path, source_trees: dict, output: Path) -> Path:
    return Builder(root, source_trees=source_trees, data_revision="d" * 40).build(output).output_dir


def test_no_published_baseline_and_smoke_only_are_reported_as_absent(tmp_path: Path):
    empty = FakeGitHubClient(latest=None, pages=[[]])
    assert fetch_release_baseline(empty, tmp_path / "empty") is None
    assert empty.page_calls == [1]

    smoke = {"tag_name": "rules-" + "a" * 64, "body": "build_purpose=smoke\n", "draft": False, "prerelease": False}
    only_smoke = FakeGitHubClient(latest=smoke, pages=[[smoke]])
    assert fetch_release_baseline(only_smoke, tmp_path / "smoke") is None
    assert only_smoke.page_calls == [1]
    assert not (tmp_path / "smoke/current").exists()


def test_release_api_failure_is_not_reported_as_missing_baseline(tmp_path: Path):
    client = FakeGitHubClient()

    def fail_api(method, path):
        raise GitHubError(503, "temporary API failure")

    client._json = fail_api
    with pytest.raises(GitHubError) as error:
        fetch_release_baseline(client, tmp_path / "out")
    assert error.value.status == 503
    assert not (tmp_path / "out/current").exists()


def test_latest_published_rules_release_is_verified_and_bootstrapped(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    client = FakeGitHubClient(latest=release, payloads=payloads)

    result = fetch_release_baseline(client, tmp_path / "dist/build")

    assert result["release_id"] == release_dir.name
    current = tmp_path / "dist/build/current"
    assert current.is_symlink()
    assert current.resolve() == (tmp_path / "dist/build/releases" / release_dir.name).resolve()
    assert json.loads((current / "manifest.json").read_text())["build_purpose"] == "rules"
    assert client.page_calls == []


def test_latest_smoke_falls_back_to_paginated_formal_release(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    formal, payloads = _assets(release_dir)
    smoke = {"tag_name": "rules-" + "b" * 64, "body": "build_purpose=smoke\n", "draft": False, "prerelease": False}
    first_page = [{"tag_name": f"other-{index}", "draft": False, "prerelease": False} for index in range(100)]
    client = FakeGitHubClient(latest=smoke, pages=[first_page, [smoke, formal]], payloads=payloads)

    result = fetch_release_baseline(client, tmp_path / "out")

    assert result["release_id"] == release_dir.name
    assert client.page_calls == [1, 2]


def test_corrupt_or_identity_mismatched_latest_is_not_silently_skipped(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    fallback = dict(release)
    fallback["tag_name"] = "rules-" + "c" * 64
    client = FakeGitHubClient(latest=fallback, pages=[[release]], payloads=payloads)

    with pytest.raises(BuildError, match="tag does not match manifest identity"):
        fetch_release_baseline(client, tmp_path / "out")
    assert client.page_calls == []
    assert not (tmp_path / "out/current").exists()


def test_sha256sum_mismatch_fails_without_falling_back(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    payloads["manifest.json"] = payloads["manifest.json"].replace(b"{", b" ", 1)
    client = FakeGitHubClient(latest=release, pages=[[]], payloads=payloads)

    with pytest.raises(BuildError, match="hash does not match SHA256SUMS"):
        fetch_release_baseline(client, tmp_path / "out")
    assert client.page_calls == []


def test_zip_special_path_is_rejected(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    manifest = (release_dir / "manifest.json").read_bytes()
    malicious_zip = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious_zip, "w") as archive:
        archive.writestr("rules/manifest.json", manifest)
        archive.writestr("../escape", b"no")
    release, payloads = _assets(release_dir, zip_payload=malicious_zip.read_bytes())
    client = FakeGitHubClient(latest=release, payloads=payloads)

    with pytest.raises(BuildError, match="无法读取规则 Release 压缩包"):
        fetch_release_baseline(client, tmp_path / "out")
    assert not (tmp_path / "out/current").exists()


def test_manifest_bytes_must_match_between_external_asset_and_zip(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    altered_zip = tmp_path / "altered-manifest.zip"
    with zipfile.ZipFile(altered_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in release_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(release_dir).as_posix()
            content = path.read_bytes()
            if relative == "manifest.json":
                content += b" \n"
            archive.writestr(f"rules/{relative}", content)
    release, payloads = _assets(release_dir, zip_payload=altered_zip.read_bytes())
    client = FakeGitHubClient(latest=release, payloads=payloads)

    with pytest.raises(BuildError, match="External manifest bytes do not match"):
        fetch_release_baseline(client, tmp_path / "out")
    assert not (tmp_path / "out/current").exists()


def test_installed_baseline_triggers_builder_category_shrink_gate(tmp_path: Path):
    root, source_root, source_file = _make_project(tmp_path / "project")
    source_release = _build_release(root, source_root, tmp_path / "source-build")
    release, payloads = _assets(source_release)
    fetch_release_baseline(FakeGitHubClient(latest=release, payloads=payloads), tmp_path / "ci-build")

    source_file.write_text("".join(f"DOMAIN-SUFFIX,d{i}.example.com\n" for i in range(6)))
    candidate = Builder(root, source_trees=source_root, data_revision="d" * 40).build(tmp_path / "ci-build")
    manifest = json.loads((candidate.output_dir / "manifest.json").read_text())

    assert candidate.summary["review_required"]
    assert any(item["reason"] == "category decreased by more than 30 percent" for item in manifest["deployment_blockers"])


def test_baseline_without_large_drop_does_not_change_candidate_identity(tmp_path: Path):
    root, source_root, source_file = _make_project(tmp_path / "project")
    source_file.write_text("".join(f"DOMAIN-SUFFIX,d{i}.example.com\n" for i in range(10)))
    ten_rule_release = _build_release(root, source_root, tmp_path / "source-ten")
    ten_assets, ten_payloads = _assets(ten_rule_release)

    source_file.write_text("".join(f"DOMAIN-SUFFIX,d{i}.example.com\n" for i in range(8)))
    eight_rule_release = _build_release(root, source_root, tmp_path / "source-eight")
    eight_assets, eight_payloads = _assets(eight_rule_release)
    ten_output = tmp_path / "out-ten"
    eight_output = tmp_path / "out-eight"
    fetch_release_baseline(FakeGitHubClient(latest=ten_assets, payloads=ten_payloads), ten_output)
    fetch_release_baseline(FakeGitHubClient(latest=eight_assets, payloads=eight_payloads), eight_output)

    ten_result = Builder(root, source_trees=source_root, data_revision="d" * 40).build(ten_output)
    eight_result = Builder(root, source_trees=source_root, data_revision="d" * 40).build(eight_output)

    assert not ten_result.summary["review_required"]
    assert not eight_result.summary["review_required"]
    assert ten_result.release_id == eight_result.release_id


def test_existing_current_or_release_destination_is_never_overwritten(tmp_path: Path):
    root, source_root, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_root, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    client = FakeGitHubClient(latest=release, payloads=payloads)
    out = tmp_path / "out"
    out.mkdir()
    (out / "current").symlink_to("missing-target")

    with pytest.raises(BuildError, match="existing build current pointer"):
        fetch_release_baseline(client, out)
    assert (out / "current").is_symlink()


def test_existing_verified_destination_is_reused_without_deletion(tmp_path: Path):
    root, source_trees, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_trees, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    out = tmp_path / "out"
    destination = out / "releases" / release_dir.name
    destination.parent.mkdir(parents=True)
    shutil.copytree(release_dir, destination)
    before = (destination / "manifest.json").read_bytes()

    result = fetch_release_baseline(FakeGitHubClient(latest=release, payloads=payloads), out)

    assert result["release_id"] == release_dir.name
    assert (destination / "manifest.json").read_bytes() == before
    assert (out / "current").resolve() == destination.resolve()


def test_invalid_existing_destination_is_not_deleted(tmp_path: Path):
    root, source_trees, _ = _make_project(tmp_path / "project")
    release_dir = _build_release(root, source_trees, tmp_path / "source-build")
    release, payloads = _assets(release_dir)
    out = tmp_path / "out"
    destination = out / "releases" / release_dir.name
    destination.mkdir(parents=True)
    marker = destination / "preserve.txt"
    marker.write_text("existing data\n")

    with pytest.raises(BuildError, match="manifest must be a regular file"):
        fetch_release_baseline(FakeGitHubClient(latest=release, payloads=payloads), out)
    assert marker.read_text() == "existing data\n"
