from __future__ import annotations

import json
import hashlib
import io
import tarfile
from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.engine import BuildError, Builder, verify_manifest
from dns_rule_builder.sources import parse_source, safe_source_file
from dns_rule_builder.snapshot import fetch_sources_snapshot
from test_rules_state_snapshot import tiny_project


def _v2fly_tree(root: Path, key: str = "root") -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    (root / ".source-revision").write_text("a" * 40 + "\n")
    (data / "root").write_text("include:middle @cn @-ads\ninclude:child @ads\n", encoding="utf-8")
    (data / "middle").write_text("include:child\n", encoding="utf-8")
    (data / "child").write_text(
        "example.net # comment\n"
        "full:api.example.net @cn\n"
        "domain:svc.example.net @ads\n"
        "keyword:shared-key @cn\n"
        "regexp:^regexp\\.example$ @cn\n"
        "affiliate.example.net @cn &VIRTUAL\n",
        encoding="utf-8",
    )
    return root


def test_v2fly_include_filters_affiliation_and_unsupported_types(tmp_path: Path):
    root = _v2fly_tree(tmp_path / "source")
    rows, hashes, unsupported = parse_source("v2fly", root, "root", {"format": "v2fly-domain-list"})
    # Must-attributes are conjunctive; banned attributes exclude; exact full
    # domains remain exact and unsupported constructs are never approximated.
    assert {(row["domain"], row["match_type"]) for row in rows} == {
        ("api.example.net", "EXACT"),
        ("svc.example.net", "SUFFIX"),
        ("affiliate.example.net", "SUFFIX"),
    }
    assert any(row["domain"] == "api.example.net" and row["attributes"] == ["cn"] for row in rows)
    assert any(row["source_file"] == "data/child" and row["line"] == 2 for row in rows)
    assert {row["type"] for row in unsupported} == {"KEYWORD", "REGEXP"}
    assert {"data/root", "data/middle", "data/child"}.issubset(hashes)


def test_v2fly_virtual_affiliation_uses_full_hashed_tree(tmp_path: Path):
    root = _v2fly_tree(tmp_path / "source")
    rows, hashes, _ = parse_source("v2fly", root, "VIRTUAL", {"format": "v2fly-domain-list"})
    assert [(row["domain"], row["source_file"], row["line"]) for row in rows] == [
        ("affiliate.example.net", "data/child", 6)
    ]
    assert set(hashes) == {"data/root", "data/middle", "data/child"}


def test_v2fly_virtual_affiliation_can_be_included_with_attribute_filter(tmp_path: Path):
    root = _v2fly_tree(tmp_path / "source")
    (root / "data/root").write_text("include:VIRTUAL @cn\n")
    rows, _hashes, _ = parse_source("v2fly", root, "root", {"format": "v2fly-domain-list"})
    assert [(row["domain"], row["include_chain"]) for row in rows] == [
        ("affiliate.example.net", ["ROOT", "VIRTUAL"])
    ]


def test_v2fly_cycle_missing_include_and_source_paths_rejected(tmp_path: Path):
    root = _v2fly_tree(tmp_path / "source")
    (root / "data/root").write_text("include:child\n")
    (root / "data/child").write_text("include:root\n")
    with pytest.raises(BuildError, match="cycle"):
        parse_source("v2fly", root, "root", {"format": "v2fly-domain-list"})
    (root / "data/root").write_text("include:missing\n")
    with pytest.raises(BuildError, match="does not exist"):
        parse_source("v2fly", root, "root", {"format": "v2fly-domain-list"})
    with pytest.raises(BuildError, match="unsafe"):
        safe_source_file(root, "data/../secret")
    target = tmp_path / "outside"
    target.write_text("example.net\n")
    (root / "data/link").symlink_to(target)
    with pytest.raises(BuildError, match="symlink"):
        safe_source_file(root, "data/link")


