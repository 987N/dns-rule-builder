from __future__ import annotations

import json
import shutil

import pytest
import yaml

from dns_rule_builder.engine import Builder, BuildError
from dns_rule_builder.store import Store
from dns_rule_builder.target_v4 import DeploymentCoordinator, SimulatedTarget, TargetV4Error, load_target_specs, target_manifest


def _verified_release(tmp_path):
    from test_engine import make_project, SOURCE_REVISION

    project, source_root = make_project(tmp_path / "project")
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
    return release, store, specs, backup, primary, coordinator, events


def test_schema4_roles_dynamic_provider_and_exact_rules(tmp_path):
    release = _verified_release(tmp_path)
    _, specs = load_target_specs(__import__("pathlib").Path(__file__).resolve().parents[1] / "config/targets-v4.yaml")
    backup = target_manifest(release, next(spec for spec in specs if spec.role == "backup"))
    primary = target_manifest(release, next(spec for spec in specs if spec.role == "primary"))
    assert backup["schema_version"] == primary["schema_version"] == 4
    assert backup["services"] == ["smartdns"]
    assert not any(path.startswith("mihomo/") for path in backup["files"])
    assert any(path.startswith("mihomo/providers/") for path in primary["files"])
    assert primary["target_id"] == "vm104-main"


def test_deployment_disabled_and_stage_validate_both_before_activation(tmp_path):
    release, store, specs, backup, primary, coordinator, events = _setup(tmp_path, enabled=False)
    with pytest.raises(TargetV4Error, match="master switch"):
        coordinator.run(release.name, "disabled")
    enabled = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=True)
    state = enabled.run(release.name, "success")
    assert state["phase"] == "succeeded"
    assert [phase for _target, phase in events] == ["stage", "stage", "validate", "validate", "activate", "activate"]
    assert [target for target, phase in events if phase == "activate"] == ["nas-backup", "vm104-main"]


def test_main_activation_failure_rolls_back_backup(tmp_path):
    release, _store, _specs, backup, primary, coordinator, _events = _setup(tmp_path, primary_failure="activate")
    with pytest.raises(TargetV4Error, match="failed_rolled_back"):
        coordinator.run(release.name, "rollback")
    assert backup.current == "a" * 64
    assert primary.current == "b" * 64
    assert "rollback" in backup.calls


def test_failed_rollback_is_persisted_as_manual_recovery_required(tmp_path):
    release, store, _specs, backup, _primary, coordinator, _events = _setup(tmp_path, primary_failure="activate", backup_rollback_failure=True)
    with pytest.raises(TargetV4Error, match="rollback_failed"):
        coordinator.run(release.name, "rollback-failed")
    assert store.get("deployment:rollback-failed")["phase"] == "rollback_failed"
    assert store.get("deployment:rollback-failed")["rollback_errors"]
    assert backup.current == release.name
    activations = backup.calls.count("activate")
    with pytest.raises(TargetV4Error, match="terminal"):
        coordinator.run(release.name, "rollback-failed")
    assert backup.calls.count("activate") == activations


def test_resume_after_backup_activation_skips_duplicate_activation(tmp_path):
    release, store, specs, backup, primary, coordinator, _events = _setup(tmp_path)
    original_activate = backup.activate

    def crash_after_backup_activation(request_id, envelope):
        original_activate(request_id, envelope)
        state = store.get("deployment:resume")
        state["completed_activation"] = ["nas-backup"]
        state["phase"] = "mainactivate"
        store.put("deployment:resume", state)
        raise KeyboardInterrupt()

    backup.activate = crash_after_backup_activation
    with pytest.raises(KeyboardInterrupt):
        coordinator.run(release.name, "resume")
    backup.activate = original_activate
    resumed = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=True)
    state = resumed.run(release.name, "resume")
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
        coordinator.run(release.name, "resume-rollback")
    assert store.get("deployment:resume-rollback")["phase"] == "rolling_back"
    backup.rollback = original_rollback
    resumed = DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=False)
    state = resumed.run(release.name, "resume-rollback")
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
        coordinator.run(release.name, "partial-main")
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
        coordinator.run(release.name, "unknown-readback")
    state = store.get("deployment:unknown-readback")
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
