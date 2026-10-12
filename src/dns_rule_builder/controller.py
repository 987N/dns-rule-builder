"""Serialized, persistent GitHub workflow and candidate-release controller."""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import yaml

from .engine import BuildError, canonical_domain, verify_manifest
from .github import GitHubClient, GitHubError
from .catalog import load_service_catalog
from .rules_state import normalize_legacy_tiktok_state
from .source_approvals import ApprovalError, validate_rules_data_dispositions
from .store import Store


MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_RELEASE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_REVIEW_ARTIFACT_BYTES = 64 * 1024 * 1024
TASK_TERMINAL = {"succeeded", "failed", "blocked", "interrupted"}


def _assert_category_runnable(category: str, categories: dict, services: dict) -> dict | None:
    cfg = categories.get(category)
    if cfg is None:
        raise BuildError(f"未知运行类别：{category}")
    service_id = cfg.get("catalog_service_id")
    spec = services.get(service_id) if service_id else None
    if not spec:
        if not service_id:
            return None
        raise BuildError(f"类别 {category} 没有可审核的服务目录条目")
    implementation = spec.get("implementation_status")
    if spec.get("route_intent") not in {"proxy", "review"} or implementation in {"reference_only", "prohibited_aggregate", "manual_missing"} or spec.get("missing", False):
        raise BuildError(f"服务 {service_id} 不是可代理的可运行来源（{implementation or spec.get('route_intent')}）")
    if category != "foreign_general" and cfg.get("source_file") is None and not cfg.get("source_refs"):
        raise BuildError(f"服务 {service_id} 没有运行来源")
    return spec