def test_source_snapshot_locks_all_remote_revisions_before_downloading(tmp_path: Path, monkeypatch):
    calls = []

    def fake_get(url, limit, github_token=None):
        calls.append((url, github_token))
        if "/commits/" in url:
            name = "a" if "one/repo" in url else "b"
            return json.dumps({"sha": name * 40, "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}).encode()
        source = "one-repo" if "one/repo" in url else "two-repo"
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            entry = tarfile.TarInfo(f"{source}/README")
            entry.size = 2
            archive.addfile(entry, io.BytesIO(b"ok"))
        return payload.getvalue()

    monkeypatch.setattr("dns_rule_builder.snapshot._get", fake_get)
    result = fetch_sources_snapshot(tmp_path / "cache", {
        "one": {"repository": "one/repo", "branch": "master"},
        "two": {"repository": "two/repo", "branch": "main"},
    }, "test-token")
    assert all("/commits/" in calls[index][0] for index in (0, 1))
    assert all(calls[index][1] == "test-token" for index in (0, 1))
    assert all("codeload.github.com" in calls[index][0] and calls[index][1] is None for index in (2, 3))
    assert result["one"]["revision"] == "a" * 40 and result["two"]["revision"] == "b" * 40
    assert (Path(result["one"]["source_root"]) / ".source-revision").read_text().strip() == "a" * 40


def test_local_source_snapshot_is_pinned_to_committed_code_and_hashes_tree(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    local = project / "config/manual"
    local.mkdir(parents=True)
    (local / "rules.list").write_text("DOMAIN-SUFFIX,manual.example\n")
    monkeypatch.setattr("dns_rule_builder.snapshot.subprocess.check_output", lambda *args, **kwargs: "c" * 40)
    result = fetch_sources_snapshot(tmp_path / "cache", {"manual": {"kind": "local", "path": "config/manual"}}, project_root=project)
    assert result["manual"]["revision"] == "c" * 40
    assert result["manual"]["kind"] == "local"
    assert result["manual"]["source_root"] == str(project.resolve())
    assert len(result["manual"]["archive_sha256"]) == 64


def test_controlled_local_surge_source_builds_with_code_commit_and_file_hash(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    manual_root = project / "config/manual"
    manual_root.mkdir()
    (manual_root / "rules.list").write_text("DOMAIN-SUFFIX,manual.example\n")
    bm_root = project / ".cache/bm"
    (bm_root / "rule/Surge").mkdir(parents=True)
    (bm_root / ".source-revision").write_text("8" * 40 + "\n")
    (project / "config/sources.yaml").write_text(yaml.safe_dump({"sources": {
        "blackmatrix7": {"repository": "fixture", "revision": "8" * 40, "archive": "none", "rules_path": "rule/Surge", "format": "surge"},
        "manual": {"kind": "local", "repository": "local", "path": "config/manual", "format": "surge", "adapter_version": 1},
    }}))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "manual", "file": "rules.list", "format": "surge"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories))
    source_trees = {
        "blackmatrix7": {"root": bm_root, "revision": "8" * 40, "archive_sha256": None, "repository": "fixture"},
        "manual": {"root": project, "revision": "c" * 40, "archive_sha256": "d" * 64, "repository": "local", "kind": "local"},
    }
    candidate = Builder(project, data_revision="e" * 40, code_commit="c" * 40, source_trees=source_trees).build(tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    assert manifest["identity_inputs"]["normalized_rules"][0]["source"] == "manual"
    assert manifest["sources"]["manual"]["revision"] == "c" * 40
    assert "config/manual/rules.list" in manifest["sources"]["manual"]["file_sha256"]


def test_v2fly_only_snapshot_builds_without_blackmatrix_cache(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    source_root = _v2fly_tree(project / ".cache/v2fly")
    sources = yaml.safe_load((project / "config/sources.yaml").read_text())
    sources["sources"]["v2fly"] = {"repository": "fixture", "format": "v2fly-domain-list", "adapter_version": 1}
    (project / "config/sources.yaml").write_text(yaml.safe_dump(sources, sort_keys=False))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "v2fly", "file": "root", "format": "v2fly-domain-list"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories, sort_keys=False))
    result = Builder(project, source_trees={"v2fly": {"root": source_root, "revision": "a" * 40, "archive_sha256": "b" * 64, "repository": "fixture"}}).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    assert manifest["source_revision"] == "a" * 40
    assert set(manifest["sources"]) == {"v2fly"}
    assert manifest["statistics"]["rule_count"] > 0


def test_cli_source_revision_uses_selected_source_not_unselected_blackmatrix(tmp_path: Path):
    from dns_rule_builder.cli import _builder_from_args

    project = tiny_project(tmp_path / "project")
    source_root = _v2fly_tree(project / ".cache/v2fly")
    sources = yaml.safe_load((project / "config/sources.yaml").read_text())
    sources["sources"]["v2fly"] = {"repository": "fixture", "format": "v2fly-domain-list", "adapter_version": 1}
    (project / "config/sources.yaml").write_text(yaml.safe_dump(sources, sort_keys=False))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "v2fly", "file": "root", "format": "v2fly-domain-list"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories, sort_keys=False))
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"schema_version": 1, "sources": {"v2fly": {
        "source_root": str(source_root), "revision": "a" * 40, "archive_sha256": "b" * 64, "repository": "fixture",
    }}}))
    args = SimpleNamespace(sources_snapshot=snapshot_path, source_revision=None, source_root=None, data_revision=None,
                           code_commit=None, snapshot_time_utc=None, build_purpose="rules")
    builder = _builder_from_args(args, project)
    assert builder.source_revision == "a" * 40
    assert "blackmatrix7" not in builder.source_trees


