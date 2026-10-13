from __future__ import annotations

import json
import hashlib
import shutil
import uuid

import pytest
import yaml

from dns_rule_builder.engine import Builder, BuildError
from dns_rule_builder.store import Store
from dns_rule_builder.target_v4 import DeploymentCoordinator, SimulatedTarget, TargetV4Error, load_target_specs, target_manifest
from dns_rule_builder.target_attestation import AttestationError, check_preparation_candidate


def _verified_release(tmp_path):
    from test_engine import make_project, SOURCE_REVISION
    from dns_rule_builder.source_approvals import approval_scope_sha256, shared_scope_for_category
    from dns_rule_builder.sources import file_legal_declarations

    project, source_root = make_project(tmp_path / "project")
    (source_root / "rule/Surge/Demo/Demo.list").write_text("DOMAIN-SUFFIX,example.com\n")
    license_text = "MIT fixture license\n"
    source_license = source_root / "LICENSE"
    source_license.write_text(license_text)
    (project / "licenses").mkdir()
    (project / "licenses/SOURCE-MIT.txt").write_text(license_text)
    sources = yaml.safe_load((project / "config/sources.yaml").read_text())
    sources["sources"]["blackmatrix7"]["license_file"] = "licenses/SOURCE-MIT.txt"
    (project / "config/sources.yaml").write_text(yaml.safe_dump(sources, sort_keys=False))
    source_ref = {"source_id": "blackmatrix7", "file": "rule/Surge/Demo/Demo.list", "format": "surge", "adapter_version": 1, "include_selector_sha256": approval_scope_sha256([]), "file_declarations": file_legal_declarations(source_root, ["rule/Surge/Demo/Demo.list"])}
    ledger = {
        "schema_version": 1,
        "sources": {"blackmatrix7": {"repository": "fixture", "license": "MIT", "license_file": "licenses/SOURCE-MIT.txt", "license_sha256": hashlib.sha256(license_text.encode()).hexdigest(), "license_scope": "test fixture", "evidence": ["test fixture"]}},
        "source_profiles": [{"profile_id": "fixture-demo", "category": "demo", "selected_sources": [source_ref], "review_status": "approved", "reviewer": "test", "reviewed_at": "2026-10-11", "coverage_status": "partial", "merge_policy": "single-selected-source"}],
        "approved_exclusions": [], "unsupported_dispositions": [],
        "shared_scope_baseline": {"excluded_exact_rules": [], "service_exact_additions": {}, "broad_shared_suffixes_prohibited": [], "recognized_shared_roots": []},
    }
    ledger["source_profiles"][0]["shared_scope_sha256"] = approval_scope_sha256(shared_scope_for_category(ledger, "demo", []))
    (project / "config/source-approvals.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    settings = yaml.safe_load((project / "config/settings.yaml").read_text())
    settings["target_semantics_verified"] = True
    settings["deployment_enabled"] = True
    (project / "config/settings.yaml").write_text(yaml.safe_dump(settings, sort_keys=False))
    result = Builder(project, source_root).build(tmp_path / "build")
    assert result.deployment_allowed
    releases = tmp_path / "data/releases"
    releases.mkdir(parents=True)
    shutil.copytree(result.output_dir, releases / result.release_id)
    return releases / result.release_id


def _setup(tmp_path, *, primary_failure=None, backup_rollback_failure=False, enabled=True):
    release = _verified_release(tmp_path)
    enabled_config, specs = load_target_specs(__import__("pathlib").Path(__file__).resolve().parents[1] / "config/targets-v4.yaml")
    assert enabled_config is False
    data_root = tmp_path / "data"
    store = Store(data_root / "controller.sqlite3")
    events = []
    backup = SimulatedTarget("nas-backup", current="a" * 64, fail_rollback=backup_rollback_failure, events=events)
    primary = SimulatedTarget("vm104-main", current="b" * 64, fail_at=primary_failure, events=events)
    coordinator = DeploymentCoordinator(store, data_root, specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=enabled)
    coordinator.prepare(release.name, _rid("preflight"))
    return release, store, specs, backup, primary, coordinator, events


def _rid(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dns-rule-builder-test:{value}"))


def test_schema4_roles_dynamic_provider_and_exact_rules(tmp_path):
    release = _verified_release(tmp_path)
    _, specs = load_target_specs(__import__("pathlib").Path(__file__).resolve().parents[1] / "config/targets-v4.yaml")
    backup = target_manifest(release, next(spec for spec in specs if spec.role == "backup"))
    primary = target_manifest(release, next(spec for spec in specs if spec.role == "primary"))
    assert backup["schema_version"] == primary["schema_version"] == 4
    assert backup["services"] == ["smartdns"]
    assert backup["mihomo_rule_order"] == []
    assert backup["provider_map"] == []
    assert backup["provider_policy_map"] == {}
    assert backup["regional_groups"] == []
    assert not any(path.startswith("mihomo/") for path in backup["files"])
    assert any(path.startswith("mihomo/providers/") for path in primary["files"])
    assert primary["target_id"] == "vm104-main"


def test_maintenance_switch_overrides_legacy_switch_and_missing_field_preserves_recovery(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "config/targets-v4.yaml"
    config = yaml.safe_load(source.read_text())
    config["deployment_enabled"] = True
    config.pop("maintenance_disabled", None)
    path = tmp_path / "targets.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    # Legacy true remains available to resume already-persisted tasks.
    assert load_target_specs(path)[0] is True
    config["maintenance_disabled"] = True
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert load_target_specs(path)[0] is False
    config["maintenance_disabled"] = False
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert load_target_specs(path)[0] is True


def test_deployment_disabled_and_stage_validate_both_before_activation(tmp_path):
    release, store, specs, backup, primary, coordinator, events = _setup(tmp_path, enabled=False)
    with pytest.raises(TargetV4Error, match="master switch"):
        coordinator.run(release.name, _rid("disabled"))
    enabled = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=True)
    state = enabled.run(release.name, _rid("success"))
    assert state["phase"] == "succeeded"
    assert [phase for _target, phase in events] == ["stage", "stage", "validate", "validate", "validate", "validate", "activate", "activate"]
    assert [target for target, phase in events if phase == "activate"] == ["nas-backup", "vm104-main"]


def test_manual_rollback_uses_readback_and_persists_previous_release(tmp_path):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    request_id = _rid("manual-rollback")
    state = coordinator.run(release.name, request_id)
    assert state["phase"] == "succeeded"
    rolled_back = coordinator.rollback_active(request_id)
    assert rolled_back["phase"] == "failed_rolled_back"
    assert backup.current == "a" * 64
    assert primary.current == "b" * 64
    assert store.get(f"deployment:{request_id}")["phase"] == "failed_rolled_back"
    with pytest.raises(TargetV4Error, match="successful or interrupted"):
        coordinator.rollback_active(request_id)


def test_manual_rollback_freezes_when_actual_target_drifted(tmp_path):
    release, store, _specs, backup, primary, coordinator, _events = _setup(tmp_path)
    request_id = _rid("manual-rollback-drift")
    coordinator.run(release.name, request_id)
    primary.current = "c" * 64
    with pytest.raises(TargetV4Error, match="manual inspection"):
        coordinator.rollback_active(request_id)
    state = store.get(f"deployment:{request_id}")
    assert state["phase"] == "manual_intervention"
    assert state["observed"]["vm104-main"] == "c" * 64
    assert backup.current == release.name


def test_main_activation_failure_rolls_back_backup(tmp_path):
    release, _store, _specs, backup, primary, coordinator, _events = _setup(tmp_path, primary_failure="activate")
    with pytest.raises(TargetV4Error, match="failed_rolled_back"):
        coordinator.run(release.name, _rid("rollback"))
    assert backup.current == "a" * 64
    assert primary.current == "b" * 64
    assert "rollback" in backup.calls


def test_failed_rollback_is_persisted_as_manual_recovery_required(tmp_path):
    release, store, _specs, backup, _primary, coordinator, _events = _setup(tmp_path, primary_failure="activate", backup_rollback_failure=True)
    with pytest.raises(TargetV4Error, match="rollback_failed"):
        coordinator.run(release.name, _rid("rollback-failed"))
    assert store.get(f"deployment:{_rid('rollback-failed')}")["phase"] == "rollback_failed"
    assert store.get(f"deployment:{_rid('rollback-failed')}")["rollback_errors"]
    assert backup.current == release.name
    activations = backup.calls.count("activate")
    with pytest.raises(TargetV4Error, match="terminal"):
        coordinator.run(release.name, _rid("rollback-failed"))
    assert backup.calls.count("activate") == activations


def test_resume_after_backup_activation_skips_duplicate_activation(tmp_path):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    original_activate = backup.activate

    def crash_after_backup_activation(request_id, envelope):
        original_activate(request_id, envelope)
        state = store.get(f"deployment:{_rid('resume')}")
        state["completed_activation"] = ["nas-backup"]
        state["phase"] = "mainactivate"
        store.put(f"deployment:{_rid('resume')}", state)
        raise KeyboardInterrupt()

    backup.activate = crash_after_backup_activation
    with pytest.raises(KeyboardInterrupt):
        coordinator.run(release.name, _rid("resume"))
    backup.activate = original_activate
    resumed = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=True)
    state = resumed.run(release.name, _rid("resume"))
    assert state["phase"] == "succeeded"
    assert backup.calls.count("activate") == 1
    assert primary.current == release.name


def test_resume_persisted_rollback_after_process_restart_does_not_activate_again(tmp_path):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path, primary_failure="activate")
    original_rollback = backup.rollback

    def crash_after_rollback_intent(request_id, previous):
        original_rollback(request_id, previous)
        raise KeyboardInterrupt()

    backup.rollback = crash_after_rollback_intent
    with pytest.raises(KeyboardInterrupt):
        coordinator.run(release.name, _rid("resume-rollback"))
    assert store.get(f"deployment:{_rid('resume-rollback')}")["phase"] == "rolling_back"
    backup.rollback = original_rollback
    resumed = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=False)
    state = resumed.run(release.name, _rid("resume-rollback"))
    assert state["phase"] == "failed_rolled_back"
    assert backup.current == "a" * 64
    assert backup.calls.count("activate") == 1
    assert primary.calls.count("activate") == 1


