from copy import deepcopy
from pathlib import Path
import shutil
import uuid

import pytest
import yaml

from dns_rule_builder.controller import TaskRunner
from dns_rule_builder.deployment_policy import automatic_enabled, compare_releases
from dns_rule_builder.engine import Builder, verify_manifest
from dns_rule_builder.target_v4 import DeploymentCoordinator, SimulatedTarget, load_target_specs


PROJECT = Path(__file__).resolve().parents[1]


def manifest(count=1000, categories=10):
    return {"release_id": "a" * 64, "deployment_allowed": True, "deployment_blockers": [],
            "statistics": {"rule_count": count}, "category_status": [{"category": str(i), "priority": i, "policy_group": "Proxy"} for i in range(categories)],
            "identity_inputs": {"normalized_rules": [rule(i, str(i % categories)) for i in range(count)],
                                "code_identity": {"engine.py": "a"}, "config_identity": {"config/categories.yaml": "a", "config/sources.yaml": "a"},
                                "sources": {"upstream": {"repository": "fixture", "revision": "a", "kind": "remote", "adapter_version": 1, "selected_refs": []}},
                                "automatic_policy_inputs": {"force_direct": [], "source_configuration": {}}, "source_capability_version": 1,
                                "compiler_capability_version": 2, "license_capability_version": 1, "builder_version": "1",
                                "source_approvals_sha256": "a", "approved_exclusions_sha256": "a",
                                "shared_domain_reviews_sha256": "a", "unsupported_dispositions_sha256": "a"}}


def rule(i, category="0"):
    return {"domain": f"d{i}.example.net", "match_type": "EXACT", "category": category, "policy_group": "Proxy", "priority": 10}


def changed(left, additions=0, category_count=10):
    right = deepcopy(left)
    right["release_id"] = "b" * 64
    right["identity_inputs"]["normalized_rules"].extend(rule(10000+i, str(i % category_count)) for i in range(additions))
    return right


@pytest.mark.parametrize("count,added,expected", [(1000,100,"auto_allowed"),(1000,101,"approval_required"),
                                                 (100,50,"auto_allowed"),(100,51,"approval_required")])
def test_global_threshold_strict_percent_and_absolute(count, added, expected):
    left = manifest(count)
    assert compare_releases(left, changed(left, added))["decision"] == expected


@pytest.mark.parametrize("count,added,expected", [(70,21,"auto_allowed"),(70,22,"approval_required"),
                                                 (10,20,"auto_allowed"),(10,21,"approval_required")])
def test_category_threshold_strict_percent_and_absolute(count, added, expected):
    left = manifest(count, 1)
    assert compare_releases(left, changed(left, added, 1))["decision"] == expected


def test_counts_use_added_plus_removed_and_not_net_or_display_limit():
    left = manifest()
    right = changed(left, 60)
    del right["identity_inputs"]["normalized_rules"][:60]
    result = compare_releases(left, right)
    assert result["rule_count_delta"] == 0
    assert result["changed_count"] == 120 and result["decision"] == "approval_required"
    assert len(result["added_rules"]) == 50 and result["truncated"]["added_rules"]


@pytest.mark.parametrize("field,value", [("code_identity", {}), ("compiler_capability_version", 3),
    ("automatic_policy_inputs", {"force_direct": ["example.net"], "source_configuration": {}}), ("source_approvals_sha256", "new"),
    ("approved_exclusions_sha256", "new"), ("shared_domain_reviews_sha256", "new"), ("unsupported_dispositions_sha256", "new")])
def test_structural_changes_require_approval(field, value):
    left = manifest()
    right = changed(left, 1)
    right["identity_inputs"][field] = value
    assert compare_releases(left, right)["decision"] == "approval_required"


def test_regular_revision_custom_domains_and_notes_are_routine_but_config_and_sources_are_not():
    left = manifest()
    right = changed(left, 1)
    right["identity_inputs"]["sources"]["upstream"]["revision"] = "new"
    right["identity_inputs"]["config_identity"]["config/sources.yaml"] = "new"
    right["identity_inputs"]["custom_rules_sha256"] = "new"
    assert compare_releases(left, right)["decision"] == "auto_allowed"
    for key in ("repository", "selected_refs", "adapter_version"):
        other = deepcopy(right)
        other["identity_inputs"]["sources"]["upstream"][key] = "different"
        assert compare_releases(left, other)["decision"] == "approval_required"
    right["identity_inputs"]["config_identity"]["config/categories.yaml"] = "new"
    assert compare_releases(left, right)["decision"] == "approval_required"