def test_manual_only_snapshot_builds_without_blackmatrix_cache(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    manual_root = project / "config/manual"
    manual_root.mkdir()
    (manual_root / "rules.list").write_text("DOMAIN-SUFFIX,manual.example.net\n")
    sources = yaml.safe_load((project / "config/sources.yaml").read_text())
    sources["sources"]["manual"] = {"kind": "local", "repository": "local", "path": "config/manual", "format": "surge", "adapter_version": 1}
    (project / "config/sources.yaml").write_text(yaml.safe_dump(sources, sort_keys=False))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "manual", "file": "rules.list", "format": "surge"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories, sort_keys=False))
    result = Builder(project, source_trees={"manual": {"root": project, "revision": "c" * 40, "archive_sha256": "d" * 64, "repository": "local", "kind": "local"}}).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    assert manifest["source_revision"] == "c" * 40
    assert set(manifest["sources"]) == {"manual"}
    assert "config/manual/rules.list" in manifest["sources"]["manual"]["file_sha256"]


def test_explicit_empty_snapshot_custom_only_does_not_require_blackmatrix_cache(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    for category, config in categories["categories"].items():
        if category != "foreign_general":
            config["enabled"] = False
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories, sort_keys=False))
    (project / "data/custom-rules.yaml").write_text(yaml.safe_dump({"rules": [{"domain": "custom.example.net", "match_type": "EXACT", "category": "foreign_general"}], "force_direct": []}, sort_keys=False))
    result = Builder(project, source_trees={}, build_purpose="smoke").build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    assert manifest["source_revision"] is None
    assert manifest["sources"] == {}
    assert manifest["statistics"]["rule_count"] == 1
    assert manifest["build_purpose"] == "smoke"


def test_virtual_v2fly_selected_key_records_actual_contributor_hashes(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    source_root = _v2fly_tree(project / ".cache/v2fly")
    bm_root = project / ".cache/bm"
    (bm_root / "rule/Surge").mkdir(parents=True)
    (bm_root / ".source-revision").write_text("8" * 40 + "\n")
    (project / "config/sources.yaml").write_text(yaml.safe_dump({"sources": {
        "blackmatrix7": {"repository": "fixture", "revision": "8" * 40, "archive": "none", "rules_path": "rule/Surge", "format": "surge"},
        "v2fly": {"repository": "fixture", "format": "v2fly-domain-list"},
    }}))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "v2fly", "file": "VIRTUAL", "format": "v2fly-domain-list"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories))
    source_trees = {
        "blackmatrix7": {"root": bm_root, "revision": "8" * 40, "archive_sha256": None, "repository": "fixture"},
        "v2fly": {"root": source_root, "revision": "a" * 40, "archive_sha256": "b" * 64, "repository": "fixture"},
    }
    candidate = Builder(project, source_trees=source_trees).build(tmp_path / "out")
    manifest = verify_manifest(candidate.output_dir)
    source = manifest["sources"]["v2fly"]
    assert source["selected_refs"] == [{"category": "demo", "file": "VIRTUAL", "format": "v2fly-domain-list", "reference_kind": "list-key"}]
    assert "data/child" in source["file_sha256"] and "VIRTUAL" not in source["file_sha256"]