def validate_state(state: object, project_root: Path) -> dict:
    state, _warnings = normalize_legacy_tiktok_state(state)
    required_fields = {"schema_version", "categories", "rules", "force_direct", "approved_exclusions", "shared_domain_reviews"}
    if not isinstance(state, dict) or frozenset(state) not in {frozenset(required_fields), frozenset(required_fields | {"unsupported_dispositions"})} or state.get("schema_version") != 1:
        raise BuildError("规则状态必须是完整的 schema v1 数据")
    state.setdefault("unsupported_dispositions", [])
    if not isinstance(state["categories"], dict) or not isinstance(state["rules"], list) or not isinstance(state["force_direct"], list) or not isinstance(state["approved_exclusions"], list) or not isinstance(state["shared_domain_reviews"], list):
        raise BuildError("规则状态字段类型无效")
    try:
        validate_rules_data_dispositions(project_root, state["unsupported_dispositions"])
    except ApprovalError as exc:
        raise BuildError(str(exc)) from exc
    categories = yaml.safe_load((project_root / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
    policies = yaml.safe_load((project_root / "config/policies.yaml").read_text(encoding="utf-8"))["policies"]
    services = load_service_catalog(project_root).get("services", {})
    for category, value in state["categories"].items():
        if category not in categories or not isinstance(value, dict) or set(value) - {"enabled", "policy_group", "priority"}:
            raise BuildError(f"未知类别或字段：{category}")
        if "enabled" in value and not isinstance(value["enabled"], bool):
            raise BuildError(f"类别 {category} 的启用状态必须为布尔值")
        if value.get("enabled") is True:
            _assert_category_runnable(category, categories, services)
        group = value.get("policy_group", categories[category]["policy_group"])
        if not isinstance(group, str) or group not in policies:
            raise BuildError(f"类别 {category} 映射到了未知策略组")
        if "priority" in value and (not isinstance(value["priority"], int) or isinstance(value["priority"], bool)):
            raise BuildError(f"类别 {category} 优先级必须是整数")
    if len(state["approved_exclusions"]) > 1000:
        raise BuildError("来源排除记录不能超过 1000 条")
    for raw_domain in state["force_direct"]:
        if not isinstance(raw_domain, str) or canonical_domain(raw_domain) != raw_domain:
            raise BuildError("直连例外必须是规范小写域名")
    exclusion_keys = set()
    for item in state["approved_exclusions"]:
        fields = {"category", "domain", "match_type", "reviewer", "reviewed_at", "reason"}
        if not isinstance(item, dict) or set(item) != fields:
            raise BuildError("来源排除项必须包含类别、域名、匹配类型、审核人、时间和理由")
        category, domain, match_type = item["category"], item["domain"], item["match_type"]
        if not isinstance(category, str) or category not in categories or not isinstance(domain, str) or not isinstance(match_type, str) or match_type not in {"EXACT", "SUFFIX"}:
            raise BuildError("来源排除项的类别、域名或匹配类型无效")
        try:
            if canonical_domain(domain) != domain:
                raise BuildError("来源排除域名必须使用规范小写形式")
            reviewed_at = item["reviewed_at"]
            if not isinstance(reviewed_at, str) or datetime.fromisoformat(reviewed_at.replace("Z", "+00:00")).tzinfo is None:
                raise BuildError("来源排除审核时间必须包含时区")
        except (ValueError, TypeError) as exc:
            raise BuildError(f"来源排除项无效：{exc}") from exc
        if any(not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > maximum for key, maximum in (("reviewer", 100), ("reason", 500))):
            raise BuildError("来源排除项必须填写审核人和 500 字以内理由")
        key = (category, domain, match_type)
        if key in exclusion_keys:
            raise BuildError("来源排除项重复")
        exclusion_keys.add(key)
    shared_ids = {item.get("id") for item in yaml.safe_load((project_root / "config/shared-domains.yaml").read_text(encoding="utf-8")).get("shared_domains", [])}
    seen_shared = set()
    for review in state["shared_domain_reviews"]:
        if not isinstance(review, dict) or set(review) - {"id", "status", "reviewed_by", "reviewed_at", "rationale"} or not isinstance(review.get("id"), str) or review.get("id") not in shared_ids or review.get("id") in seen_shared:
            raise BuildError("共享域名审核记录 ID 未知、重复或字段无效")
        if review.get("status") != "approved" or not all(isinstance(review.get(key), str) and review[key].strip() for key in ("reviewed_by", "reviewed_at", "rationale")):
            raise BuildError("共享域名审核必须明确填写审核人、日期和理由")
        seen_shared.add(review["id"])
    for item in state["rules"]:
        if not isinstance(item, dict) or set(item) - {"domain", "match_type", "category", "enabled", "note"} or not {"domain", "match_type", "category"}.issubset(item):
            raise BuildError("自定义规则必须包含 domain、match_type、category；可选 enabled、note")
        legacy_disabled_tiktok = item.get("category") == "tiktok" and item.get("enabled") is False
        if not isinstance(item["match_type"], str) or item["match_type"] not in {"EXACT", "SUFFIX"} or not isinstance(item["category"], str) or (item["category"] not in categories and not legacy_disabled_tiktok) or not isinstance(item["domain"], str):
            raise BuildError("自定义规则的匹配类型、类别或域名无效")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise BuildError("自定义规则 enabled 必须为布尔值")
        if "note" in item and (not isinstance(item["note"], str) or len(item["note"]) > 300):
            raise BuildError("自定义规则备注须为 300 字以内文本")
        try:
            if canonical_domain(item["domain"]) != item["domain"]:
                raise BuildError("域名必须使用规范小写形式")
        except BuildError as exc:
            raise BuildError(f"自定义规则域名无效：{exc}") from exc
        effective_enabled = state["categories"].get(item["category"], {}).get("enabled", categories.get(item["category"], {}).get("enabled", True))
        if item.get("enabled", True) and not effective_enabled:
            raise BuildError(f"启用规则不能归入已禁用类别：{item['category']}")
    seen = set()
    for item in state["rules"]:
        key = (item["domain"], item["match_type"])
        if key in seen:
            raise BuildError(f"重复的自定义规则：{item['domain']} {item['match_type']}")
        seen.add(key)
    return state


def validate_release_zip(payload: bytes, destination: Path) -> tuple[Path, dict]:
    if len(payload) > MAX_RELEASE_BYTES:
        raise BuildError("规则压缩包超过大小限制")
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for item in archive.infolist():
                name = PurePosixPath(item.filename)
                mode = (item.external_attr >> 16) & 0xFFFF
                if name.is_absolute() or any(part in {"", ".", ".."} for part in name.parts) or not name.parts or name.parts[0] != "rules" or (mode and (mode & 0o170000) not in {0, 0o100000, 0o040000}):
                    raise BuildError("Release 压缩包包含非法路径或特殊文件")
                total += item.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise BuildError("Release 解压大小超过限制")
                archive.extract(item, destination)
    except (OSError, zipfile.BadZipFile, RuntimeError, BuildError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise BuildError(f"无法读取规则 Release 压缩包：{type(exc).__name__}") from exc
    extracted_rules = destination / "rules"
    if not extracted_rules.is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        raise BuildError("Release 压缩包缺少 rules/ 目录")
    try:
        manifest_data = json.loads((extracted_rules / "manifest.json").read_text(encoding="utf-8"))
        release_id = manifest_data.get("release_id")
        if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            raise BuildError("Release manifest 的版本身份无效")
        release_root = destination.parent / release_id
        if release_root.exists():
            shutil.rmtree(release_root)
        os.replace(extracted_rules, release_root)
        destination.rmdir()
        manifest = verify_manifest(release_root)
    except (OSError, ValueError, KeyError, BuildError) as exc:
        if "release_root" in locals():
            shutil.rmtree(release_root, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise BuildError(f"Release manifest 无法校验：{type(exc).__name__}") from exc
    return release_root, manifest


class TaskRunner:
    def __init__(self, store: Store, project_root: Path, data_root: Path, client_factory, poll_interval: float = 2.0, start_thread: bool = True, deployment_factory=None):
        self.store = store
        self.project_root = project_root.resolve()
        self.data_root = data_root.resolve()
        self.client_factory = client_factory
        self.deployment_factory = deployment_factory
        self.poll_interval = poll_interval
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._remote_thread: threading.Thread | None = None
        self._next_remote_check = 0.0
        self.store.recover_interrupted()
        if start_thread:
            self._thread = threading.Thread(target=self.run_forever, name="dns-rule-worker", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        for thread in (self._thread, self._remote_thread):
            if thread and thread.is_alive():
                thread.join(timeout=10)

    def _maybe_start_remote_check(self):
        if time.monotonic() < self._next_remote_check:
            return
        if self._remote_thread and self._remote_thread.is_alive():
            return
        if self.store.has_running_task() or self.store.next_queued_task():
            return
        self._remote_thread = threading.Thread(target=self._check_remote_state, name="dns-rule-remote-check", daemon=True)
        self._remote_thread.start()

    def _check_remote_state(self):
        prior = self.store.get("remote_status") or {}
        try:
            client = self.client_factory()
            head = self._retry(lambda: client.get_branch_head("rules-data"), attempts=2)
            releases = self._retry(lambda: client.list_releases(50), attempts=2)
            latest = next((item for item in releases if isinstance(item, dict) and not item.get("draft") and str(item.get("tag_name", "")).startswith("rules-") and "build_purpose=smoke" not in str(item.get("body", ""))), None)
            synced = (self.store.get("synced") or {}).get("commit")
            status = {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "data_commit": head, "remote_changed": bool(synced and synced != head), "latest_release_tag": latest.get("tag_name") if latest else None, "error": None, "failures": 0}
            self.store.put("remote_status", status)
            self._next_remote_check = time.monotonic() + 15 * 60
        except (GitHubError, OSError, ValueError, RuntimeError) as exc:
            failures = min(8, int(prior.get("failures", 0)) + 1)
            status = {**prior, "status": "error", "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "error": f"GitHub 检查失败（HTTP {exc.status}）" if isinstance(exc, GitHubError) else f"GitHub 检查失败（{type(exc).__name__}）", "failures": failures}
            self.store.put("remote_status", status)
            self._next_remote_check = time.monotonic() + min(60 * 60, 15 * 60 * (2 ** (failures - 1)))

    def run_forever(self):
        self._schedule_daily()
        while not self._stop.is_set():
            if self.store.has_running_task():
                self._stop.wait(1)
                continue
            task = self.store.next_queued_task()
            if task:
                self.run_task(task["id"])
            else:
                self._schedule_daily()
                self._maybe_start_remote_check()
                self._stop.wait(1)

    def _schedule_daily(self):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        scheduled = now.replace(hour=4, minute=17, second=0, microsecond=0)
        if now < scheduled:
            return
        day = now.date().isoformat()
        if self.store.get("daily_enqueued_date") == day:
            return
        synced = self.store.get("synced") or {}
        if not synced.get("commit"):
            return
        self.store.create_task("build", {"data_commit": synced["commit"], "daily": True, "schedule_date": day})
        self.store.put("daily_enqueued_date", day)

    @staticmethod
    def _retry(call, attempts: int = 5):
        for index in range(attempts):
            try:
                return call()
            except GitHubError as exc:
                retryable = exc.status == 0 or exc.status == 429 or exc.status >= 500 or exc.status == 403 and exc.retry_after
                if not retryable or index == attempts - 1:
                    raise
                try:
                    delay = min(60, max(1, int(exc.retry_after))) if exc.retry_after else min(30, 2 ** index)
                except ValueError:
                    delay = min(30, 2 ** index)
                time.sleep(delay)

    def run_task(self, task_id: str):
        task = self.store.task(task_id)
        if not task or task["status"] != "queued":
            return
        self.store.update_task(task_id, "running", "任务已开始")
        try:
            if task["kind"] == "save":
                result = self._save(task["payload"])
            elif task["kind"] == "build":
                result = self._build(task_id, task["payload"])
            elif task["kind"] in {"prepare_deploy", "manual_deploy", "rollback"}:
                result = self._run_deployment_task(task["kind"], task["payload"])
            else:
                raise BuildError("未知任务类型")
            if self._stop.is_set() and task["kind"] == "build":
                return
            self.store.update_task(task_id, "succeeded", json.dumps(result, ensure_ascii=False))
        except (BuildError, GitHubError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            if self._stop.is_set() and task["kind"] == "build" and self.store.task(task_id)["payload"].get("request_id"):
                # Leave the durable polling/downloading phase intact so startup recovery
                # resumes this UUID instead of reporting a false terminal failure.
                return
            state = "blocked" if isinstance(exc, (BuildError, ValueError)) else "failed"
            if task["kind"] in {"prepare_deploy", "manual_deploy", "rollback"}:
                operation_request = task["payload"].get("deployment_request_id") if task["kind"] == "rollback" else task["payload"].get("request_id")
                coordinator_state = self.store.get(f"deployment:{operation_request}") or self.store.get(f"target-preparation:{operation_request}") or {}
                phase = coordinator_state.get("phase")
                if phase in {"manual_intervention", "rollback_failed", "failed_rolled_back", "failed"}:
                    state = "blocked" if phase in {"manual_intervention", "rollback_failed"} else "failed"
                    exc = BuildError(f"部署状态 {phase}；{exc}")
                try:
                    if self.deployment_factory is not None:
                        self._record_target_status(self.deployment_factory())
                except Exception:
                    self.store.put("target_actual_status", {"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "targets": {}, "error": "目标状态未知；需要人工检查"})
            self.store.update_task(task_id, state, str(exc))

    def _run_deployment_task(self, kind: str, payload: dict) -> dict:
        if self.deployment_factory is None:
            raise BuildError("未配置 schema-v4 target；部署任务未连接任何设备")
        release_id = payload.get("release_id")
        request_id = payload.get("request_id")
        if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id):
            raise BuildError("部署任务版本 ID 无效")
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise BuildError("部署任务 request UUID 无效") from exc
        coordinator = self.deployment_factory()
        if kind == "prepare_deploy":
            state = coordinator.prepare(release_id, request_id)
            self._record_target_status(coordinator)
            return {"request_id": request_id, "release_id": release_id, "phase": state["phase"], "expires_at": (self.store.get(f"target-attestation:{release_id}") or {}).get("expires_at")}
        if kind == "manual_deploy":
            state = coordinator.run(release_id, request_id)
            if state.get("phase") != "succeeded":
                raise BuildError(f"人工部署未能确认完成：{state.get('phase', 'unknown')}；需要检查部署记录")
        else:
            original_request_id = payload.get("deployment_request_id")
            try:
                if str(uuid.UUID(original_request_id)) != original_request_id:
                    raise ValueError
            except (ValueError, TypeError, AttributeError) as exc:
                raise BuildError("回滚目标 request UUID 无效") from exc
            state = coordinator.rollback_active(original_request_id)
        self._record_target_status(coordinator)
        if kind == "rollback" and state.get("phase") != "failed_rolled_back":
            raise BuildError(f"回退未能确认完成：{state.get('phase', 'unknown')}；需要人工检查")
        return {"request_id": request_id, "deployment_request_id": state.get("request_id"), "release_id": release_id, "phase": state.get("phase"), "targets": {target_id: self._safe_target_current(coordinator, target_id) for target_id in coordinator.handlers}}

    @staticmethod
    def _safe_target_current(coordinator, target_id: str):
        try:
            return coordinator.handlers[target_id].current_release()
        except Exception:
            return "unknown"

    def _record_target_status(self, coordinator) -> None:
        observed = {target_id: self._safe_target_current(coordinator, target_id) for target_id in coordinator.handlers}
        self.store.put("target_actual_status", {"observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "targets": observed})

    def _save(self, payload: dict) -> dict:
        state = validate_state(payload.get("state"), self.project_root)
        encoded = yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode()
        if len(encoded) > MAX_STATE_BYTES:
            raise BuildError("规则状态超过 2 MiB 限制")
        client = self.client_factory()
        expected = payload.get("expected_sha")
        blob_sha = hashlib.sha1(b"blob " + str(len(encoded)).encode() + b"\0" + encoded).hexdigest()
        commit = None
        for attempt in range(3):
            try:
                try:
                    current, current_sha = self._retry(lambda: client.get_contents("rules-data/state.yaml", "rules-data"))
                    if hashlib.sha1(b"blob " + str(len(current)).encode() + b"\0" + current).hexdigest() == blob_sha:
                        commit = self._retry(lambda: client.get_branch_head("rules-data"))
                        expected = current_sha
                        break
                except GitHubError as exc:
                    if exc.status != 404:
                        raise
                commit = client.update_contents("rules-data/state.yaml", encoded, "rules-data", expected, "Update DNS rule state")
                break
            except GitHubError as exc:
                retryable = exc.status == 0 or exc.status == 429 or exc.status >= 500
                if not retryable or attempt == 2:
                    raise
                try:
                    delay = min(60, max(1, int(exc.retry_after))) if exc.retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
        if not commit:
            raise GitHubError(0, "GitHub 状态保存未确认")
        remote = self.store.get("draft", {})
        try:
            remote_state = validate_state(remote.get("state"), self.project_root)
        except BuildError:
            remote_state = None
        if remote_state == state:
            remote.update({"state": state, "base_sha": blob_sha, "dirty": False})
        else:
            # A newer local edit arrived while this immutable save task was running.
            remote.update({"base_sha": blob_sha, "dirty": True})
        self.store.put("draft", remote)
        synced = {"commit": commit, "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        self.store.put("synced", synced)
        return {"commit": commit, "synced": True}

    def _build(self, task_id: str, payload: dict) -> dict:
        data_commit = payload.get("data_commit")
        synced = self.store.get("synced") or {}
        if not isinstance(data_commit, str) or data_commit != synced.get("commit"):
            raise BuildError("规则尚未同步到 GitHub，不能生成")
        client = self.client_factory()
        if not payload.get("request_id"):
            payload = {**payload, "code_commit": self._retry(lambda: client.get_branch_head("main")), "request_id": str(uuid.uuid4()), "phase": "dispatching"}
            self.store.update_task_payload(task_id, payload)
            self.store.update_task(task_id, "polling", "已记录触发 UUID，正在请求 GitHub Actions")
            try:
                client.dispatch_workflow("rules.yml", "main", data_commit, payload["request_id"])
            except GitHubError as exc:
                # Dispatch may have reached GitHub even if the response was lost. Resume by UUID;
                # never issue a second dispatch automatically.
                if exc.status not in (0, 408, 429) and exc.status < 500:
                    raise
        request_id = payload["request_id"]
        code_commit = payload["code_commit"]
        deadline = time.monotonic() + 60 * 60
        run = self._retry(lambda: client.get_workflow_run(payload["run_id"])) if payload.get("run_id") else None
        while run is None and time.monotonic() < deadline and not self._stop.is_set():
            run = self._retry(lambda: client.find_dispatch_run(request_id, "main"))
            if run:
                payload = {**payload, "phase": "polling", "run_id": run.get("id"), "code_commit": run.get("head_sha", code_commit)}
                self.store.update_task_payload(task_id, payload)
                break
            time.sleep(self.poll_interval)
        if not run:
            raise BuildError("无法确认该触发 UUID 是否已建立 workflow run；为避免重复构建，请在 GitHub Actions 核对后再手动重试")
        run_id = run.get("id")
        while time.monotonic() < deadline and run.get("status") != "completed" and not self._stop.is_set():
            if isinstance(run_id, int):
                run = self._retry(lambda: client.get_workflow_run(run_id))
            else:
                run = self._retry(lambda: client.find_dispatch_run(request_id, "main")) or run
            time.sleep(self.poll_interval)
        if run.get("status") != "completed":
            raise BuildError("GitHub 构建尚未完成；任务保持受阻状态，不会重复触发，请稍后查看原 workflow")
        if run.get("conclusion") != "success":
            raise BuildError(f"GitHub 构建失败：{run.get('conclusion') or '无结论'}")
        self.store.update_task(task_id, "downloading", "CI 成功，正在校验并下载 Release")
        items = self._retry(lambda: client.list_releases(20))
        expected_code = run.get("head_sha")
        if expected_code != code_commit:
            raise BuildError("workflow 代码版本与触发时记录的 main SHA 不一致")
        selected = None
        for item in items:
            if not isinstance(item, dict) or item.get("draft") or not str(item.get("tag_name", "")).startswith("rules-"):
                continue
            try:
                rel_id = item["tag_name"][6:]
                if len(rel_id) != 64 or not all(c in "0123456789abcdef" for c in rel_id):
                    continue
                asset = next(asset for asset in item.get("assets", []) if asset.get("name") == "rules.zip")
                archive = self._retry(lambda: client.download_public_release_asset(asset["browser_download_url"], MAX_RELEASE_BYTES, asset_id=asset["id"]))
                unpack_dir = self.data_root / ".incoming" / str(uuid.uuid4())
                rules_dir, manifest = validate_release_zip(archive, unpack_dir)
                if manifest["build_purpose"] != "rules" or manifest["release_id"] != rel_id or manifest["data_revision"] != data_commit or manifest["code_commit"] != expected_code:
                    shutil.rmtree(rules_dir, ignore_errors=True)
                    continue
                selected = (item, rules_dir, manifest)
                break
            except (StopIteration, KeyError, BuildError, ValueError):
                continue
        if not selected:
            review = self._read_publish_review(client, run_id, request_id, data_commit, code_commit)
            if review:
                self.store.update_task_payload(task_id, {**payload, "publish_review": review})
                raise BuildError("构建完成，但来源/许可证发布审核门禁阻止发布；详见审核报告")
            raise BuildError("已无阻发布的可用 Release；无法读取匹配的审核报告，请检查 GitHub Actions")
        release, rules_dir, manifest = selected
        final_dir = self.data_root / "releases" / manifest["release_id"]
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            verify_manifest(final_dir)
            shutil.rmtree(rules_dir, ignore_errors=True)
        else:
            os.replace(rules_dir, final_dir)
        newest_local = (self.store.get("synced") or {}).get("commit")
        newest_remote = self._retry(lambda: client.get_branch_head("rules-data"))
        stale = newest_local != data_commit or newest_remote != data_commit or bool((self.store.get("draft") or {}).get("dirty"))
        if not stale:
            self._promote_candidate(final_dir)
        metadata = {"tag": release["tag_name"], "completed_at": run.get("completed_at"), "workflow_run_id": run_id, "stale": stale, "candidate": not stale, "manifest": {"statistics": manifest["statistics"], "category_status": manifest["category_status"], "deployment_allowed": manifest["deployment_allowed"], "deployment_blockers": manifest["deployment_blockers"]}}
        self.store.add_release(manifest["release_id"], data_commit, expected_code, final_dir, metadata)
        return {"release_id": manifest["release_id"], "data_commit": data_commit, "code_commit": expected_code, "candidate": str(final_dir) if not stale else None, "stale": stale, "workflow_completed_at": run.get("completed_at")}

    @staticmethod
    def _read_publish_review(client, run_id, request_id, data_commit, code_commit):
        """Read only the identity-matching manifest from this run's bounded artifact."""
        if not isinstance(run_id, int) or not isinstance(request_id, str):
            return None
        try:
            artifacts = client.list_workflow_artifacts(run_id)
            artifact = next((item for item in artifacts if item.get("name") == f"rule-build-{request_id}" and not item.get("expired")), None)
            if not artifact or not isinstance(artifact.get("id"), int) or artifact.get("size_in_bytes", 0) > MAX_REVIEW_ARTIFACT_BYTES:
                return None
            payload = client.download_workflow_artifact(artifact["id"], MAX_REVIEW_ARTIFACT_BYTES)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for item in archive.infolist():
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                        return None
                    if path.name != "manifest.json" or len(path.parts) != 2 or item.file_size > 16 * 1024 * 1024:
                        continue
                    manifest = json.loads(archive.read(item))
                    if not isinstance(manifest, dict):
                        continue
                    release_id = manifest.get("release_id")
                    if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id) or release_id != path.parts[0]:
                        continue
                    if (manifest.get("data_revision") != data_commit or manifest.get("code_commit") != code_commit
                            or manifest.get("build_purpose") != "rules" or manifest.get("publish_allowed") is not False):
                        continue
                    blockers = manifest.get("publish_blockers")
                    if not isinstance(blockers, list):
                        return None
                    safe_blockers = []
                    for blocker in blockers[:200]:
                        if not isinstance(blocker, dict):
                            continue
                        category = blocker.get("category")
                        reason = blocker.get("reason")
                        safe = {
                            "category": category[:100] if isinstance(category, str) else None,
                            "reason": reason[:400] if isinstance(reason, str) else "审核门禁未通过",
                        }
                        for key in ("source_id", "detail"):
                            value = blocker.get(key)
                            if isinstance(value, str):
                                safe[key] = value[:200]
                        rules = blocker.get("rules")
                        if isinstance(rules, list):
                            safe["rules"] = [
                                {key: rule[key][:200] for key in ("category", "domain", "match_type") if isinstance(rule, dict) and isinstance(rule.get(key), str)}
                                for rule in rules[:10] if isinstance(rule, dict)
                            ]
                        safe_blockers.append(safe)
                    return {
                        "run_id": run_id,
                        "request_id": request_id,
                        "data_commit": data_commit,
                        "code_commit": code_commit,
                        "release_id": manifest.get("release_id") if isinstance(manifest.get("release_id"), str) else None,
                        "publish_blockers": safe_blockers,
                    }
        except (GitHubError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError, zipfile.BadZipFile):
            return None
        return None

    def _promote_candidate(self, path: Path):
        candidate = self.data_root / "candidate"
        previous = self.data_root / "previous"
        old = candidate.resolve() if candidate.is_symlink() else None
        if old and old.exists():
            temp_previous = self.data_root / f".previous-{uuid.uuid4()}"
            temp_previous.symlink_to(old.relative_to(self.data_root), target_is_directory=True)
            os.replace(temp_previous, previous)
        temp_candidate = self.data_root / f".candidate-{uuid.uuid4()}"
        temp_candidate.symlink_to(path.relative_to(self.data_root), target_is_directory=True)
        os.replace(temp_candidate, candidate)
        protected = {path.resolve()}
        for pointer in (candidate, previous):
            if pointer.is_symlink():
                protected.add(pointer.resolve())
        releases = sorted((self.data_root / "releases").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        kept = 0
        for item in releases:
            if not item.is_dir():
                continue
            if item.resolve() in protected or kept < 30:
                kept += 1
            else:
                shutil.rmtree(item)

