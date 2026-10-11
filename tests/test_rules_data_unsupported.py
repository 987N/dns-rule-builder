from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.cli import main
from dns_rule_builder.controller import validate_state
from dns_rule_builder.engine import BuildError, Builder
from dns_rule_builder.rules_state import apply_state
from dns_rule_builder.source_approvals import approval_scope_sha256, shared_scope_for_category
from dns_rule_builder.web import Settings, create_app
from fastapi.testclient import TestClient


SOURCE_SHA = "a" * 40


def project(root: Path, *, include_unsupported: bool = True) -> Path:
    root.mkdir(parents=True)
    for directory in ("config", "data", "src/dns_rule_builder", "templates", "static", "rules-data"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "templates", root / "templates", dirs_exist_ok=True)
    shutil.copytree(repo / "static", root / "static", dirs_exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__="0.1"\n')
    (root / "config/categories.yaml").write_text(
        "categories:\n"
        "  demo:\n"
        "    provider_name: Demo\n"
        "    policy_group: Proxy\n"
        "    priority: 10\n"
        "    enabled: true\n"
        "    source_refs:\n"
        "    - source_id: v2fly\n"
        "      file: demo\n"
        "      format: v2fly-domain-list\n"
        "  foreign_general:\n"
        "    provider_name: Foreign-General\n"
        "    policy_group: Foreign-General\n"
        "    priority: 900\n"
        "    enabled: true\n"
        "    source_file: null\n"
    )
    (root / "config/policies.yaml").write_text("policies:\n  Proxy: {}\n  Foreign-General: {}\n")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/settings.yaml").write_text("schema_version: 4\ntarget_semantics_verified: false\ndeployment_enabled: false\nlicense_release_approved: false\napproved_unsupported_types: []\n")
    (root / "config/sources.yaml").write_text(yaml.safe_dump({"sources": {"v2fly": {"repository": "fixture", "revision": SOURCE_SHA, "archive": "absent.tgz", "archive_sha256": None, "format": "v2fly-domain-list", "adapter_version": 1}}}))
    source = root / ".cache/v2fly"
    (source / "data").mkdir(parents=True)
    (source / ".source-revision").write_text(SOURCE_SHA + "\n")
    (source / "data/demo").write_text("keyword:telemetry.example.net\nexample.net\n" if include_unsupported else "example.net\n")
    ledger: dict = {"schema_version": 1, "sources": {}, "source_profiles": [], "approved_exclusions": [], "unsupported_dispositions": [], "shared_scope_baseline": {}}
    ledger["source_profiles"].append({
        "profile_id": "fixture-demo", "category": "demo",
        "selected_sources": [{"source_id": "v2fly", "key": "demo", "format": "v2fly-domain-list", "include_selector_sha256": approval_scope_sha256([]), "include_closure_file_sha256": {"data/demo": "f" * 64}, "file_declarations": []}],
        "review_status": "approved", "reviewer": "Fixture reviewer", "reviewed_at": "2026-10-11",
        "shared_scope_sha256": approval_scope_sha256(shared_scope_for_category(ledger, "demo", [])),
    })
    if include_unsupported:
        raw = "keyword:telemetry.example.net"
        import hashlib
        ledger["unsupported_dispositions"].append({
            "category": "demo", "source_id": "v2fly", "source_key": "demo", "source_file": "data/demo",
            "type": "KEYWORD", "raw": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "reason": "v2fly rule type is not a domain EXACT/SUFFIX", "review_status": "approved_signature",
            "disposition": "report_only_nonrepresentable", "reviewer": "Fixture reviewer", "reviewed_at": "2026-10-11",
        })
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    return root


def state(dispositions=None):
    return {"schema_version": 1, "categories": {}, "rules": [], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": [], **({"unsupported_dispositions": dispositions} if dispositions is not None else {})}


def build(root: Path, output: Path):
    return Builder(root, data_revision="d" * 40, code_commit="e" * 40,
                   source_trees={"v2fly": {"root": root / ".cache/v2fly", "revision": SOURCE_SHA, "archive_sha256": None}}).build(output)


def test_old_schema_defaults_empty_and_unknown_fields_remain_rejected(tmp_path):
    root = project(tmp_path / "project")
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(yaml.safe_dump(state()))
    result = apply_state(root, legacy)
    assert result["unsupported_dispositions"] == 0
    assert yaml.safe_load((root / "data/unsupported-dispositions.yaml").read_text()) == {"dispositions": []}
    legacy_data = yaml.safe_load(legacy.read_text())
    legacy_data["unexpected"] = True
    legacy.write_text(yaml.safe_dump(legacy_data))
    with pytest.raises(BuildError, match="exactly the schema v1 fields"):
        apply_state(root, legacy)


def test_cli_apply_state_builds_with_exact_rules_data_disposition(tmp_path, monkeypatch, capsys):
    root = project(tmp_path / "project")
    ledger = yaml.safe_load((root / "config/source-approvals.yaml").read_text())
    record = ledger["unsupported_dispositions"].pop()
    record["reviewed_at"] = "2026-10-11T00:00:00Z"
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    state_file = tmp_path / "state.yaml"
    state_file.write_text(yaml.safe_dump(state([record]), sort_keys=False))
    monkeypatch.setattr("sys.argv", ["dns-rule-builder", "--project-root", str(root), "apply-state", str(state_file)])
    main()
    assert json.loads(capsys.readouterr().out)["unsupported_dispositions"] == 1
    result = build(root, tmp_path / "out")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    unsupported = json.loads((result.output_dir / "reports/unsupported.json").read_text())
    assert unsupported[0]["approval_status"] == "report_only_nonrepresentable"
    assert not any(item.get("reason") == "unsupported input rules require review" for item in manifest["publish_blockers"])
    assert manifest["unsupported_dispositions"] == manifest["identity_inputs"]["unsupported_dispositions"]
    assert manifest["unsupported_disposition_report"][0]["status"] == "applied"


def test_rules_data_scope_validation_and_inactive_signature(tmp_path):
    root = project(tmp_path / "project")
    base_ledger = yaml.safe_load((root / "config/source-approvals.yaml").read_text())
    record = dict(base_ledger["unsupported_dispositions"][0])
    record["reviewed_at"] = "2026-10-11T00:00:00Z"
    base_ledger["unsupported_dispositions"] = []
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(base_ledger, sort_keys=False))
    state_file = tmp_path / "state.yaml"
    state_file.write_text(yaml.safe_dump(state([record]), sort_keys=False))
    apply_state(root, state_file)
    # The category remains selected and the exact source signature is applied.
    result = build(root, tmp_path / "out")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert not any(item.get("reason") == "unsupported input rules require review" for item in manifest["publish_blockers"])

    expanded = dict(record, source_key="other-key")
    with pytest.raises(BuildError, match="unselected source key"):
        validate_state(state([expanded]), root)
    expanded_file = dict(record, source_file="data/another-key")
    with pytest.raises(BuildError, match="outside the selected key/include closure"):
        validate_state(state([expanded_file]), root)
    invalid = dict(record, type="SOME-UNKNOWN-TYPE")
    with pytest.raises(BuildError, match="unknown rule type"):
        validate_state(state([invalid]), root)

    (root / ".cache/v2fly/data/demo").write_text("example.net\n")
    inactive_result = build(root, tmp_path / "inactive")
    inactive_manifest = json.loads((inactive_result.output_dir / "manifest.json").read_text())
    assert not any(item.get("reason") == "unsupported input rules require review" for item in inactive_manifest["publish_blockers"])
    assert inactive_manifest["unsupported_disposition_report"][0]["status"] == "inactive"


def test_unapproved_unsupported_syntax_still_blocks_review(tmp_path):
    root = project(tmp_path / "project")
    ledger = yaml.safe_load((root / "config/source-approvals.yaml").read_text())
    ledger["unsupported_dispositions"] = []
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    result = build(root, tmp_path / "out")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["review_required"] is True
    assert manifest["publish_allowed"] is False


def test_web_save_and_remote_sync_preserve_optional_dispositions(tmp_path):
    root = project(tmp_path / "project")
    ledger = yaml.safe_load((root / "config/source-approvals.yaml").read_text())
    record = ledger["unsupported_dispositions"].pop()
    record["reviewed_at"] = "2026-10-11T00:00:00Z"
    (root / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))

    class FakeGitHub:
        def __init__(self):
            self.content = yaml.safe_dump(state(), sort_keys=False).encode()
            self.sha = "initial-sha"
        def get_branch_head(self, branch):
            assert branch == "rules-data"
            return "d" * 40
        def get_contents(self, path, ref):
            assert path == "rules-data/state.yaml"
            return self.content, self.sha
        def update_contents(self, path, content, branch, expected_sha, message):
            assert expected_sha == self.sha
            self.content = content
            self.sha = "updated-sha"
            return "d" * 40

    fake = FakeGitHub()
    secrets = {}
    for name, value in (("token", "fake-token-value-which-is-long"), ("password", "fake-admin-password-long"), ("session", "fake-session-secret-long-enough")):
        path = tmp_path / f"{name}.secret"
        path.write_text(value)
        secrets[name] = path
    app = create_app(Settings(root, tmp_path / "persistent", "owner", "repo", secrets["token"], secrets["password"], secrets["session"], False), lambda: fake, start_worker=False)
    with TestClient(app) as client:
        client.get("/login")
        login = client.post("/login", json={"password": "fake-admin-password-long"})
        csrf = login.json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        initial = client.get("/api/state")
        assert initial.status_code == 200
        assert initial.json()["state"]["unsupported_dispositions"] == []
        edited = state([record])
        response = client.put("/api/draft", json={"state": edited}, headers=headers)
        assert response.status_code == 200
        save = client.post("/api/save", json={}, headers=headers)
        assert save.status_code == 202
        app.state.runner.run_task(save.json()["task_id"])
        remote = yaml.safe_load(fake.content)
        assert remote["unsupported_dispositions"] == [record]
        reread = client.get("/api/state")
        assert reread.json()["state"]["unsupported_dispositions"] == [record]