def test_missing_baseline_old_manifest_and_hard_gates_block_automation():
    left = manifest()
    right = changed(left)
    assert compare_releases(None, right)["decision"] == "blocked"
    del left["identity_inputs"]["automatic_policy_inputs"]
    assert compare_releases(left, right)["decision"] == "blocked"
    right.update(deployment_allowed=False, deployment_blockers=[{"reason": "unresolved conflict"}])
    assert compare_releases(manifest(), right)["decision"] == "blocked"
    assert compare_releases(manifest(), manifest())["unchanged"]


def test_switch_requires_explicit_auto_and_maintenance_flags(tmp_path):
    data = yaml.safe_load((PROJECT / "config/targets-v4.yaml").read_text())
    path = tmp_path / "targets.yaml"
    for fields, enabled in [({"deployment_enabled": True}, False),
                            ({"maintenance_disabled": False}, False),
                            ({"maintenance_disabled": False, "automatic_deployment_enabled": True}, True),
                            ({"maintenance_disabled": True, "automatic_deployment_enabled": True}, False),
                            ({"maintenance_disabled": False, "automatic_deployment_enabled": "true"}, False)]:
        path.write_text(yaml.safe_dump({**data, **fields}))
        assert automatic_enabled(path) is enabled


def setup_application(tmp_path, *, primary_failure=None):
    from test_target_v4 import _verified_release
    from dns_rule_builder.store import Store

    baseline = _verified_release(tmp_path)
    project = tmp_path / "project"
    source = project / ".cache/source"
    (source / "rule/Surge/Demo/Demo.list").write_text("DOMAIN-SUFFIX,example.com\nDOMAIN,added.example.net\n")
    candidate = Builder(project, source).build(tmp_path / "next-build")
    release = baseline.parent / candidate.release_id
    shutil.copytree(candidate.output_dir, release)
    assert compare_releases(verify_manifest(baseline), verify_manifest(release))["decision"] == "auto_allowed"
    store = Store(tmp_path / "data/controller.sqlite3")
    _, specs = load_target_specs(PROJECT / "config/targets-v4.yaml")
    events = []
    backup = SimulatedTarget("nas-backup", current=baseline.name, events=events)
    primary = SimulatedTarget("vm104-main", current=baseline.name, fail_at=primary_failure, events=events)
    switches = {"automatic": True, "maintenance": False}

    def factory():
        return DeploymentCoordinator(store, tmp_path / "data", specs, {"nas-backup": backup, "vm104-main": primary}, deployment_enabled=not switches["maintenance"])

    runner = TaskRunner(store, project, tmp_path / "data", lambda: None, start_thread=False, deployment_factory=factory,
                        automatic_enabled=lambda: switches["automatic"] and not switches["maintenance"])
    return runner, baseline, release, backup, primary, switches, events


def application_task(runner, release, kind="build", **extra):
    payload = {"phase": "applying", "release_id": release.name, "prepare_request_id": str(uuid.uuid4()), **extra}
    if kind == "generate_prepare":
        payload["intent"] = "generate_and_apply"
    return runner.store.create_task(kind, payload)


@pytest.mark.parametrize("kind", ["build", "generate_prepare"])
def test_both_entrypoints_auto_apply_and_repeat_is_noop(tmp_path, kind):
    runner, baseline, release, backup, primary, _, events = setup_application(tmp_path)
    task_id = application_task(runner, release, kind)
    runner.run_task(task_id)
    task = runner.store.task(task_id)
    assert task["status"] == "succeeded", task["message"]
    assert task["payload"]["application_result"]["phase"] == "auto_deployed"
    assert backup.current == primary.current == release.name
    assert [e for e in events if e[1] == "activate"] == [("nas-backup", "activate"), ("vm104-main", "activate")]
    auth = task["payload"]["automatic_authorization"]
    assert auth["baseline_release_id"] == baseline.name and auth["manifest_sha256"] and auth["attestation_sha256"]
    second = application_task(runner, release, kind)
    runner.run_task(second)
    assert runner.store.task(second)["payload"]["application_result"]["phase"] == "unchanged"
    assert len([e for e in events if e[1] == "activate"]) == 2


