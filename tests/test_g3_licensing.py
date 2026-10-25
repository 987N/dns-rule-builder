from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.engine import BuildError, Builder, canonical_json, verify_manifest
from dns_rule_builder.source_approvals import approval_scope_sha256, shared_scope_for_category
from dns_rule_builder.sources import v2fly_include_scope


REVISION = "a" * 40


def licensed_project(root: Path, *, v2fly: bool = False) -> tuple[Path, dict[str, dict[str, object]]]:
    for name in ("config", "data", "src/dns_rule_builder", "licenses"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__ = "0.1.0"\n')
    source_root = root / ".cache/source"
    (source_root / ".source-revision").parent.mkdir(parents=True, exist_ok=True)
    (source_root / ".source-revision").write_text(REVISION + "\n")
    (source_root / "LICENSE").write_text("MIT fixture license\n")
    if v2fly:
        (source_root / "data").mkdir()
        (source_root / "data/demo").write_text("include:shared @cn\n")
        (source_root / "data/shared").write_text("api.example.net @cn\n")
        source_id, fmt, source_file = "v2fly", "v2fly-domain-list", "demo"
    else:
        (source_root / "rule/Surge/Demo").mkdir(parents=True)
        (source_root / "rule/Surge/Demo/Demo.list").write_text("DOMAIN-SUFFIX,example.net\n")
        source_id, fmt, source_file = "blackmatrix7", "surge", "rule/Surge/Demo/Demo.list"
    license_copy = root / "licenses/SOURCE-MIT.txt"
    license_copy.write_text("MIT fixture license\n")
    source_cfg = {
        source_id: {
            "repository": "fixture/source", "revision": REVISION, "archive": "missing.tar.gz",
            "archive_sha256": None, "rules_path": "data" if v2fly else "rule/Surge", "format": fmt,
            "adapter_version": 1, "license": "MIT", "license_file": "licenses/SOURCE-MIT.txt",
        }
    }
    (root / "config/sources.yaml").write_text(yaml.safe_dump({"sources": source_cfg}, sort_keys=False))
    categories = {"demo": {"provider_name": "Demo", "policy_group": "AI", "priority": 10, "enabled": True, "source_refs": [{"source_id": source_id, "file": source_file, "format": fmt}]}}
    (root / "config/categories.yaml").write_text(yaml.safe_dump({"categories": categories}, sort_keys=False))
    (root / "config/policies.yaml").write_text("policies:\n  AI: {}\n")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/settings.yaml").write_text("schema_version: 4\ntarget_semantics_verified: false\ndeployment_enabled: false\nlicense_release_approved: true\n")
    (root / "data/custom-rules.yaml").write_text("rules: []\nforce_direct: []\n")
    ledger: dict[str, object] = {
        "schema_version": 1,
        "sources": {source_id: {"repository": "fixture/source", "license": "MIT", "license_file": "licenses/SOURCE-MIT.txt", "license_sha256": hashlib.sha256(b"MIT fixture license\n").hexdigest(), "license_scope": "fixture only", "evidence": ["fixture"]}},
        "shared_scope_baseline": {"excluded_exact_rules": [], "service_exact_additions": {}, "broad_shared_suffixes_prohibited": [], "recognized_shared_roots": ["auth0.com", "stripe.com", "cloudfront.net", "sentry.io", "datadoghq.com", "qualtrics.com", "workos.com", "workoscdn.com", "usefathom.com", "fathom.com"]},
        "source_profiles": [], "approved_exclusions": [], "unsupported_dispositions": [],
    }
    ref: dict[str, object] = {"source_id": source_id, "format": fmt, "adapter_version": 1}
    if v2fly:
        closure, selectors = v2fly_include_scope(source_root, source_file)
        ref["key"] = source_file
    else:
        closure, selectors = [source_file], []
        ref["file"] = source_file
    from dns_rule_builder.sources import file_legal_declarations
    ref["include_selector_sha256"] = approval_scope_sha256(selectors)
    ref["file_declarations"] = file_legal_declarations(source_root, closure)
    profile = {
        "profile_id": f"fixture-{source_id}", "category": "demo", "selected_sources": [ref],
        "review_status": "approved", "reviewer": "test reviewer", "reviewed_at": "2026-10-11",
        "coverage_status": "partial", "merge_policy": "single-selected-source",
    }
    ledger["source_profiles"] = [profile]
    ledger["shared_scope_sha256"] = "unused-top-level"
    profile["shared_scope_sha256"] = approval_scope_sha256(shared_scope_for_category(ledger, "demo", []))
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    return root, {source_id: {"root": source_root, "revision": REVISION, "archive_sha256": None, "repository": "fixture/source"}}


def build(project: Path, trees: dict[str, dict[str, object]], output: Path):
    return Builder(project, source_trees=trees, data_revision="d" * 40).build(output)


def reseal_test_candidate(release_dir: Path) -> Path:
    manifest_path = release_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_sha256"] = {
        path.relative_to(release_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(release_dir.rglob("*")) if path.is_file() and path != manifest_path
    }
    manifest["release_id"] = hashlib.sha256(canonical_json({"identity_inputs": manifest["identity_inputs"], "artifact_sha256": manifest["artifact_sha256"]})).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    resealed = release_dir.with_name(manifest["release_id"])
    release_dir.rename(resealed)
    return resealed


def test_reviewed_domain_delta_inherits_profile_but_license_change_blocks(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    first = build(project, trees, tmp_path / "out")
    first_manifest = verify_manifest(first.output_dir)
    assert first_manifest["review_required"] is False
    assert first_manifest["publish_allowed"] is True
    license_record = first_manifest["source_license_provenance"]["blackmatrix7"]
    assert license_record["license_sha256"] == license_record["approved_license_sha256"]
    assert (first.output_dir / "provenance/sources/blackmatrix7/LICENSE.txt").is_file()

    source_rules = trees["blackmatrix7"]["root"] / "rule/Surge/Demo/Demo.list"
    source_rules.write_text("DOMAIN-SUFFIX,example.net\nDOMAIN,new.example.net\n")
    delta = build(project, trees, tmp_path / "out")
    delta_manifest = verify_manifest(delta.output_dir)
    assert delta.release_id != first.release_id
    assert delta_manifest["review_required"] is False
    assert delta_manifest["publish_allowed"] is True

    actual_license = trees["blackmatrix7"]["root"] / "LICENSE"
    actual_license.write_text("MIT fixture license, amended\n")
    changed = build(project, trees, tmp_path / "out")
    changed_manifest = verify_manifest(changed.output_dir)
    assert changed.release_id != delta.release_id
    assert changed_manifest["review_required"] is True
    assert changed_manifest["publish_allowed"] is False
    assert any(item["reason"] == "source license text changed and requires review" for item in changed_manifest["source_license_blockers"])
    assert (changed.output_dir / "provenance/sources/blackmatrix7/LICENSE.txt").read_text() == "MIT fixture license, amended\n"


def test_source_profile_pending_cannot_be_overridden_by_legacy_boolean(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    ledger_path = project / "config/source-approvals.yaml"
    ledger = yaml.safe_load(ledger_path.read_text())
    ledger["source_profiles"][0]["review_status"] = "pending"
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False))
    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    assert manifest["review_required"] is True
    assert manifest["publish_allowed"] is False
    assert any(item["reason"] == "enabled source profile requires review" for item in manifest["deployment_blockers"])


def test_source_approved_exact_exclusion_is_applied_to_compiled_artifacts(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project", v2fly=True)
    source_file = trees["v2fly"]["root"] / "data/shared"
    source_file.write_text("api.example.net @cn\nfull:telemetry.example.net @cn\n")
    ledger_path = project / "config/source-approvals.yaml"
    ledger = yaml.safe_load(ledger_path.read_text())
    raw = "full:telemetry.example.net @cn"
    ledger["approved_exclusions"].append({
        "category": "demo", "domain": "telemetry.example.net", "match_type": "EXACT",
        "source_id": "v2fly", "source_key": "demo", "source_file": "data/shared",
        "source_line": 2, "raw": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "review_status": "approved", "reviewer": "test reviewer", "reviewed_at": "2026-10-25",
        "reason": "Exclude this exact telemetry source row.",
    })
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False))

    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    assert manifest["publish_allowed"] is True
    excluded = json.loads((candidate.output_dir / "reports/excluded-rules.json").read_text())
    applied = [row for row in excluded if row.get("exclusion", {}).get("domain") == "telemetry.example.net"]
    assert len(applied) == 1 and applied[0]["status"] == "applied"
    compiled = [
        path.read_text()
        for directory in (candidate.output_dir / "smartdns", candidate.output_dir / "mihomo")
        for path in directory.rglob("*") if path.is_file()
    ]
    assert all("telemetry.example.net" not in content for content in compiled)