def test_partial_primary_activation_is_detected_and_rolled_back(tmp_path):
    release, _store, _specs, backup, primary, coordinator, _events = _setup(tmp_path)
    original_activate = primary.activate

    def partial_activate(request_id, envelope):
        original_activate(request_id, envelope)
        raise RuntimeError("connection dropped after activation")

    primary.activate = partial_activate
    with pytest.raises(TargetV4Error, match="failed_rolled_back"):
        coordinator.run(release.name, _rid("partial-main"))
    assert backup.current == "a" * 64
    assert primary.current == "b" * 64
    assert "rollback" in primary.calls


def test_unknown_target_readback_requires_manual_intervention(tmp_path):
    release, store, _specs, backup, _primary, coordinator, _events = _setup(tmp_path, primary_failure="activate")
    original_current = backup.current_release
    count = 0

    def unavailable_readback():
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError("target is disconnected")
        return original_current()

    backup.current_release = unavailable_readback
    with pytest.raises(TargetV4Error, match="manual_intervention"):
        coordinator.run(release.name, _rid("unknown-readback"))
    state = store.get(f"deployment:{_rid('unknown-readback')}")
    assert state["phase"] == "manual_intervention"
    assert state["intent"]["status"] == "unknown"
    assert "rollback" not in backup.calls