def test_main_failure_rolls_backup_back(tmp_path):
    runner, baseline, release, backup, primary, _, _ = setup_application(tmp_path, primary_failure="activate")
    task_id = application_task(runner, release)
    runner.run_task(task_id)
    task = runner.store.task(task_id)
    assert task["status"] == "failed", task["message"]
    assert backup.current == primary.current == baseline.name
    assert runner.store.get("deployment:" + task["payload"]["prepare_request_id"])["phase"] == "failed_rolled_back"


@pytest.mark.parametrize("failure", ["baseline", "maintenance", "expired", "unknown", "mismatch", "missing"])
def test_unsafe_changes_never_activate(tmp_path, failure):
    runner, baseline, release, backup, primary, switches, events = setup_application(tmp_path)
    original = runner.deployment_factory
    if failure in {"unknown", "mismatch"}:
        primary.current = None if failure == "unknown" else "f" * 64
    if failure == "missing":
        shutil.rmtree(baseline)

    def factory():
        coordinator = original()
        prepare = coordinator.prepare
        def wrapped(release_id, request_id):
            result = prepare(release_id, request_id)
            if failure == "baseline":
                backup.current = primary.current = "f" * 64
            elif failure == "maintenance":
                switches["maintenance"] = True
            elif failure == "expired":
                att = runner.store.get(f"target-attestation:{release_id}")
                att["expires_at"] = "2000-01-01T00:00:00Z"
                runner.store.put(f"target-attestation:{release_id}", att)
            return result
        coordinator.prepare = wrapped
        return coordinator
    runner.deployment_factory = factory
    task_id = application_task(runner, release)
    runner.run_task(task_id)
    assert not any(event[1] == "activate" for event in events)
    assert runner.store.task(task_id)["status"] == "blocked"


def test_disabled_automation_prepares_for_manual_approval(tmp_path):
    runner, baseline, release, backup, primary, switches, events = setup_application(tmp_path)
    switches["automatic"] = False
    task_id = application_task(runner, release)
    runner.run_task(task_id)
    result = runner.store.task(task_id)["payload"]["application_result"]
    assert result["phase"] == "approval_required"
    assert backup.current == primary.current == baseline.name
    assert not any(event[1] == "activate" for event in events)


class PowerLoss(BaseException):
    pass


@pytest.mark.parametrize("disable_after_restart", [False, True])
def test_restart_after_backup_activation_resumes_or_rolls_back(tmp_path, disable_after_restart):
    runner, baseline, release, backup, primary, switches, events = setup_application(tmp_path)
    original = primary.activate
    def interrupt(*_args):
        raise PowerLoss()
    primary.activate = interrupt
    task_id = application_task(runner, release)
    with pytest.raises(PowerLoss):
        runner.run_task(task_id)
    assert backup.current == release.name and primary.current == baseline.name
    primary.activate = original
    switches["automatic"] = not disable_after_restart
    runner.store.recover_interrupted()
    runner.run_task(task_id)
    task = runner.store.task(task_id)
    if disable_after_restart:
        assert backup.current == primary.current == baseline.name
        assert task["status"] == "failed"
    else:
        assert backup.current == primary.current == release.name
        assert task["status"] == "succeeded", task["message"]
    assert len([e for e in events if e == ("nas-backup", "activate")]) == 1


def test_daily_build_continues_apply_once_and_recovers_without_rebuilding(tmp_path):
    runner, baseline, release, backup, primary, _, _ = setup_application(tmp_path)
    builds = []
    def build(task_id, payload):
        builds.append(task_id)
        return {"release_id": release.name}
    runner._build = build
    task_id = runner.store.create_task("build", {"intent": "scheduled_apply", "daily": True, "schedule_date": "2026-10-14", "data_commit": "d" * 40})
    assert runner.store.create_task("build", {"daily": True, "schedule_date": "2026-10-14"}) == task_id
    activate = primary.activate
    primary.activate = lambda *_args: (_ for _ in ()).throw(PowerLoss())
    with pytest.raises(PowerLoss):
        runner.run_task(task_id)
    primary.activate = activate
    runner.store.recover_interrupted()
    assert runner.store.task(task_id)["status"] == "queued"
    runner.run_task(task_id)
    assert builds == [task_id]
    assert runner.store.task(task_id)["payload"]["application_result"]["phase"] == "auto_deployed"
    assert backup.current == primary.current == release.name