def test_approved_notice_disappearance_requires_review(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    source_notice = trees["blackmatrix7"]["root"] / "NOTICE"
    source_notice.write_text("Fixture notice\n")
    notice_copy = project / "licenses/SOURCE-NOTICE.txt"
    notice_copy.write_text("Fixture notice\n")
    ledger_path = project / "config/source-approvals.yaml"
    ledger = yaml.safe_load(ledger_path.read_text())
    ledger["sources"]["blackmatrix7"].update({"notice_file": "licenses/SOURCE-NOTICE.txt", "notice_sha256": hashlib.sha256(b"Fixture notice\n").hexdigest()})
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False))
    first = build(project, trees, tmp_path / "out")
    assert verify_manifest(first.output_dir)["publish_allowed"] is True
    source_notice.unlink()
    removed = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(removed.output_dir)
    assert manifest["review_required"] is True
    assert manifest["publish_allowed"] is False
    assert any(item["reason"] == "approved upstream NOTICE disappeared and requires review" for item in manifest["source_license_blockers"])


def test_license_copy_symlink_component_is_rejected(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    licenses = project / "licenses"
    real_dir = licenses / "real"
    real_dir.mkdir()
    (real_dir / "SOURCE-MIT.txt").write_text("MIT fixture license\n")
    (licenses / "nested").symlink_to(real_dir, target_is_directory=True)
    ledger_path = project / "config/source-approvals.yaml"
    ledger = yaml.safe_load(ledger_path.read_text())
    ledger["sources"]["blackmatrix7"]["license_file"] = "licenses/nested/SOURCE-MIT.txt"
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False))
    source_cfg_path = project / "config/sources.yaml"
    source_cfg = yaml.safe_load(source_cfg_path.read_text())
    source_cfg["sources"]["blackmatrix7"]["license_file"] = "licenses/nested/SOURCE-MIT.txt"
    source_cfg_path.write_text(yaml.safe_dump(source_cfg, sort_keys=False))
    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    assert manifest["publish_allowed"] is False
    assert any(item["reason"] == "source license copy is missing or unsafe" for item in manifest["source_license_blockers"])