def test_schema4_rejects_backup_mihomo_and_traversal(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "config/targets-v4.yaml"
    data = yaml.safe_load(source.read_text())
    data["targets"]["nas-backup"]["mihomo_provider_config"] = "../outside"
    data["targets"]["nas-backup"]["services"].append("mihomo")
    bad = tmp_path / "targets.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(TargetV4Error, match="backup role"):
        load_target_specs(bad)


def test_preparation_allowlist_rejects_malformed_extra_blocker():
    manifest = {
        "deployment_allowed": False,
        "deployment_blockers": [
            {"reason": "target SmartDNS/Mihomo semantics not verified"},
            {"reason": "deployment master switch is disabled"},
            "unexpected blocker",
        ],
        "review_required": False, "publish_allowed": True,
        "source_license_blockers": [], "publish_blockers": [],
    }
    with pytest.raises(AttestationError):
        check_preparation_candidate(manifest)


def test_preflight_evidence_must_still_match_before_activation(tmp_path):
    release, _store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    original_validate = backup.validate

    def changed_config(request_id, envelope):
        evidence = original_validate(request_id, envelope)
        evidence["config_sha256"] = {"simulated": "0" * 64}
        return evidence

    backup.validate = changed_config
    with pytest.raises(TargetV4Error, match="evidence changed"):
        coordinator.run(release.name, _rid("changed-preflight"))
    assert backup.current == "a" * 64
    assert primary.current == "b" * 64


@pytest.mark.parametrize("cause", ["expired_attestation", "maintenance_disabled"])
def test_interrupted_activation_intent_recovers_by_rollback(tmp_path, cause):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    request_id = _rid(f"interrupted-{cause}")
    prepared = coordinator.prepare(release.name, request_id)
    attestation = store.get(f"target-attestation:{release.name}")
    assert prepared["phase"] == "validated"
    previous = {"nas-backup": "a" * 64, "vm104-main": "b" * 64}
    backup.activate(request_id, prepared["envelopes"]["nas-backup"])
    store.put(f"deployment:{request_id}", {
        "request_id": request_id, "release_id": release.name, "phase": "backupactivate",
        "completed_activation": [], "previous": previous, "envelopes": prepared["envelopes"],
        "attestation_sha256": attestation["attestation_sha256"],
        "intent": {"operation": "activate", "target_id": "nas-backup"},
    })
    enabled = True
    if cause == "expired_attestation":
        attestation["expires_at"] = "2000-01-01T00:00:00Z"
        store.put(f"target-attestation:{release.name}", attestation)
    else:
        enabled = False
    recovery = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=enabled)
    state = recovery.run(release.name, request_id)
    assert state["phase"] == "failed_rolled_back"
    assert backup.current == previous["nas-backup"]
    assert primary.current == previous["vm104-main"]
    assert backup.calls.count("rollback") == 1
    assert primary.calls.count("activate") == 0


def test_interrupted_activation_with_unexpected_target_drift_requires_manual_intervention(tmp_path):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    request_id = _rid("unexpected-drift-recovery")
    prepared = coordinator.prepare(release.name, request_id)
    attestation = store.get(f"target-attestation:{release.name}")
    attestation["expires_at"] = "2000-01-01T00:00:00Z"
    store.put(f"target-attestation:{release.name}", attestation)
    backup.current = "c" * 64
    store.put(f"deployment:{request_id}", {
        "request_id": request_id, "release_id": release.name, "phase": "backupactivate",
        "completed_activation": [], "previous": {"nas-backup": "a" * 64, "vm104-main": "b" * 64},
        "envelopes": prepared["envelopes"], "attestation_sha256": attestation["attestation_sha256"],
        "intent": {"operation": "activate", "target_id": "nas-backup"},
    })
    state = coordinator.run(release.name, request_id)
    assert state["phase"] == "manual_intervention"
    assert backup.current == "c" * 64
    assert "rollback" not in backup.calls