@pytest.mark.parametrize("major", [False, True])
def test_web_generate_applies_routine_and_waits_for_major_approval(tmp_path, major):
    from fastapi.testclient import TestClient
    from test_controller_web import secrets_for, custom_state
    from dns_rule_builder.web import Settings, create_app

    runner, baseline, release, backup, primary, _, events = setup_application(tmp_path)
    project = tmp_path / "project"
    shutil.copytree(PROJECT / "static", project / "static")
    shutil.copytree(PROJECT / "templates", project / "templates")
    if major:
        categories_path = project / "config/categories.yaml"
        categories = yaml.safe_load(categories_path.read_text())
        categories["categories"]["demo"]["priority"] += 1
        categories_path.write_text(yaml.safe_dump(categories))
        candidate = Builder(project, project / ".cache/source").build(tmp_path / "major-build")
        release = baseline.parent / candidate.release_id
        shutil.copytree(candidate.output_dir, release)
    token, password, session = secrets_for(tmp_path)
    config = yaml.safe_load((PROJECT / "config/targets-v4.yaml").read_text())
    config.update(maintenance_disabled=False, automatic_deployment_enabled=True)
    path = tmp_path / "targets.yaml"
    path.write_text(yaml.safe_dump(config))
    app = create_app(Settings(project, tmp_path / "data", "fixture", "fixture", token, password, session, False, path),
                     client_factory=lambda: None, start_worker=False, deployment_factory=runner.deployment_factory)
    for item in (baseline, release):
        app.state.store.add_release(item.name, "d" * 40, "e" * 40, item, {"manifest": verify_manifest(item)})
    app.state.store.put("draft", {"state": custom_state(), "base_sha": "blob", "dirty": True})
    builds = []
    app.state.runner._save = lambda payload: {"commit": "d" * 40}
    def build(task_id, payload):
        builds.append(payload["data_commit"])
        return {"release_id": release.name}
    app.state.runner._build = build
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]}
        response = client.post("/api/deployment/generate", json={}, headers=headers)
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        app.state.runner.run_task(task_id)
        task = app.state.store.task(task_id)
        assert task["status"] == "succeeded", task["message"]
        result = task["payload"]["application_result"]
        assert builds == ["d" * 40]
        if major:
            assert result["phase"] == "approval_required"
            assert result["deployment_decision"]["decision"] == "approval_required"
            assert backup.current == primary.current == baseline.name
            assert not any(e[1] == "activate" for e in events)
            approval = client.post("/api/deployment/manual", json={"request_id": result["request_id"]}, headers=headers)
            assert approval.status_code == 202, approval.text
            repeated = client.post("/api/deployment/manual", json={"request_id": result["request_id"]}, headers=headers)
            assert repeated.json()["task_id"] == approval.json()["task_id"]
            app.state.runner.run_task(approval.json()["task_id"])
            assert app.state.store.task(approval.json()["task_id"])["status"] == "succeeded"
        else:
            assert result["phase"] == "auto_deployed"
        assert backup.current == primary.current == release.name


def test_automatic_authorization_cannot_be_replaced_after_evidence_changes(tmp_path):
    runner, baseline, release, backup, primary, _, events = setup_application(tmp_path)
    original = runner.deployment_factory
    task_id = application_task(runner, release)
    request_id = runner.store.task(task_id)["payload"]["prepare_request_id"]
    coordinator = original()
    coordinator.prepare(release.name, request_id)
    runner.store.update_task_payload(task_id, {**runner.store.task(task_id)["payload"], "automatic_authorization": {
        "baseline_release_id": baseline.name, "policy_version": 1, "manifest_sha256": "wrong", "attestation_sha256": "wrong"}})
    runner.run_task(task_id)
    assert runner.store.task(task_id)["status"] == "blocked"
    assert backup.current == primary.current == baseline.name
    assert not any(e[1] == "activate" for e in events)