def test_resealed_candidate_cannot_omit_selected_source_license_provenance(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    candidate = build(project, trees, tmp_path / "out")
    copied = tmp_path / "forged"
    shutil.copytree(candidate.output_dir, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for location in (manifest, manifest["identity_inputs"]):
        location["source_license_provenance"].pop("blackmatrix7", None)
    summary_path = copied / "provenance/source-review-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["source_license_provenance"] = manifest["identity_inputs"]["source_license_provenance"]
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (copied / "provenance/sources/blackmatrix7/LICENSE.txt").unlink()
    forged = reseal_test_candidate(copied)
    with pytest.raises(BuildError, match="source license provenance does not cover selected source references"):
        verify_manifest(forged)


def test_resealed_candidate_cannot_omit_selected_category_profile_observation(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    candidate = build(project, trees, tmp_path / "out")
    copied = tmp_path / "forged"
    shutil.copytree(candidate.output_dir, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for location in (manifest, manifest["identity_inputs"]):
        location["source_profile_observations"].pop("demo", None)
    summary_path = copied / "provenance/source-review-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["source_profiles"] = manifest["identity_inputs"]["source_profile_observations"]
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    forged = reseal_test_candidate(copied)
    with pytest.raises(BuildError, match="source profile observations contain missing or extra categories"):
        verify_manifest(forged)


def test_include_selector_change_requires_review_even_when_files_are_unchanged(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project", v2fly=True)
    source_root = trees["v2fly"]["root"]
    first = build(project, trees, tmp_path / "out")
    first_manifest = verify_manifest(first.output_dir)
    assert first_manifest["publish_allowed"] is True
    key_file = source_root / "data/demo"
    key_file.write_text("include:shared @cn @-ads\n")
    changed = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(changed.output_dir)
    assert changed.release_id != first.release_id
    assert manifest["review_required"] is True
    assert manifest["publish_allowed"] is False
    assert any(item["reason"] == "source include selectors require review" for item in manifest["deployment_blockers"])


def test_shared_infrastructure_scope_is_checked_against_parsed_rules(tmp_path: Path):
    project, trees = licensed_project(tmp_path / "project")
    source_rules = trees["blackmatrix7"]["root"] / "rule/Surge/Demo/Demo.list"
    source_rules.write_text("DOMAIN-SUFFIX,example.net\nDOMAIN-SUFFIX,stripe.com\n")
    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    assert manifest["review_required"] is True
    assert manifest["publish_allowed"] is False
    assert manifest["source_shared_scope_blockers"]


def _publisher_module():
    path = Path(__file__).resolve().parents[1] / "scripts/publish_rules_release.py"
    spec = importlib.util.spec_from_file_location("publish_rules_release_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publisher_assets_are_candidate_derived_deterministic_and_upload_failure_stops(tmp_path: Path, monkeypatch):
    project, trees = licensed_project(tmp_path / "project")
    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    publisher = _publisher_module()
    assets_a = publisher._candidate_assets(candidate.output_dir, tmp_path / "assets-a")
    assets_b = publisher._candidate_assets(candidate.output_dir, tmp_path / "assets-b")
    assert [path.name for path in assets_a] == [path.name for path in assets_b]
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in assets_a] == [hashlib.sha256(path.read_bytes()).hexdigest() for path in assets_b]
    external_license = tmp_path / "assets-a/LICENSE-blackmatrix7.txt"
    bundled_license = candidate.output_dir / "provenance/sources/blackmatrix7/LICENSE.txt"
    assert external_license.read_bytes() == bundled_license.read_bytes()
    sums = (tmp_path / "assets-a/SHA256SUMS").read_text()
    assert all(path.name in {line.split("  ", 1)[1] for line in sums.splitlines()} for path in assets_a[:-1])
    with zipfile.ZipFile(tmp_path / "assets-a/rules.zip") as archive:
        assert archive.read("rules/provenance/sources/blackmatrix7/LICENSE.txt") == bundled_license.read_bytes()

    calls = {"published": False}
    publications = []
    class FakeClient:
        def __init__(self, *_args): pass
        def create_or_get_draft_release(self, *_args): return {"upload_url": "https://uploads.example.invalid/", "id": 17}
        def ensure_release_asset(self, *_args): raise RuntimeError("simulated upload failure")
        def publish_release(self, *_args, **_kwargs): calls["published"] = True; return {"draft": False}

    monkeypatch.setattr(publisher, "GitHubClient", FakeClient)
    monkeypatch.setenv("RULE_RELEASE_DIR", str(candidate.output_dir))
    monkeypatch.setenv("DATA_COMMIT", str(manifest["data_revision"]))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only")
    with pytest.raises(RuntimeError, match="simulated upload failure"):
        publisher.main()
    assert calls["published"] is False

    persisted_assets = {}
    ensure_calls = []
    class IdempotentFakeClient:
        def __init__(self, *_args): self.assets = persisted_assets
        def create_or_get_draft_release(self, tag, *_args): return {"upload_url": "https://uploads.example.invalid/", "id": 17, "tag_name": tag}
        def ensure_release_asset(self, _draft, _url, path):
            ensure_calls.append(path.name)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            prior = self.assets.setdefault(path.name, digest)
            assert prior == digest
        def publish_release(self, _id, tag, *_args, **kwargs):
            publications.append((tag, kwargs.get("make_latest")))
            return {"draft": False}

    monkeypatch.setattr(publisher, "GitHubClient", IdempotentFakeClient)
    assert publisher.main() == 0
    assert publisher.main() == 0
    assert len(publications) == 2 and publications[0] == publications[1]
    assert len(ensure_calls) == 2 * len(persisted_assets)
    assert len(persisted_assets) == len(set(ensure_calls))


def test_publisher_verifies_all_source_licenses_before_constructing_github_client(tmp_path: Path, monkeypatch):
    project, trees = licensed_project(tmp_path / "project", v2fly=True)
    candidate = build(project, trees, tmp_path / "out")
    copied = tmp_path / "forged"
    shutil.copytree(candidate.output_dir, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for location in (manifest, manifest["identity_inputs"]):
        location["source_license_provenance"].pop("v2fly", None)
    summary_path = copied / "provenance/source-review-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["source_license_provenance"] = manifest["identity_inputs"]["source_license_provenance"]
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (copied / "provenance/sources/v2fly/LICENSE.txt").unlink()
    forged = reseal_test_candidate(copied)
    publisher = _publisher_module()
    constructed = []
    monkeypatch.setattr(publisher, "GitHubClient", lambda *_args: constructed.append(True))
    monkeypatch.setenv("RULE_RELEASE_DIR", str(forged))
    monkeypatch.setenv("DATA_COMMIT", str(manifest["data_revision"]))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only")
    with pytest.raises(BuildError, match="source license provenance does not cover selected source references"):
        publisher.main()
    assert not constructed


def test_publisher_prepares_assets_before_constructing_github_client(tmp_path: Path, monkeypatch):
    project, trees = licensed_project(tmp_path / "project")
    candidate = build(project, trees, tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    publisher = _publisher_module()
    constructed = []
    monkeypatch.setattr(publisher, "GitHubClient", lambda *_args: constructed.append(True))
    def fail_asset_preparation(*_args):
        raise BuildError("simulated local asset validation failure")
    monkeypatch.setattr(publisher, "_candidate_assets", fail_asset_preparation)
    monkeypatch.setenv("RULE_RELEASE_DIR", str(candidate.output_dir))
    monkeypatch.setenv("DATA_COMMIT", str(manifest["data_revision"]))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only")
    with pytest.raises(BuildError, match="simulated local asset validation failure"):
        publisher.main()
    assert not constructed