def test_source_input_identity_changes_and_build_is_reproducible(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    source_root = _v2fly_tree(project / ".cache/v2fly")
    bm_root = project / ".cache/bm"
    (bm_root / "rule/Surge").mkdir(parents=True)
    (bm_root / ".source-revision").write_text("8" * 40 + "\n")
    (project / "config/sources.yaml").write_text(yaml.safe_dump({"sources": {
        "blackmatrix7": {"repository": "fixture", "revision": "8" * 40, "archive": "none", "rules_path": "rule/Surge", "format": "surge"},
        "v2fly": {"repository": "fixture", "format": "v2fly-domain-list"},
    }}))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())
    categories["categories"]["demo"]["source_refs"] = [{"source_id": "v2fly", "file": "root", "format": "v2fly-domain-list"}]
    categories["categories"]["demo"].pop("source_file")
    (project / "config/categories.yaml").write_text(yaml.safe_dump(categories))
    locked = {
        "blackmatrix7": {"root": bm_root, "revision": "8" * 40, "archive_sha256": "2" * 64, "repository": "fixture", "source_date_utc": "2026-01-01T00:00:00Z"},
        "v2fly": {"root": source_root, "revision": "a" * 40, "archive_sha256": "1" * 64, "repository": "fixture", "source_date_utc": "2026-01-01T00:00:00Z"},
    }
    first = Builder(project, source_trees=locked).build(tmp_path / "out-a")
    second = Builder(project, source_trees=locked).build(tmp_path / "out-b")
    assert first.release_id == second.release_id
    source_file = source_root / "data/child"
    source_file.write_text(source_file.read_text() + "new.example.net\n")
    changed = Builder(project, source_trees=locked).build(tmp_path / "out-c")
    assert changed.release_id != first.release_id
    manifest = json.loads((changed.output_dir / "manifest.json").read_text())
    assert manifest["sources"]["v2fly"]["revision"] == "a" * 40
    assert manifest["sources"]["v2fly"]["adapter_version"] == 1
    assert manifest["sources"]["v2fly"]["file_sha256"] == manifest["identity_inputs"]["sources"]["v2fly"]["file_sha256"]
    manifest_path = changed.output_dir / "manifest.json"
    manifest["sources"]["v2fly"]["revision"] = "b" * 40
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BuildError, match="source metadata mismatch"):
        verify_manifest(changed.output_dir)


def test_malformed_surge_domain_becomes_reviewable_unsupported(tmp_path: Path):
    source = tmp_path / "source"
    (source / "rule/Surge/Demo").mkdir(parents=True)
    (source / "rule/Surge/Demo/list.list").write_text("DOMAIN,not a valid host\n")
    rows, hashes, unsupported = parse_source("blackmatrix7", source, "rule/Surge/Demo/list.list", {"format": "surge"})
    assert rows == [] and len(hashes) == 1
    assert unsupported[0]["line"] == 1 and "bare DNS domain" in unsupported[0]["reason"]


def test_source_approval_raw_hash_and_scope_are_validated(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    source_root = project / ".cache/source"
    (project / "config/source-approvals.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "source_profiles": [{
            "profile_id": "unknown-category", "category": "missing", "review_status": "approved",
            "reviewer": "test", "reviewed_at": "2026-10-11",
            "selected_sources": [{"source_id": "blackmatrix7", "file": "Demo/Demo.list", "format": "surge"}],
        }],
        "approved_exclusions": [], "unsupported_dispositions": [],
    }, sort_keys=False))
    with pytest.raises(BuildError, match="unknown category"):
        Builder(project, source_root=source_root)

    (project / "config/source-approvals.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "source_profiles": [],
        "approved_exclusions": [{
            "category": "demo", "domain": "api.example.com", "match_type": "EXACT", "source_id": "blackmatrix7",
            "source_key": "demo", "source_file": "Demo/Demo.list", "raw": "DOMAIN,api.example.com",
            "raw_sha256": "0" * 64, "review_status": "approved", "reviewer": "test", "reviewed_at": "2026-10-11", "reason": "test",
        }],
        "unsupported_dispositions": [],
    }, sort_keys=False))
    with pytest.raises(BuildError, match="raw text does not match its hash"):
        Builder(project, source_root=source_root)

    raw_rule = "DOMAIN,api.example.com"
    (project / "config/source-approvals.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "source_profiles": [{
            "profile_id": "demo", "category": "demo", "review_status": "approved", "reviewer": "test", "reviewed_at": "2026-10-11",
            "selected_sources": [{"source_id": "blackmatrix7", "file": "Demo/Demo.list", "format": "surge"}],
        }],
        "approved_exclusions": [{
            "category": "demo", "domain": "api.example.com", "match_type": "EXACT", "source_id": "blackmatrix7",
            "source_key": "unknown-key", "source_file": "Demo/Demo.list", "raw": raw_rule,
            "raw_sha256": hashlib.sha256(raw_rule.encode()).hexdigest(), "review_status": "approved", "reviewer": "test", "reviewed_at": "2026-10-11", "reason": "test",
        }],
        "unsupported_dispositions": [],
    }, sort_keys=False))
    with pytest.raises(BuildError, match="unselected source key"):
        Builder(project, source_root=source_root)