def test_daily_scheduler_keeps_0417_shanghai_and_enqueues_apply_once(tmp_path, monkeypatch):
    from datetime import datetime
    import dns_rule_builder.controller as controller

    runner, _, _, _, _, _, _ = setup_application(tmp_path)
    runner.store.put("synced", {"commit": "d" * 40})
    class Clock(datetime):
        minute = 16
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 14, 4, cls.minute, tzinfo=tz)
    monkeypatch.setattr(controller, "datetime", Clock)
    runner._schedule_daily()
    assert not runner.store.tasks()
    Clock.minute = 17
    runner._schedule_daily()
    runner._schedule_daily()
    tasks = runner.store.tasks()
    assert len(tasks) == 1
    assert tasks[0]["payload"]["intent"] == "scheduled_apply"
    assert tasks[0]["payload"]["data_commit"] == "d" * 40


def test_unknown_target_transport_blocks_before_preparation(tmp_path):
    runner, _, release, backup, primary, _, events = setup_application(tmp_path)
    def fail():
        raise RuntimeError("unreachable fixture")
    primary.current_release = fail
    task_id = application_task(runner, release)
    runner.run_task(task_id)
    assert runner.store.task(task_id)["status"] == "blocked"
    assert not any(e[1] in {"stage", "activate"} for e in events)


def test_real_manifest_revision_only_update_remains_routine(tmp_path):
    runner, baseline, _, _, _, _, _ = setup_application(tmp_path)
    project = tmp_path / "project"
    source = project / ".cache/source"
    # Keep the baseline domains, but update the source's pinned revision/marker.
    (source / "rule/Surge/Demo/Demo.list").write_text("DOMAIN-SUFFIX,example.com\n")
    path = project / "config/sources.yaml"
    config = yaml.safe_load(path.read_text())
    config["sources"]["blackmatrix7"]["revision"] = "c" * 40
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    (source / ".source-revision").write_text("c" * 40)
    result = Builder(project, source).build(tmp_path / "revision-build")
    decision = compare_releases(verify_manifest(baseline), verify_manifest(result.output_dir))
    assert decision["decision"] == "auto_allowed", decision["reasons"]
    assert decision["changed_count"] == 0
    assert decision["unchanged"] and decision["content_unchanged"]


def test_content_identical_release_skips_restart_but_code_changes_still_need_approval():
    left = manifest()
    left["artifact_sha256"] = {name: "a" * 64 for name in ("smartdns/proxy-domains.txt", "mihomo/rule-providers.yaml", "mihomo/rules.yaml", "mihomo/providers/Demo.yaml")}
    right = changed(left)
    right["code_commit"] = "new-doc-commit"
    decision = compare_releases(left, right)
    assert decision["unchanged"] and decision["content_unchanged"]
    right["identity_inputs"]["code_identity"] = {"engine.py": "different"}
    decision = compare_releases(left, right)
    assert decision["decision"] == "approval_required" and not decision["unchanged"]


@pytest.mark.parametrize("kind", ["prepare_deploy", "manual_deploy"])
def test_existing_hard_gate_cannot_be_bypassed_by_manual_paths(tmp_path, kind):
    runner, baseline, _, backup, primary, _, events = setup_application(tmp_path)
    project = tmp_path / "project"
    source = project / ".cache/source"
    (source / "rule/Surge/Demo/Demo.list").write_text("DOMAIN-SUFFIX,example.com\nDOMAIN-REGEX,unreviewed\n")
    candidate = Builder(project, source).build(tmp_path / "blocked-build")
    release = baseline.parent / candidate.release_id
    shutil.copytree(candidate.output_dir, release)
    manifest = verify_manifest(release)
    assert manifest["review_required"] and not manifest["deployment_allowed"]
    task_id = runner.store.create_task(kind, {"release_id": release.name, "request_id": str(uuid.uuid4())})
    runner.run_task(task_id)
    assert runner.store.task(task_id)["status"] == "blocked"
    assert backup.current == primary.current == baseline.name
    assert not any(e[1] in {"stage", "activate"} for e in events)
