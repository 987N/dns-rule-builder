from __future__ import annotations

import base64
import hashlib
import json
import shutil
import zipfile
import uuid
from pathlib import Path

import yaml
import pytest
from fastapi.testclient import TestClient

from dns_rule_builder.controller import TaskRunner, validate_release_zip, validate_state
from dns_rule_builder.engine import Builder, BuildError
from dns_rule_builder.github import GitHubError
from dns_rule_builder.rules_state import apply_state
from dns_rule_builder.store import Store
from dns_rule_builder.web import Settings, create_app


PROJECT = Path(__file__).resolve().parents[1]
DATA_SHA = "d" * 40
CODE_SHA = "e" * 40
SOURCE_SHA = "8" * 40


def fixture_project(root: Path) -> Path:
    for directory in ("config", "src/dns_rule_builder", "templates", "static", "rules-data"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__="0.1.0"\n')
    shutil.copytree(PROJECT / "templates", root / "templates", dirs_exist_ok=True)
    shutil.copytree(PROJECT / "static", root / "static", dirs_exist_ok=True)
    (root / "config/categories.yaml").write_text("categories:\n  demo:\n    provider_name: Demo\n    policy_group: Proxy\n    priority: 10\n    enabled: true\n    source_file: Demo/Demo.list\n  foreign_general:\n    provider_name: Foreign-General\n    policy_group: Foreign-General\n    priority: 900\n    enabled: true\n    source_file: null\n")
    (root / "config/policies.yaml").write_text("policies:\n  Proxy: {}\n  Foreign-General: {}\n")
    shutil.copy(PROJECT / "config/settings.yaml", root / "config/settings.yaml")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/sources.yaml").write_text(yaml.safe_dump({"sources": {"blackmatrix7": {"repository": "fixture", "revision": SOURCE_SHA, "archive": "absent.tar.gz", "archive_sha256": None, "rules_path": "rule/Surge"}}}))
    source = root / ".cache/source/rule/Surge"
    source.mkdir(parents=True)
    (source / "Demo").mkdir()
    (source / "Demo/Demo.list").write_text("DOMAIN,telemetry.example.net\n")
    (root / ".cache/source/.source-revision").write_text(SOURCE_SHA + "\n")
    return root


def custom_state():
    return {"schema_version": 1, "categories": {"demo": {"enabled": False}}, "rules": [{"domain": "example.net", "match_type": "EXACT", "category": "foreign_general", "enabled": True, "note": "smoke fixture"}], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": []}


def write_candidate(root: Path, state: dict, build_purpose: str = "rules") -> bytes:
    state_path = root / "rules-data/state.yaml"
    state_path.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False))
    apply_state(root, state_path)
    candidate = Builder(root, root / ".cache/source", source_revision=SOURCE_SHA, data_revision=DATA_SHA, code_commit=CODE_SHA, input_snapshot_time_utc="2026-10-10T00:00:00Z", build_purpose=build_purpose).build(root / ".cache/build")
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in candidate.output_dir.rglob("*") if p.is_file()):
            archive.write(path, "rules/" + path.relative_to(candidate.output_dir).as_posix())
    return buf.getvalue()


class FakeGitHubService:
    """In-memory fake of the GitHub REST workflow and public Release endpoints."""
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.state_bytes = yaml.safe_dump({"schema_version": 1, "categories": {}, "rules": [], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": []}, allow_unicode=True, sort_keys=False).encode()
        self.state_sha = self._blob_sha(self.state_bytes)
        self.dispatches = []
        self.archive = None
        self.release = None
        self.run = None
        self.rules_head = DATA_SHA
        self.change_head_before_candidate_check = False
        self.archive_purpose = "rules"

    @staticmethod
    def _blob_sha(content):
        return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()

    def get_contents(self, path, ref):
        assert path == "rules-data/state.yaml" and ref in {"rules-data", DATA_SHA}
        return self.state_bytes, self.state_sha

    def get_branch_head(self, branch):
        return self.rules_head if branch == "rules-data" else CODE_SHA

    def update_contents(self, path, content, branch, expected_sha, message):
        if expected_sha != self.state_sha:
            raise GitHubError(409, "GitHub API failed (HTTP 409)")
        self.state_bytes, self.state_sha = content, self._blob_sha(content)
        return self.rules_head

    def dispatch_workflow(self, workflow, ref, data_commit, request_id, smoke_test=False):
        self.dispatches.append((workflow, ref, data_commit, request_id))
        self.archive = write_candidate(self.project_root, yaml.safe_load(self.state_bytes), self.archive_purpose)
        run_id = 17
        self.run = {"id": run_id, "display_title": f"DNS Rules {request_id}", "status": "completed", "conclusion": "success", "head_sha": CODE_SHA, "completed_at": "2026-10-10T01:00:00Z"}
        manifest = json.loads(zipfile.ZipFile(__import__("io").BytesIO(self.archive)).read("rules/manifest.json"))
        self.release = {"tag_name": "rules-" + manifest["release_id"], "draft": False, "assets": [{"id": 630974498, "name": "rules.zip", "browser_download_url": "https://github.com/987N/dns-rule-builder/releases/download/rules/rules.zip"}]}
        return 204

    def find_dispatch_run(self, request_id, branch):
        return self.run if self.run and self.run["display_title"].endswith(request_id) else None

    def get_workflow_run(self, run_id):
        return self.run

    def list_releases(self, limit=20):
        if self.change_head_before_candidate_check:
            self.rules_head = "f" * 40
        return [self.release] if self.release else []

    def download_public_release_asset(self, url, maximum_bytes=128 * 1024 * 1024, asset_id=None):
        assert url.startswith("https://github.com/987N/dns-rule-builder/releases/download/")
        assert asset_id == 630974498
        return self.archive


def secrets_for(tmp_path: Path):
    values = {"token": "fake-github-token-that-is-long", "password": "private-admin-password-123", "session": "session-signing-secret-123456789"}
    paths = []
    for name, value in values.items():
        path = tmp_path / f"{name}.secret"
        path.write_text(value)
        paths.append(path)
    return paths


def test_state_validation_accepts_editable_rule_and_rejects_duplicates_or_bad_domains(tmp_path):
    root = fixture_project(tmp_path / "project")
    good = custom_state()
    assert validate_state(good, root) is good
    bad = custom_state()
    bad["rules"].append(dict(bad["rules"][0]))
    try:
        validate_state(bad, root)
    except BuildError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("duplicate custom rule passed validation")
    bad = custom_state()
    bad["rules"][0]["domain"] = "Example.NET"
    try:
        validate_state(bad, root)
    except BuildError:
        pass
    else:
        raise AssertionError("noncanonical domain passed validation")


def test_idle_remote_check_records_branch_and_latest_rules_release_without_touching_draft(tmp_path):
    project = fixture_project(tmp_path / "project")
    fake = FakeGitHubService(project)
    fake.rules_head = "f" * 40
    fake.release = {"tag_name": "rules-" + "a" * 64, "draft": False, "body": "build_purpose=rules"}
    store = Store(tmp_path / "state.sqlite3")
    store.put("synced", {"commit": DATA_SHA})
    original_draft = {"state": custom_state(), "base_sha": "base", "dirty": True}
    store.put("draft", original_draft)
    runner = TaskRunner(store, project, tmp_path / "persistent", lambda: fake, start_thread=False)
    runner._check_remote_state()
    status = store.get("remote_status")
    assert status["status"] == "ok"
    assert status["data_commit"] == fake.rules_head
    assert status["remote_changed"] is True
    assert status["latest_release_tag"] == fake.release["tag_name"]
    assert store.get("draft") == original_draft


def test_idle_remote_check_reports_auth_status_without_exposing_error_body(tmp_path):
    project = fixture_project(tmp_path / "project")
    store = Store(tmp_path / "state.sqlite3")

    class Unauthorized(FakeGitHubService):
        def get_branch_head(self, branch):
            raise GitHubError(401, "private fake token response body")

    runner = TaskRunner(store, project, tmp_path / "persistent", lambda: Unauthorized(project), start_thread=False)
    runner._check_remote_state()
    status = store.get("remote_status")
    assert status["status"] == "error"
    assert "HTTP 401" in status["error"]
    assert "token" not in status["error"]
    assert runner._next_remote_check > __import__("time").monotonic()


def test_release_zip_extracts_actual_manifest_root_and_rejects_bad_hash(tmp_path):
    root = fixture_project(tmp_path / "project")
    archive = write_candidate(root, custom_state())
    release_id = json.loads(zipfile.ZipFile(__import__("io").BytesIO(archive)).read("rules/manifest.json"))["release_id"]
    rules_root, manifest = validate_release_zip(archive, tmp_path / ".incoming" / "job")
    assert rules_root.name == release_id
    assert manifest["release_id"] == release_id


def test_authenticated_web_save_ci_poll_and_candidate_download(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False)
    app = create_app(settings, client_factory=lambda: fake, start_worker=False)
    client = TestClient(app, follow_redirects=False)
    assert client.get("/").status_code == 303
    login = client.post("/login", json={"password": "private-admin-password-123"})
    assert login.status_code == 200
    csrf = login.json()["csrf"]
    state_response = client.get("/api/state")
    assert state_response.status_code == 200
    disabled_draft = custom_state()
    assert client.put("/api/draft", json={"state": disabled_draft}, headers={"X-CSRF-Token": csrf}).status_code == 200
    preview = client.post("/api/import-preview", json={"lines": "DOMAIN-SUFFIX,Example.org\nDOMAIN-SUFFIX,example.org\na..com\n192.0.2.1\n例子.测试\nx.local\ndemo.example,EXACT,demo"}, headers={"X-CSRF-Token": csrf})
    assert preview.status_code == 200
    assert preview.json()["ok"] is False
    assert preview.json()["rules"][0]["domain"] == "example.org"
    assert preview.json()["rules"][1]["domain"] == "xn--fsqu00a.xn--0zwm56d"
    assert len(preview.json()["errors"]) == 5
    assert "禁用" in preview.json()["errors"][-1]["error"]
    assert client.post("/api/import-preview", json=[], headers={"X-CSRF-Token": csrf}).status_code == 422
    state = custom_state()
    state["categories"]["demo"]["enabled"] = True
    draft = client.put("/api/draft", json={"state": state}, headers={"X-CSRF-Token": csrf})
    assert draft.json()["synced"] is False
    assert client.put("/api/draft", json={"state": state}).status_code == 403
    save = client.post("/api/save", json={}, headers={"X-CSRF-Token": csrf})
    assert save.status_code == 202
    app.state.runner.run_task(save.json()["task_id"])
    assert app.state.store.task(save.json()["task_id"])["status"] == "succeeded"
    assert app.state.store.get("draft")["dirty"] is False
    build = client.post("/api/build", json={}, headers={"X-CSRF-Token": csrf})
    assert build.status_code == 202
    app.state.runner.run_task(build.json()["task_id"])
    task = app.state.store.task(build.json()["task_id"])
    assert task["status"] == "succeeded", task["message"]
    assert len(fake.dispatches) == 1
    current = data_root / "candidate"
    assert current.is_symlink()
    assert current.resolve().name == json.loads((current.resolve() / "manifest.json").read_text())["release_id"]
    assert client.get("/api/releases").json()["releases"][0]["data_commit"] == DATA_SHA
    assert client.get(f"/api/report/{current.resolve().name}/statistics.json").status_code == 200
    excluded = client.post("/api/exclusions", json={"category": "demo", "domain": "telemetry.example.net", "match_type": "EXACT", "reviewer": "admin", "reason": "已人工核查共享统计域名"}, headers={"X-CSRF-Token": csrf})
    assert excluded.status_code == 200, excluded.text
    assert app.state.store.get("draft")["state"]["approved_exclusions"][0]["domain"] == "telemetry.example.net"
    assert app.state.store.get("draft")["dirty"] is True
    assert client.get("/api/explain?domain=example.net").status_code == 200
    assert client.get("/api/explain?domain=bad").status_code == 422
    assert client.get("/api/state").json()["candidate_release_id"] == current.resolve().name
    client.cookies.clear()
    assert client.get("/api/tasks").status_code == 401


def test_failed_release_download_keeps_existing_candidate_unchanged(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    app = create_app(Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    client.get("/api/state")
    state = custom_state()
    client.put("/api/draft", json={"state": state}, headers={"X-CSRF-Token": csrf})
    save = client.post("/api/save", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(save.json()["task_id"])
    first_build = client.post("/api/build", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(first_build.json()["task_id"])
    candidate = data_root / "candidate"
    old_target = candidate.resolve()
    assert candidate.is_symlink()

    def both_download_paths_fail(url, maximum_bytes=128 * 1024 * 1024, asset_id=None):
        raise GitHubError(404, "release download failed on browser and API endpoints (HTTP 0; HTTP 404)")

    fake.download_public_release_asset = both_download_paths_fail
    second_build = client.post("/api/build", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(second_build.json()["task_id"])
    assert app.state.store.task(second_build.json()["task_id"])["status"] == "failed"
    assert candidate.is_symlink()
    assert candidate.resolve() == old_target


def test_login_rate_limit_and_missing_github_credentials_are_clear(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    app = create_app(Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: (_ for _ in ()).throw(GitHubError(401, "token expired")), start_worker=False)
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/login", json={"password": "incorrect"}).status_code == 401
    assert client.post("/login", json={"password": "incorrect"}).status_code == 429
    # Use a fresh app instance for the credential error path; the first one's
    # deliberate login throttle remains intact for this peer.
    second_app = create_app(Settings(project, tmp_path / "other-persistent", "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: (_ for _ in ()).throw(GitHubError(401, "token expired")), start_worker=False)
    other = TestClient(second_app)
    csrf = other.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    response = other.get("/api/state")
    assert response.status_code == 503
    assert response.json()["unsynced"] is True
    assert "无法读取 GitHub 规则" in response.json()["error"]
    assert other.get("/api/state").status_code == 503
    assert other.post("/api/build", json={}, headers={"X-CSRF-Token": csrf}).status_code == 409


def test_restart_recovers_existing_workflow_uuid_without_red_dispatch(tmp_path):
    path = tmp_path / "persistent/controller.sqlite3"
    store = Store(path)
    request_id = "1d2552f0-bd48-4f25-9611-dd0a68cb1991"
    task_id = store.create_task("build", {"data_commit": DATA_SHA, "request_id": request_id, "code_commit": CODE_SHA, "phase": "polling"})
    store.update_task(task_id, "polling", "waiting")
    resumed = Store(path)
    resumed.recover_interrupted()
    recovered = resumed.task(task_id)
    assert recovered["status"] == "queued"
    assert recovered["payload"]["request_id"] == request_id
    assert recovered["payload"]["phase"] == "polling"


def test_save_rechecks_remote_hash_after_lost_write_response(tmp_path):
    project = fixture_project(tmp_path / "project")
    store = Store(tmp_path / "persistent/controller.sqlite3")
    class LostResponse(FakeGitHubService):
        writes = 0

        def update_contents(self, *args, **kwargs):
            self.writes += 1
            result = super().update_contents(*args, **kwargs)
            if self.writes == 1:
                raise GitHubError(0, "response lost after successful write")
            return result
    fake = LostResponse(project)
    state = custom_state()
    store.put("draft", {"state": state, "base_sha": fake.state_sha, "dirty": True})
    runner = TaskRunner(store, project, tmp_path / "persistent", lambda: fake, start_thread=False)
    result = runner._save({"state": state, "expected_sha": fake.state_sha})
    assert result["synced"] is True
    assert fake.writes == 1
    assert store.get("draft")["dirty"] is False


def test_outdated_completed_build_is_retained_but_not_promoted(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False)
    app = create_app(settings, client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    client.get("/api/state")
    state = custom_state()
    assert client.put("/api/draft", json={"state": state}, headers={"X-CSRF-Token": csrf}).status_code == 200
    save = client.post("/api/save", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(save.json()["task_id"])
    fake.change_head_before_candidate_check = True
    build = client.post("/api/build", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(build.json()["task_id"])
    assert app.state.store.task(build.json()["task_id"])["status"] == "succeeded"
    assert not (data_root / "candidate").exists()
    release = app.state.store.releases(1)[0]
    assert release["metadata"]["stale"] is True
    assert release["metadata"]["candidate"] is False


def test_normal_build_never_promotes_smoke_release_with_same_input_commits(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    fake.archive_purpose = "smoke"
    app = create_app(Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    client.get("/api/state")
    state = custom_state()
    client.put("/api/draft", json={"state": state}, headers={"X-CSRF-Token": csrf})
    save = client.post("/api/save", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(save.json()["task_id"])
    build = client.post("/api/build", json={}, headers={"X-CSRF-Token": csrf})
    app.state.runner.run_task(build.json()["task_id"])
    assert app.state.store.task(build.json()["task_id"])["status"] == "blocked"
    assert not (data_root / "candidate").exists()


def test_deployment_api_is_authenticated_csrf_protected_and_manual_switch_gated(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    target_config = tmp_path / "targets.yaml"
    target_data = yaml.safe_load((PROJECT / "config/targets-v4.yaml").read_text())
    target_data["deployment_enabled"] = False
    target_config.write_text(yaml.safe_dump(target_data, sort_keys=False))
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False, target_config)
    app = create_app(settings, client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    assert client.get("/api/deployment").status_code == 401
    login = client.post("/login", json={"password": "private-admin-password-123"})
    csrf = login.json()["csrf"]
    assert client.post("/api/deployment/prepare", json={"release_id": "a" * 64}).status_code == 403
    assert client.post("/api/deployment/manual", json={"request_id": str(uuid.uuid4())}, headers={"X-CSRF-Token": csrf}).status_code == 409
    assert "开关关闭" in client.post("/api/deployment/manual", json={"request_id": str(uuid.uuid4())}, headers={"X-CSRF-Token": csrf}).json()["error"]
    assert client.post("/api/deployment/prepare", json={"release_id": "bad"}, headers={"X-CSRF-Token": csrf}).status_code == 422
    assert app.state.store.tasks() == []


def test_prepare_deployment_requires_local_verified_release_and_persists_task(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    target_config = tmp_path / "targets.yaml"
    target_data = yaml.safe_load((PROJECT / "config/targets-v4.yaml").read_text())
    target_config.write_text(yaml.safe_dump(target_data, sort_keys=False))
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False, target_config)
    app = create_app(settings, client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    archive = write_candidate(project, custom_state())
    unpacked, manifest = validate_release_zip(archive, tmp_path / "incoming" / "job")
    releases = data_root / "releases"
    releases.mkdir(parents=True)
    destination = releases / manifest["release_id"]
    shutil.move(str(unpacked), destination)
    app.state.store.add_release(manifest["release_id"], DATA_SHA, CODE_SHA, destination, {"manifest": manifest})
    response = client.post("/api/deployment/prepare", json={"release_id": manifest["release_id"]}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    task = app.state.store.task(response.json()["task_id"])
    assert task["kind"] == "prepare_deploy" and task["status"] == "queued"
    assert task["payload"]["request_id"] == response.json()["request_id"]
    assert task["payload"]["release_id"] == manifest["release_id"]


def test_manual_deployment_requires_enabled_switch_and_fresh_matching_attestation(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    target_config = tmp_path / "targets.yaml"
    target_data = yaml.safe_load((PROJECT / "config/targets-v4.yaml").read_text())
    target_data["deployment_enabled"] = True
    target_config.write_text(yaml.safe_dump(target_data, sort_keys=False))
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False, target_config)
    app = create_app(settings, client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    request_id = str(uuid.uuid4())
    assert client.post("/api/deployment/manual", json={"request_id": request_id}, headers={"X-CSRF-Token": csrf}).status_code == 409

    archive = write_candidate(project, custom_state())
    unpacked, manifest = validate_release_zip(archive, tmp_path / "incoming" / "job")
    release_id = manifest["release_id"]
    releases = data_root / "releases"
    releases.mkdir(parents=True)
    destination = releases / release_id
    shutil.move(str(unpacked), destination)
    app.state.store.add_release(release_id, DATA_SHA, CODE_SHA, destination, {"manifest": manifest})
    app.state.store.put(f"target-preparation:{request_id}", {"request_id": request_id, "release_id": release_id, "phase": "validated"})
    app.state.store.put(f"target-attestation:{release_id}", {"request_id": request_id, "expires_at": "2099-01-01T00:00:00Z"})
    response = client.post("/api/deployment/manual", json={"request_id": request_id}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    task = app.state.store.task(response.json()["task_id"])
    assert task["kind"] == "manual_deploy"
    assert task["payload"]["intent"] == "human_manual_activation"
    assert task["payload"]["request_id"] == request_id


def test_deployment_task_recovers_from_sqlite_and_records_readback(tmp_path):
    project = fixture_project(tmp_path / "project")
    store = Store(tmp_path / "persistent/controller.sqlite3")
    request_id, release_id = str(uuid.uuid4()), "a" * 64
    task_id = store.create_task("prepare_deploy", {"release_id": release_id, "request_id": request_id, "intent": "prepare_only"})
    store.update_task(task_id, "running", "stopped during preparation")

    class Handler:
        def current_release(self):
            return "b" * 64

    class Coordinator:
        handlers = {"nas-backup": Handler(), "vm104-main": Handler()}
        def prepare(self, rid, req):
            assert (rid, req) == (release_id, request_id)
            store.put(f"target-preparation:{request_id}", {"release_id": rid, "request_id": req, "phase": "validated"})
            store.put(f"target-attestation:{rid}", {"request_id": req, "expires_at": "2099-01-01T00:00:00Z"})
            return store.get(f"target-preparation:{request_id}")

    runner = TaskRunner(store, project, tmp_path / "persistent", lambda: None, start_thread=False, deployment_factory=Coordinator)
    assert store.task(task_id)["status"] == "queued"
    runner.run_task(task_id)
    assert store.task(task_id)["status"] == "succeeded"
    assert store.get("target_actual_status")["targets"] == {"nas-backup": "b" * 64, "vm104-main": "b" * 64}
    assert store.get(f"target-preparation:{request_id}")["phase"] == "validated"


@pytest.mark.parametrize(
    ("phase", "expected_task_status"),
    [("failed_rolled_back", "failed"), ("rollback_failed", "blocked")],
)
def test_manual_deploy_task_does_not_report_failed_coordinator_phase_as_success(tmp_path, phase, expected_task_status):
    project = fixture_project(tmp_path / "project")
    store = Store(tmp_path / "persistent/controller.sqlite3")
    request_id, release_id = str(uuid.uuid4()), "a" * 64

    class Handler:
        def current_release(self):
            return "b" * 64

    class Coordinator:
        handlers = {"nas-backup": Handler(), "vm104-main": Handler()}
        def run(self, rid, req):
            assert (rid, req) == (release_id, request_id)
            store.put(f"deployment:{request_id}", {"request_id": req, "release_id": rid, "phase": phase})
            return store.get(f"deployment:{request_id}")

    task_id = store.create_task("manual_deploy", {"release_id": release_id, "request_id": request_id, "intent": "human_manual_activation"})
    runner = TaskRunner(store, project, tmp_path / "persistent", lambda: None, start_thread=False, deployment_factory=Coordinator)
    runner.run_task(task_id)
    task = store.task(task_id)
    assert task["status"] == expected_task_status
    assert "succeeded" != task["status"]
    assert phase in task["message"]



def _add_catalog_fixture(root: Path) -> None:
    categories_path = root / "config/categories.yaml"
    categories = yaml.safe_load(categories_path.read_text())["categories"]
    categories["demo"]["catalog_service_id"] = "demo_service"
    categories["pending"] = {"provider_name": "Pending", "policy_group": "Proxy", "priority": 11, "enabled": False, "source_file": "Pending/Pending.list", "catalog_service_id": "pending_service"}
    categories["review_candidate"] = {"provider_name": "ReviewCandidate", "policy_group": "Proxy", "priority": 13, "enabled": False, "source_file": "ReviewCandidate/ReviewCandidate.list", "catalog_service_id": "review_service"}
    categories["direct"] = {"provider_name": "DirectRef", "policy_group": "Proxy", "priority": 12, "enabled": False, "source_file": "Direct/Direct.list", "catalog_service_id": "direct_service"}
    categories["apple"] = {"provider_name": "Apple", "policy_group": "Apple", "priority": 30, "enabled": False, "source_file": "Apple/Apple.list", "catalog_service_id": "apple_aggregate"}
    categories["apple_domain_set"] = {"provider_name": "AppleDomainSet", "policy_group": "Apple", "priority": 30, "enabled": False, "source_file": "Apple/Apple_Domain.list", "catalog_service_id": "apple_aggregate"}
    categories_path.write_text(yaml.safe_dump({"categories": categories}, sort_keys=False))
    policy_path = root / "config/policies.yaml"
    policies = yaml.safe_load(policy_path.read_text())["policies"]
    policies["Apple"] = {}
    policy_path.write_text(yaml.safe_dump({"policies": policies}, sort_keys=False))
    review_file = root / ".cache/source/rule/Surge/ReviewCandidate/ReviewCandidate.list"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.write_text("DOMAIN,review-candidate.example\n")
    services = {}
    for service_id, review, route, implementation in (
        ("demo_service", "approved", "proxy", "approved_candidate"),
        ("pending_service", "pending", "proxy", "candidate"),
        ("review_service", "pending", "review", "candidate"),
        ("direct_service", "approved", "direct", "reference_only"),
        ("apple_aggregate", "pending", "review", "prohibited_aggregate"),
    ):
        services[service_id] = {"label": service_id, "use_group": "Development", "policy_group": "Proxy", "review_state": review, "route_intent": route, "implementation_status": implementation, "default_enabled": False, "missing": False, "coverage_status": "domain-only", "source_refs": [], "risks": [], "dependencies": [], "evidence": []}
    (root / "config/service-catalog.yaml").write_text(yaml.safe_dump({"schema_version": 1, "services": services}, sort_keys=False))


def test_catalog_metadata_and_category_batch_preview_apply_are_server_gated(tmp_path):
    project = fixture_project(tmp_path / "project")
    _add_catalog_fixture(project)
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    app = create_app(Settings(project, tmp_path / "persistent", "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    csrf = client.post("/login", json={"password": "private-admin-password-123"}).json()["csrf"]
    unauthenticated = TestClient(create_app(Settings(project, tmp_path / "unauth", "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: fake, start_worker=False))
    assert unauthenticated.post("/api/categories/batch-preview", json={"action": "enable", "service_ids": ["demo_service"]}).status_code == 401
    assert client.post("/api/categories/batch-preview", json={"action": "enable", "service_ids": ["demo_service"]}).status_code == 403

    metadata = {item["id"]: item for item in client.get("/api/state").json()["service_catalog"]}
    assert metadata["demo_service"]["category_id"] == "demo"
    assert metadata["demo_service"]["source_candidates"] == []
    assert metadata["demo_service"]["evidence"] == []
    assert set(metadata["apple_aggregate"]["category_ids"]) == {"apple", "apple_domain_set"}

    preview = client.post("/api/categories/batch-preview", json={"action": "enable", "service_ids": ["demo_service", "pending_service", "direct_service"]}, headers={"X-CSRF-Token": csrf})
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["ok"] is False
    assert [item["service_id"] for item in plan["blockers"]] == ["pending_service", "direct_service"]
    assert "待审核" in plan["blockers"][0]["reason"]
    assert client.post("/api/categories/batch-apply", json={"action": "enable", "service_ids": ["demo_service", "pending_service"]}, headers={"X-CSRF-Token": csrf}).status_code == 422

    review_enable = client.post("/api/categories/batch-preview", json={"action": "enable", "service_ids": ["review_service"]}, headers={"X-CSRF-Token": csrf})
    assert review_enable.status_code == 200 and review_enable.json()["ok"] is False
    assert "待审核" in review_enable.json()["blockers"][0]["reason"]
    review_disable = client.post("/api/categories/batch-preview", json={"action": "disable", "service_ids": ["review_service"]}, headers={"X-CSRF-Token": csrf})
    assert review_disable.status_code == 200 and review_disable.json()["ok"] is True
    assert client.post("/api/categories/batch-preview", json={"action": "disable", "service_ids": ["apple_aggregate"]}, headers={"X-CSRF-Token": csrf}).json()["ok"] is True

    single_enable_state = custom_state()
    single_enable_state["categories"]["review_candidate"] = {"enabled": True}
    single_enable = client.put("/api/draft", json={"state": single_enable_state}, headers={"X-CSRF-Token": csrf})
    assert single_enable.status_code == 200
    archive = write_candidate(project, single_enable_state)
    with zipfile.ZipFile(__import__("io").BytesIO(archive)) as zipped:
        manifest = json.loads(zipped.read("rules/manifest.json"))
    review_blockers = [item for item in manifest["deployment_blockers"] if item.get("service") == "review_service"]
    assert review_blockers and review_blockers[0]["reason"] == "enabled service catalog entry requires review"

    allowed = client.post("/api/categories/batch-preview", json={"action": "enable", "service_ids": ["demo_service"]}, headers={"X-CSRF-Token": csrf})
    assert allowed.json()["ok"] is True
    applied = client.post("/api/categories/batch-apply", json={"action": "enable", "service_ids": ["demo_service"]}, headers={"X-CSRF-Token": csrf})
    assert applied.status_code == 200
    assert applied.json()["state"]["categories"]["demo"]["enabled"] is True
    assert app.state.store.get("draft")["dirty"] is True
    assert fake.dispatches == []

    forbidden_state = applied.json()["state"]
    forbidden_state["categories"]["direct"] = {"enabled": True}
    rejected = client.put("/api/draft", json={"state": forbidden_state}, headers={"X-CSRF-Token": csrf})
    assert rejected.status_code == 422
    assert "不是可代理" in rejected.json()["error"]
    for blocked_category in ("apple", "apple_domain_set"):
        prohibited_state = custom_state()
        prohibited_state["categories"][blocked_category] = {"enabled": True}
        assert client.put("/api/draft", json={"state": prohibited_state}, headers={"X-CSRF-Token": csrf}).status_code == 422

    disabled = client.post("/api/categories/batch-preview", json={"action": "disable", "service_ids": ["demo_service"]}, headers={"X-CSRF-Token": csrf})
    assert disabled.json()["ok"] is True
    disabled_apply = client.post("/api/categories/batch-apply", json={"action": "disable", "service_ids": ["demo_service"]}, headers={"X-CSRF-Token": csrf})
    assert disabled_apply.status_code == 200
    assert disabled_apply.json()["state"]["categories"]["demo"]["enabled"] is False


def test_legacy_tiktok_disabled_state_is_preserved_and_active_use_rejected(tmp_path):
    project = fixture_project(tmp_path / "project")
    state = custom_state()
    state["categories"]["tiktok"] = {"enabled": False}
    state["rules"].append({"domain": "old-tiktok.example", "match_type": "EXACT", "category": "tiktok", "enabled": False, "note": "preserve me"})
    normalized = validate_state(state, project)
    assert "tiktok" not in normalized["categories"]
    assert normalized["rules"][-1]["note"] == "preserve me"
    token_path, password_path, session_path = secrets_for(tmp_path)
    fake = FakeGitHubService(project)
    fake.state_bytes = yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode()
    fake.state_sha = fake._blob_sha(fake.state_bytes)
    app = create_app(Settings(project, tmp_path / "persistent", "987N", "dns-rule-builder", token_path, password_path, session_path, False), client_factory=lambda: fake, start_worker=False)
    client = TestClient(app)
    client.post("/login", json={"password": "private-admin-password-123"})
    result = client.get("/api/state").json()
    assert "TikTok" in " ".join(result["compatibility_warnings"])
    assert result["state"]["rules"][-1]["category"] == "tiktok"
    invalid = custom_state()
    invalid["categories"]["tiktok"] = {"enabled": True}
    with pytest.raises(BuildError, match="TikTok"):
        validate_state(invalid, project)


def test_cached_legacy_draft_get_defaults_unsupported_dispositions_and_put_round_trips(tmp_path):
    project = fixture_project(tmp_path / "project")
    data_root = tmp_path / "persistent"
    token_path, password_path, session_path = secrets_for(tmp_path)
    store = Store(data_root / "controller.sqlite3")
    legacy = custom_state()
    assert "unsupported_dispositions" not in legacy
    store.put("draft", {"state": legacy, "base_sha": "cached-base", "dirty": True})
    settings = Settings(project, data_root, "987N", "dns-rule-builder", token_path, password_path, session_path, False)
    app = create_app(settings, client_factory=lambda: None, start_worker=False)
    with TestClient(app) as client:
        login = client.post("/login", json={"password": "private-admin-password-123"})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf"]}
        response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json()["state"]["unsupported_dispositions"] == []
        assert response.json()["dirty"] is True
        assert app.state.store.get("draft")["state"]["unsupported_dispositions"] == []
        saved = client.put("/api/draft", json={"state": response.json()["state"]}, headers=headers)
        assert saved.status_code == 200
        assert app.state.store.get("draft")["state"]["unsupported_dispositions"] == []
