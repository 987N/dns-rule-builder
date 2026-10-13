"""Single-process FastAPI web UI, auth boundary, and task API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
import urllib.parse
import copy
import hashlib
from datetime import datetime, timezone
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .controller import TaskRunner, _assert_category_runnable, validate_state
from .catalog import service_metadata_for_api
from .engine import BuildError, canonical_domain, explain, verify_manifest
from .github import GitHubClient, GitHubError
from .store import Store
from .rules_state import normalize_legacy_tiktok_state


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    github_owner: str
    github_repository: str
    github_token_file: Path
    admin_password_file: Path
    session_secret_file: Path
    secure_cookies: bool = True
    target_config_file: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project_root=Path(os.getenv("PROJECT_ROOT", "/app")),
            data_root=Path(os.getenv("DATA_ROOT", "/data")),
            github_owner=os.environ.get("GITHUB_OWNER", "987N"),
            github_repository=os.environ.get("GITHUB_REPOSITORY_NAME", "dns-rule-builder"),
            github_token_file=Path(os.environ.get("GITHUB_TOKEN_FILE", "/run/secrets/github_token")),
            admin_password_file=Path(os.environ.get("ADMIN_PASSWORD_FILE", "/run/secrets/admin_password")),
            session_secret_file=Path(os.environ.get("SESSION_SECRET_FILE", "/run/secrets/session_secret")),
            secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
            target_config_file=Path(os.environ.get("TARGET_CONFIG_FILE", "/run/config/targets-v4.yaml")),
        )


def _read_secret(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"{label} secret file is unavailable") from exc
    if len(value) < 16:
        raise RuntimeError(f"{label} secret must contain at least 16 characters")
    return value


class RateLimiter:
    def __init__(self):
        self.events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: tuple[str, str], limit: int, window: int) -> bool:
        now = time.monotonic()
        events = self.events[key]
        while events and events[0] <= now - window:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


async def bounded_body(request: Request, maximum: int = 2 * 1024 * 1024) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > maximum:
                raise ValueError("请求内容超过 2 MiB 限制")
        except ValueError as exc:
            if str(exc).startswith("请求内容"):
                raise
            raise ValueError("Content-Length 无效") from exc
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ValueError("请求内容超过 2 MiB 限制")
    return bytes(chunks)


def create_app(settings: Settings | None = None, client_factory=None, start_worker: bool = True, deployment_factory=None) -> FastAPI:
    settings = settings or Settings.from_env()
    password = _read_secret(settings.admin_password_file, "admin password")
    session_secret = _read_secret(settings.session_secret_file, "session signing")
    store = Store(settings.data_root / "controller.sqlite3")
    limiter = RateLimiter()
    templates = Jinja2Templates(directory=str(settings.project_root / "templates"))

    def github() -> GitHubClient:
        if client_factory:
            return client_factory()
        token = _read_secret(settings.github_token_file, "GitHub token")
        return GitHubClient(settings.github_owner, settings.github_repository, token)

    def configured_deployment():
        if deployment_factory is not None:
            return deployment_factory()
        if settings.target_config_file is None or not settings.target_config_file.is_file():
            raise BuildError("未安装 schema-v4 target 配置")
        from .ssh_transport import SSHTransport
        from .target_v4 import DeploymentCoordinator, load_target_specs
        enabled, specs = load_target_specs(settings.target_config_file)
        return DeploymentCoordinator(store, settings.data_root, specs, {spec.target_id: SSHTransport(spec) for spec in specs}, deployment_enabled=enabled)

    runner = TaskRunner(store, settings.project_root, settings.data_root, github, start_thread=start_worker, deployment_factory=configured_deployment)
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await run_in_threadpool(runner.stop)

    app = FastAPI(title="DNS Rule Builder", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(settings.project_root / "static")), name="static")
    app.add_middleware(SessionMiddleware, secret_key=session_secret, max_age=43200, same_site="strict", https_only=settings.secure_cookies)
    app.state.store = store
    app.state.runner = runner
    app.state.settings = settings
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def guard(request: Request, call_next):
        peer = request.client.host if request.client else "unknown"
        if request.url.path == "/login" and request.method == "POST":
            if not limiter.allow((peer, "login"), 5, 300):
                return JSONResponse({"error": "登录尝试过多，请五分钟后重试"}, status_code=429)
        elif request.url.path.startswith("/api/"):
            if not limiter.allow((peer, "api"), 90, 60):
                return JSONResponse({"error": "请求过于频繁，请稍后重试"}, status_code=429)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def csrf_valid(request: Request) -> bool:
        token = request.session.get("csrf")
        supplied = request.headers.get("x-csrf-token", "")
        return isinstance(token, str) and bool(supplied) and hmac.compare_digest(token, supplied)

    async def authenticated(request: Request):
        if not request.session.get("authenticated"):
            return JSONResponse({"error": "请先登录"}, status_code=401)
        return None

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login")
    async def login(request: Request):
        content_type = request.headers.get("content-type", "")
        try:
            raw = await bounded_body(request, 4096)
        except ValueError:
            return JSONResponse({"error": "登录请求超过 4 KiB 限制"}, status_code=413)
        if "application/json" in content_type:
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            submitted = body.get("password", "") if isinstance(body, dict) else ""
        else:
            submitted = urllib.parse.parse_qs(raw.decode("utf-8", "replace")).get("password", [""])[0]
        if not isinstance(submitted, str) or not hmac.compare_digest(hashlib.sha256(submitted.encode()).digest(), hashlib.sha256(password.encode()).digest()):
            return JSONResponse({"error": "密码不正确"}, status_code=401)
        request.session.clear()
        request.session.update({"authenticated": True, "csrf": secrets.token_urlsafe(32)})
        return {"ok": True, "csrf": request.session["csrf"]}

    @app.post("/logout")
    async def logout(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        if not csrf_valid(request):
            return JSONResponse({"error": "CSRF 校验失败"}, status_code=403)
        try:
            await bounded_body(request, 4096)
        except ValueError:
            return JSONResponse({"error": "请求内容超过 4 KiB 限制"}, status_code=413)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        csrf = request.session.setdefault("csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(request, "index.html", {"csrf": csrf})

    async def api_guard(request: Request):
        denied = await authenticated(request)
        if denied:
            return denied
        if request.method not in {"GET", "HEAD"} and not csrf_valid(request):
            return JSONResponse({"error": "CSRF 校验失败，请刷新页面后重试"}, status_code=403)
        return None

    def target_switch_state() -> tuple[bool, list[str], str | None]:
        if settings.target_config_file is None or not settings.target_config_file.is_file():
            return False, [], "schema-v4 target 尚未配置"
        try:
            from .target_v4 import load_target_specs
            _enabled, specs = load_target_specs(settings.target_config_file)
            config = yaml.safe_load(settings.target_config_file.read_text(encoding="utf-8"))
            # Old deployment_enabled never authorizes a new manual activation.
            human_enabled = config.get("maintenance_disabled") is False
            return human_enabled, [spec.target_id for spec in specs], None
        except (BuildError, OSError, ValueError) as exc:
            return False, [], str(exc)

    def deployment_summary() -> dict:
        enabled, target_ids, config_error = target_switch_state()
        candidate = settings.data_root / "candidate"
        candidate_id = candidate.resolve().name if candidate.is_symlink() else None
        blockers = []
        if candidate_id and re.fullmatch(r"[0-9a-f]{64}", candidate_id):
            try:
                manifest = verify_manifest(settings.data_root / "releases" / candidate_id)
                blockers = manifest.get("deployment_blockers", [])
            except (OSError, BuildError):
                blockers = [{"reason": "candidate manifest 校验失败"}]
        preparations = store.get_prefix("target-preparation:")
        deployments = store.get_prefix("deployment:")
        tasks_by_request = {}
        for task in store.tasks(1000):
            payload = task.get("payload", {})
            request_id = payload.get("prepare_request_id") if task.get("kind") == "generate_prepare" else payload.get("deployment_request_id") if task.get("kind") == "rollback" else payload.get("request_id")
            if isinstance(request_id, str):
                tasks_by_request.setdefault(request_id, task)
        prepared_rows = []
        for row in preparations.values():
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            attestation = store.get(f"target-attestation:{row.get('release_id')}") or {}
            if attestation.get("request_id") == row.get("request_id"):
                enriched["expires_at"] = attestation.get("expires_at")
            task = tasks_by_request.get(row.get("request_id"))
            if task:
                enriched["task_updated_at"] = task.get("updated_at")
                enriched["task_status"] = task.get("status")
            prepared_rows.append(enriched)
        deployment_rows = []
        for row in deployments.values():
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            task = tasks_by_request.get(row.get("request_id"))
            if task:
                enriched["task_updated_at"] = task.get("updated_at")
            deployment_rows.append(enriched)
        return {
            "configured": config_error is None,
            "configuration_error": config_error,
            "deployment_enabled": enabled,
            "target_ids": target_ids,
            "actual_status": store.get("target_actual_status"),
            "preparations": sorted(prepared_rows, key=lambda row: str(row.get("task_updated_at") or ""), reverse=True)[:20],
            "deployments": sorted(deployment_rows, key=lambda row: str(row.get("task_updated_at") or ""), reverse=True)[:20],
            "candidate_release_id": candidate_id,
            "candidate_blockers": blockers,
        }

    def busy_task_summary() -> dict | None:
        active = [task for task in store.tasks(1000) if task.get("status") in {"queued", "running", "polling", "downloading"}]
        if not active:
            return None
        task = sorted(active, key=lambda row: str(row.get("created_at") or ""))[0]
        return {key: task.get(key) for key in ("id", "kind", "status", "updated_at")}

    def validate_strategy_reference(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("策略成员引用必须是对象")
        kind, name = value.get("kind"), value.get("name")
        expected = {"kind", "name", "provider"} if kind == "provider" else {"kind", "name"}
        if (set(value) != expected or kind not in {"group", "proxy", "provider", "special"}
                or not isinstance(name, str) or not name or len(name) > 128
                or any(ord(char) < 32 for char in name)):
            raise ValueError("策略成员引用格式无效")
        if kind == "provider":
            provider = value.get("provider")
            if not isinstance(provider, str) or not provider or len(provider) > 128 or any(ord(char) < 32 for char in provider):
                raise ValueError("策略 provider 引用格式无效")
        return dict(value)

    async def parse_strategy_request(request: Request, *, include_preview_hash: bool) -> dict | JSONResponse:
        try:
            raw = await bounded_body(request, 128 * 1024)
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"error": "策略请求 JSON 无效或超过 128 KiB"}, status_code=422)
        expected_keys = {"group", "revision", "members", "selected"} | ({"preview_sha256"} if include_preview_hash else set())
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            return JSONResponse({"error": "策略请求字段不完整或包含不支持的字段"}, status_code=422)
        group, revision, members = payload.get("group"), payload.get("revision"), payload.get("members")
        if not isinstance(group, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", group):
            return JSONResponse({"error": "策略组名称无效"}, status_code=422)
        if type(revision) is not int or revision < 0 or not isinstance(members, list) or not members or len(members) > 256:
            return JSONResponse({"error": "策略版本或候选列表无效"}, status_code=422)
        try:
            normalized_members = [validate_strategy_reference(member) for member in members]
            selected = validate_strategy_reference(payload.get("selected"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        if len({json.dumps(member, sort_keys=True) for member in normalized_members}) != len(normalized_members):
            return JSONResponse({"error": "策略候选中有重复成员"}, status_code=422)
        if selected not in normalized_members:
            return JSONResponse({"error": "当前选择必须属于候选成员"}, status_code=422)
        result = {"group": group, "revision": revision, "members": normalized_members, "selected": selected}
        if include_preview_hash:
            digest = payload.get("preview_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                return JSONResponse({"error": "策略预览摘要无效，请重新预览"}, status_code=422)
            result["preview_sha256"] = digest
        return result

    def verified_local_release(release_id: object):
        if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id):
            raise ValueError("规则版本必须是 64 位小写十六进制 release ID")
        path = settings.data_root / "releases" / release_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError("规则版本目录缺失或不是本机管理目录")
        record = next((row for row in store.releases(1000) if row.get("release_id") == release_id), None)
        if record is None or Path(record["path"]).resolve() != path.resolve():
            raise ValueError("该版本不在本机已校验的规则索引中")
        manifest = verify_manifest(path)
        if manifest.get("release_id") != release_id:
            raise ValueError("规则包版本身份与路径不一致")
        return path, manifest

    @app.get("/api/state")
    async def get_state(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        draft = store.get("draft")
        if draft is None:
            try:
                client = github()
                commit = await run_in_threadpool(client.get_branch_head, "rules-data")
                remote, sha = await run_in_threadpool(client.get_contents, "rules-data/state.yaml", commit)
                raw_state = yaml.safe_load(remote)
                _normalized, compatibility_warnings = normalize_legacy_tiktok_state(raw_state)
                state = validate_state(raw_state, settings.project_root)
                draft = {"state": state, "base_sha": sha, "dirty": False, "compatibility_warnings": compatibility_warnings}
                store.put("draft", draft)
                store.put("synced", {"commit": commit})
            except (GitHubError, OSError, ValueError, BuildError, RuntimeError, yaml.YAMLError) as exc:
                return JSONResponse({"error": f"无法读取 GitHub 规则：{exc}", "unsynced": True}, status_code=503)
        try:
            original_state = copy.deepcopy(draft.get("state"))
            normalized_state, state_warnings = normalize_legacy_tiktok_state(draft.get("state"))
            normalized_state = validate_state(normalized_state, settings.project_root)
            compatibility_warnings = list(dict.fromkeys([*(draft.get("compatibility_warnings") or []), *state_warnings]))
            if normalized_state != original_state or compatibility_warnings != draft.get("compatibility_warnings", []):
                draft = {**draft, "state": normalized_state, "compatibility_warnings": compatibility_warnings}
                store.put("draft", draft)
        except (GitHubError, OSError, ValueError, BuildError, RuntimeError, yaml.YAMLError) as exc:
            return JSONResponse({"error": f"无法读取本机规则草稿：{exc}", "unsynced": True}, status_code=503)
        categories = yaml.safe_load((settings.project_root / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
        policies = yaml.safe_load((settings.project_root / "config/policies.yaml").read_text(encoding="utf-8"))["policies"]
        candidate = settings.data_root / "candidate"
        candidate_release_id = candidate.resolve().name if candidate.is_symlink() else None
        synced_commit = (store.get("synced") or {}).get("commit")
        tasks = store.tasks(100)
        latest_build = next((task for task in tasks if task.get("kind") == "build" and task.get("payload", {}).get("data_commit") == synced_commit), None)
        if latest_build:
            latest_build = {key: latest_build.get(key) for key in ("id", "status", "message", "updated_at", "payload")}
            run_id = latest_build.get("payload", {}).get("run_id")
            latest_build["workflow_url"] = f"https://github.com/{settings.github_owner}/{settings.github_repository}/actions/runs/{run_id}" if isinstance(run_id, int) else None
        return {"state": normalized_state, "dirty": draft.get("dirty", False), "base_sha": draft.get("base_sha"), "synced_commit": synced_commit, "remote_status": store.get("remote_status"), "categories": categories, "policies": sorted(policies), "service_catalog": service_metadata_for_api(settings.project_root), "compatibility_warnings": compatibility_warnings, "tasks": tasks[:20], "latest_build": latest_build, "releases": store.releases(10), "candidate_release_id": candidate_release_id, "deployment": deployment_summary(), "csrf": request.session["csrf"]}

    @app.put("/api/draft")
    async def update_draft(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            raw = await bounded_body(request)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return JSONResponse({"error": "请求内容必须是 JSON 对象"}, status_code=422)
            state = validate_state(payload.get("state"), settings.project_root)
            encoded = yaml.safe_dump(state, allow_unicode=True, sort_keys=False).encode()
            if len(encoded) > 2 * 1024 * 1024:
                return JSONResponse({"error": "规则状态超过 2 MiB"}, status_code=413)
            prior = store.get("draft", {})
            _state, compatibility_warnings = normalize_legacy_tiktok_state(payload.get("state"))
            store.put("draft", {"state": state, "base_sha": prior.get("base_sha"), "dirty": True, "compatibility_warnings": compatibility_warnings})
            return {"ok": True, "synced": False, "message": "草稿仅保存在本机，尚未同步 GitHub"}
        except (ValueError, BuildError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    def category_batch_plan(payload: object) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"action", "service_ids"}:
            raise ValueError("请求必须包含 action 和 service_ids")
        action, service_ids = payload["action"], payload["service_ids"]
        if action not in {"enable", "disable"} or not isinstance(service_ids, list) or not service_ids or len(service_ids) > 200:
            raise ValueError("批量操作须选择 1 至 200 个服务并指定 enable/disable")
        if any(not isinstance(item, str) for item in service_ids) or len(set(service_ids)) != len(service_ids):
            raise ValueError("服务 ID 必须是唯一字符串")
        categories = yaml.safe_load((settings.project_root / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
        catalog = service_metadata_for_api(settings.project_root)
        by_service = {item["id"]: item for item in catalog}
        draft = store.get("draft") or {}
        current = (draft.get("state") or {}).get("categories", {})
        changes, blockers = [], []
        for service_id in service_ids:
            spec = by_service.get(service_id)
            category = spec.get("category_id") if spec else None
            if not spec or not category or category not in categories:
                blockers.append({"service_id": service_id, "reason": "没有可编辑的运行类别"})
                continue
            desired = action == "enable"
            before = current.get(category, {}).get("enabled", categories[category].get("enabled", True))
            if desired:
                try:
                    _assert_category_runnable(category, categories, {row["id"]: row for row in catalog})
                    if spec.get("review_state") != "approved":
                        review_label = {"pending": "待审核", "manual_missing": "缺少来源"}.get(spec.get("review_state"), str(spec.get("review_state")))
                        raise BuildError(f"目录审核状态为{review_label}；批量启用仅允许已批准服务")
                except BuildError as exc:
                    blockers.append({"service_id": service_id, "category_id": category, "reason": str(exc)})
                    continue
            changes.append({"service_id": service_id, "category_id": category, "before": bool(before), "enabled": desired})
        return {"ok": not blockers, "action": action, "changes": changes, "blockers": blockers}

    @app.post("/api/categories/batch-preview")
    async def category_batch_preview(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 16 * 1024))
            return category_batch_plan(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422 if not str(exc).startswith("请求内容") else 413)

    @app.post("/api/categories/batch-apply")
    async def category_batch_apply(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 16 * 1024))
            plan = category_batch_plan(payload)
            if not plan["ok"]:
                return JSONResponse({"error": "批量操作包含未批准或不可运行服务", "plan": plan}, status_code=422)
            draft = store.get("draft")
            if not isinstance(draft, dict) or not isinstance(draft.get("state"), dict):
                return JSONResponse({"error": "请先从 GitHub 载入规则状态"}, status_code=409)
            state = dict(draft["state"])
            overrides = dict(state.get("categories", {}))
            for change in plan["changes"]:
                existing = dict(overrides.get(change["category_id"], {}))
                existing["enabled"] = change["enabled"]
                overrides[change["category_id"]] = existing
            state["categories"] = overrides
            state = validate_state(state, settings.project_root)
            store.put("draft", {"state": state, "base_sha": draft.get("base_sha"), "dirty": True})
            return {"ok": True, "synced": False, "plan": plan, "state": state}
        except (ValueError, BuildError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/exclusions")
    async def add_source_exclusion(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 16 * 1024))
            if not isinstance(payload, dict):
                return JSONResponse({"error": "请求内容必须是 JSON 对象"}, status_code=422)
            draft = store.get("draft")
            if not isinstance(draft, dict) or not isinstance(draft.get("state"), dict):
                return JSONResponse({"error": "请先从 GitHub 载入规则状态"}, status_code=409)
            item = {key: payload.get(key) for key in ("category", "domain", "match_type")}
            reviewer, reason = payload.get("reviewer"), payload.get("reason")
            if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100 or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                return JSONResponse({"error": "必须填写审核人和 500 字以内的排除理由"}, status_code=422)
            if not isinstance(item["category"], str) or not isinstance(item["domain"], str) or not isinstance(item["match_type"], str) or item["match_type"] not in {"EXACT", "SUFFIX"} or canonical_domain(item["domain"]) != item["domain"]:
                return JSONResponse({"error": "来源类别、规范域名或匹配类型无效"}, status_code=422)
            candidate = settings.data_root / "candidate"
            if not candidate.is_symlink():
                return JSONResponse({"error": "请先构建并校验候选版本"}, status_code=409)
            verify_manifest(candidate.resolve())
            matching_source = any(not row.get("custom") and row.get("category") == item["category"] and row.get("domain") == item["domain"] and row.get("match_type") == item["match_type"] for row in explain(candidate.resolve(), item["domain"]))
            if not matching_source:
                return JSONResponse({"error": "候选版本中没有匹配的上游来源规则"}, status_code=422)
            item.update({"reviewer": reviewer.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "reason": reason.strip()})
            state = dict(draft["state"])
            exclusions = list(state.get("approved_exclusions", []))
            exclusions.append(item)
            state["approved_exclusions"] = exclusions
            validate_state(state, settings.project_root)
            store.put("draft", {"state": state, "base_sha": draft.get("base_sha"), "dirty": True})
            return {"ok": True, "synced": False, "exclusion": item}
        except (ValueError, BuildError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/reload")
    async def reload_remote_state(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            await bounded_body(request, 4096)
            if store.has_running_task() or store.next_queued_task():
                return JSONResponse({"error": "已有保存、生成或目标任务正在运行，完成后再重新载入"}, status_code=409)
            client = github()
            commit = await run_in_threadpool(client.get_branch_head, "rules-data")
            content, sha = await run_in_threadpool(client.get_contents, "rules-data/state.yaml", commit)
            raw_state = yaml.safe_load(content)
            _normalized, compatibility_warnings = normalize_legacy_tiktok_state(raw_state)
            state = validate_state(raw_state, settings.project_root)
            draft = {"state": state, "base_sha": sha, "dirty": False, "compatibility_warnings": compatibility_warnings}
            store.put("draft", draft)
            store.put("synced", {"commit": commit})
            return {"state": state, "synced_commit": commit, "discarded_local_draft": True}
        except (GitHubError, OSError, ValueError, BuildError, RuntimeError, yaml.YAMLError) as exc:
            return JSONResponse({"error": f"重新载入失败：{exc}"}, status_code=503)

    @app.post("/api/save")
    async def save(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            await bounded_body(request, 4096)
        except ValueError:
            return JSONResponse({"error": "请求内容超过 4 KiB 限制"}, status_code=413)
        draft = store.get("draft") or {}
        if not draft.get("dirty"):
            return JSONResponse({"error": "没有待同步的规则修改"}, status_code=409)
        try:
            task_id = store.create_task("save", {"state": draft["state"], "expected_sha": draft.get("base_sha")}, dedupe_key=hashlib.sha256(json.dumps(draft["state"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(), exclusive=True)
            return JSONResponse({"task_id": task_id}, status_code=202)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.post("/api/import-preview")
    async def import_preview(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 128 * 1024))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=413 if "超过" in str(exc) else 400)
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), str):
            return JSONResponse({"error": "lines 必须是文本"}, status_code=422)
        lines = payload["lines"].splitlines()
        if len(lines) > 500:
            return JSONResponse({"error": "批量导入最多 500 行"}, status_code=413)
        draft = store.get("draft") or {}
        existing = {(item.get("domain"), item.get("match_type")) for item in (draft.get("state") or {}).get("rules", []) if isinstance(item, dict)}
        categories = yaml.safe_load((settings.project_root / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
        result, errors, seen = [], [], set(existing)
        for index, line in enumerate(lines, 1):
            if not line.strip():
                continue
            fields = [part.strip() for part in line.split(",")]
            rule = None
            if len(fields) == 1:
                rule = {"domain": fields[0], "match_type": "EXACT", "category": "foreign_general"}
            elif len(fields) == 2 and fields[0] in {"DOMAIN", "DOMAIN-SUFFIX"}:
                rule = {"domain": fields[1], "match_type": "EXACT" if fields[0] == "DOMAIN" else "SUFFIX", "category": "foreign_general"}
            elif len(fields) == 3 and fields[1] in {"EXACT", "SUFFIX"} and fields[2] in categories:
                rule = {"domain": fields[0], "match_type": fields[1], "category": fields[2]}
            try:
                if not rule or rule["category"] not in categories:
                    raise ValueError("格式无效或类别不存在")
                category_override = ((draft.get("state") or {}).get("categories") or {}).get(rule["category"], {})
                category_enabled = category_override.get("enabled", categories[rule["category"]].get("enabled", True))
                if not category_enabled:
                    raise ValueError("规则类别当前已禁用")
                rule["domain"] = canonical_domain(rule["domain"])
                key = (rule["domain"], rule["match_type"])
                if key in seen:
                    raise ValueError("与草稿或本次导入中的规则重复")
                seen.add(key)
                result.append({**rule, "line": index})
            except (ValueError, KeyError, BuildError, TypeError, AttributeError) as exc:
                errors.append({"line": index, "input": line[:300], "error": str(exc)})
        return {"rules": result, "errors": errors, "ok": bool(result) and not errors}

    @app.post("/api/build")
    async def build(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            await bounded_body(request, 4096)
        except ValueError:
            return JSONResponse({"error": "请求内容超过 4 KiB 限制"}, status_code=413)
        draft, synced = store.get("draft") or {}, store.get("synced") or {}
        if draft.get("dirty") or not synced.get("commit"):
            return JSONResponse({"error": "请先保存到 GitHub，并等待同步完成"}, status_code=409)
        try:
            task_id = store.create_task("build", {"data_commit": synced["commit"], "daily": False}, dedupe_key=f"build:{synced['commit']}", exclusive=True)
            return JSONResponse({"task_id": task_id}, status_code=202)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/api/tasks")
    async def task_history(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        return {"tasks": store.tasks(100)}

    @app.get("/api/deployment")
    async def deployment_status(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        return deployment_summary()

    @app.get("/api/strategies")
    async def strategies_status(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        enabled, _targets, error = target_switch_state()
        if error:
            return JSONResponse({"error": error}, status_code=409)
        busy = busy_task_summary()
        if busy:
            return JSONResponse({"error": "规则或目标任务正在排队/执行，请稍后刷新策略", "busy_task": busy}, status_code=409)
        try:
            coordinator = configured_deployment()
            catalog = await run_in_threadpool(coordinator.strategies)
            if not isinstance(catalog, dict) or catalog.get("ok") is not True:
                raise BuildError("目标没有返回有效策略目录")
            return {**catalog, "maintenance_enabled": enabled, "busy_task": None}
        except (BuildError, OSError, ValueError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)

    @app.post("/api/strategies/preview")
    async def strategies_preview(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        payload = await parse_strategy_request(request, include_preview_hash=False)
        if isinstance(payload, JSONResponse):
            return payload
        enabled, _targets, error = target_switch_state()
        if error:
            return JSONResponse({"error": error}, status_code=409)
        busy = busy_task_summary()
        if busy:
            return JSONResponse({"error": "规则或目标任务正在排队/执行，请稍后重试", "busy_task": busy}, status_code=409)
        try:
            coordinator = configured_deployment()
            result = await run_in_threadpool(coordinator.strategy_preview, payload["group"], payload["revision"], payload["members"], payload["selected"])
            return {**result, "maintenance_enabled": enabled}
        except (BuildError, OSError, ValueError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    async def queue_strategy_change(request: Request, operation: str):
        denied = await api_guard(request)
        if denied:
            return denied
        payload = await parse_strategy_request(request, include_preview_hash=operation == "apply")
        if isinstance(payload, JSONResponse):
            return payload
        enabled, _targets, error = target_switch_state()
        if error:
            return JSONResponse({"error": error}, status_code=409)
        if not enabled:
            return JSONResponse({"error": "维护模式已关闭目标操作；没有创建策略修改任务"}, status_code=409)
        busy = busy_task_summary()
        if busy:
            return JSONResponse({"error": "规则或目标任务正在排队/执行；没有创建策略修改任务", "busy_task": busy}, status_code=409)
        request_id = str(uuid.uuid4())
        kind = f"strategy_{operation}"
        try:
            task_id = store.create_task(kind, {**payload, "request_id": request_id}, exclusive=True)
            task = store.task(task_id)
            if task and task.get("payload", {}).get("request_id"):
                request_id = task["payload"]["request_id"]
            return JSONResponse({"task_id": task_id, "request_id": request_id, "operation": operation}, status_code=202)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.post("/api/strategies/select")
    async def strategies_select(request: Request):
        return await queue_strategy_change(request, "select")

    @app.post("/api/strategies/apply")
    async def strategies_apply(request: Request):
        return await queue_strategy_change(request, "apply")

    @app.post("/api/deployment/prepare")
    async def prepare_deployment(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 4096))
            if not isinstance(payload, dict) or set(payload) != {"release_id"}:
                return JSONResponse({"error": "请求必须包含 release_id"}, status_code=422)
            _path, _manifest = verified_local_release(payload["release_id"])
            if not target_switch_state()[1]:
                return JSONResponse({"error": target_switch_state()[2] or "target 配置无效"}, status_code=409)
            request_id = str(uuid.uuid4())
            intent = "prepare_only"
            dedupe_key = f"{payload['release_id']}:{intent}"
            task_id = store.create_task("prepare_deploy", {"release_id": payload["release_id"], "request_id": request_id, "intent": intent}, dedupe_key=dedupe_key, exclusive=True)
            request_id = store.task(task_id)["payload"]["request_id"]
            return JSONResponse({"task_id": task_id, "request_id": request_id}, status_code=202)
        except (ValueError, BuildError, OSError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, ValueError) and "正在运行" in str(exc) else 422)

    @app.post("/api/deployment/generate")
    async def generate_and_prepare(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            raw = await bounded_body(request, 4096)
            if raw.strip() not in {b"", b"{}"}:
                return JSONResponse({"error": "生成请求不接受候选版本参数；任务固定使用当前草稿"}, status_code=422)
            draft = store.get("draft") or {}
            if not isinstance(draft.get("state"), dict):
                return JSONResponse({"error": "请先从 GitHub 载入规则状态"}, status_code=409)
            _enabled, target_ids, config_error = target_switch_state()
            if config_error or not target_ids:
                return JSONResponse({"error": config_error or "没有配置双目标"}, status_code=409)
            frozen_state = copy.deepcopy(draft["state"])
            encoded = json.dumps(frozen_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            dedupe_key = hashlib.sha256(encoded).hexdigest()
            task_id = store.create_task("generate_prepare", {
                "phase": "save", "state": frozen_state, "expected_sha": draft.get("base_sha"),
                "draft_sha256": hashlib.sha256(encoded).hexdigest(), "intent": "generate_and_prepare_only",
            }, dedupe_key=dedupe_key, exclusive=True)
            return JSONResponse({"task_id": task_id}, status_code=202)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if "正在运行" in str(exc) else 422)

    @app.post("/api/deployment/reprepare")
    async def reprepare_release(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 4096))
            if not isinstance(payload, dict) or set(payload) != {"release_id"}:
                return JSONResponse({"error": "请求必须包含 release_id"}, status_code=422)
            _path, _manifest = verified_local_release(payload["release_id"])
            _enabled, target_ids, error = target_switch_state()
            if error or not target_ids:
                return JSONResponse({"error": error or "没有配置双目标"}, status_code=409)
            request_id = str(uuid.uuid4())
            intent = "reprepare_same_release"
            dedupe_key = f"{payload['release_id']}:{intent}"
            task_id = store.create_task("prepare_deploy", {"release_id": payload["release_id"], "request_id": request_id, "intent": intent}, dedupe_key=dedupe_key, exclusive=True)
            request_id = store.task(task_id)["payload"]["request_id"]
            return JSONResponse({"task_id": task_id, "request_id": request_id, "release_id": payload["release_id"]}, status_code=202)
        except (ValueError, BuildError, OSError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, ValueError) and "正在运行" in str(exc) else 422)

    @app.post("/api/deployment/manual")
    async def manual_deployment(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 4096))
            if not isinstance(payload, dict) or set(payload) != {"request_id"}:
                return JSONResponse({"error": "请求必须包含准备任务 request_id"}, status_code=422)
            request_id = payload["request_id"]
            try:
                if str(uuid.UUID(request_id)) != request_id:
                    raise ValueError
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("request_id 必须是规范 UUID") from exc
            enabled, _targets, error = target_switch_state()
            if error:
                return JSONResponse({"error": error}, status_code=409)
            if not enabled:
                return JSONResponse({"error": "维护禁用或尚未显式允许人工部署；没有创建部署任务"}, status_code=409)
            prep = store.get(f"target-preparation:{request_id}")
            if not isinstance(prep, dict) or prep.get("phase") != "validated":
                return JSONResponse({"error": "该 request 尚无双目标校验成功的准备记录"}, status_code=409)
            release_id = prep.get("release_id")
            _manifest_path, manifest = verified_local_release(release_id)
            attestation = store.get(f"target-attestation:{release_id}")
            if not isinstance(attestation, dict) or attestation.get("request_id") != request_id:
                return JSONResponse({"error": "准备记录没有对应的本地目标 attestation"}, status_code=409)
            expires = attestation.get("expires_at")
            if not isinstance(expires, str) or datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return JSONResponse({"error": "目标准备 attestation 已过期，请重新准备"}, status_code=409)
            authorization = {
                "release_id": release_id, "request_id": request_id,
                "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "attestation_sha256": hashlib.sha256(json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "authorized_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            task_id = store.create_task("manual_deploy", {"release_id": release_id, "request_id": request_id, "intent": "human_manual_activation", "authorization": authorization}, dedupe_key=f"manual:{request_id}", dedupe_terminal=True, exclusive=True)
            return JSONResponse({"task_id": task_id, "request_id": request_id, "release_id": release_id}, status_code=202)
        except (ValueError, BuildError, OSError, yaml.YAMLError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, ValueError) and "正在运行" in str(exc) else 422)

    @app.post("/api/deployment/rollback")
    async def rollback_deployment(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        try:
            payload = json.loads(await bounded_body(request, 4096))
            if not isinstance(payload, dict) or set(payload) != {"request_id"}:
                return JSONResponse({"error": "请求必须包含已部署版本 request_id"}, status_code=422)
            deployment_request_id = payload["request_id"]
            try:
                if str(uuid.UUID(deployment_request_id)) != deployment_request_id:
                    raise ValueError
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("request_id 必须是规范 UUID") from exc
            deployment = store.get(f"deployment:{deployment_request_id}")
            if not isinstance(deployment, dict) or deployment.get("phase") not in {"succeeded", "rolling_back"}:
                return JSONResponse({"error": "没有可回退的已部署版本"}, status_code=409)
            release_id = deployment.get("release_id")
            verified_local_release(release_id)
            task_id = store.create_task("rollback", {"release_id": release_id, "request_id": str(uuid.uuid4()), "deployment_request_id": deployment_request_id, "intent": "human_manual_rollback"}, dedupe_key=f"rollback:{deployment_request_id}", exclusive=True)
            return JSONResponse({"task_id": task_id}, status_code=202)
        except (ValueError, BuildError, OSError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, ValueError) and "正在运行" in str(exc) else 422)

    @app.get("/api/releases")
    async def release_history(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        return {"releases": store.releases(100)}

    @app.get("/api/audit")
    async def audit_export(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        return {"tasks": store.tasks(1000), "releases": store.releases(1000), "synced": store.get("synced"), "draft_dirty": bool((store.get("draft") or {}).get("dirty"))}

    @app.get("/api/explain")
    async def explain_domain(request: Request, domain: str):
        denied = await api_guard(request)
        if denied:
            return denied
        candidate = settings.data_root / "candidate"
        if not candidate.is_symlink():
            return JSONResponse({"error": "尚无已校验候选版本"}, status_code=404)
        try:
            return {"matches": explain(candidate.resolve(), domain)}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.get("/api/diff")
    async def diff_candidate(request: Request, release_id: str | None = None):
        denied = await api_guard(request)
        if denied:
            return denied
        current_candidate = settings.data_root / "candidate"
        candidate = release_id or (current_candidate.resolve().name if current_candidate.is_symlink() else None)
        try:
            _candidate_path, right = verified_local_release(candidate)
        except (ValueError, BuildError, OSError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        observed = store.get("target_actual_status") or {}
        actual = observed.get("targets", {})
        _enabled, required_targets, _error = target_switch_state()
        values = {actual.get(target_id) for target_id in required_targets} if isinstance(actual, dict) and required_targets and all(target_id in actual for target_id in required_targets) else set()
        actual_id = next(iter(values)) if len(values) == 1 else None
        baseline_id = actual_id if isinstance(actual_id, str) and re.fullmatch(r"[0-9a-f]{64}", actual_id) else None
        if baseline_id:
            try:
                _baseline_path, left = verified_local_release(baseline_id)
            except (ValueError, BuildError, OSError):
                baseline_id = None
        if not baseline_id:
            return {"release_id": right["release_id"], "baseline_release_id": None, "baseline_label": "目前无法识别两端一致且本机可验证的运行版本", "baseline_observed_at": observed.get("observed_at"), "comparison_available": False, "rule_count": right["statistics"]["rule_count"], "deployment_blockers": right.get("deployment_blockers", [])}
        before = {(row.get("domain"), row.get("match_type"), row.get("category")): row for row in left["identity_inputs"].get("normalized_rules", []) if isinstance(row, dict)}
        after = {(row.get("domain"), row.get("match_type"), row.get("category")): row for row in right["identity_inputs"].get("normalized_rules", []) if isinstance(row, dict)}
        added = [after[key] for key in sorted(after.keys() - before.keys())]
        removed = [before[key] for key in sorted(before.keys() - after.keys())]
        strategy_changes = [{"domain": key[0], "match_type": key[1], "category": key[2], "from": before[key].get("policy_group"), "to": after[key].get("policy_group")} for key in sorted(before.keys() & after.keys()) if before[key].get("policy_group") != after[key].get("policy_group")]
        before_categories = {row.get("category"): row for row in left.get("category_status", []) if isinstance(row, dict)}
        after_categories = {row.get("category"): row for row in right.get("category_status", []) if isinstance(row, dict)}
        category_changes = [{"category": key, "from": before_categories[key].get("policy_group"), "to": after_categories[key].get("policy_group")} for key in sorted(before_categories.keys() & after_categories.keys()) if before_categories[key].get("policy_group") != after_categories[key].get("policy_group")]
        limit = 50
        return {"release_id": right["release_id"], "baseline_release_id": baseline_id, "baseline_label": "两端最近一致读回版本", "baseline_observed_at": observed.get("observed_at"), "comparison_available": True,
                "rule_count_delta": len(added) - len(removed), "added_count": len(added), "removed_count": len(removed), "strategy_change_count": len(strategy_changes), "category_strategy_change_count": len(category_changes),
                "added_rules": added[:limit], "removed_rules": removed[:limit], "strategy_changes": strategy_changes[:limit], "category_strategy_changes": category_changes[:limit],
                "truncated": {"added_rules": len(added) > limit, "removed_rules": len(removed) > limit, "strategy_changes": len(strategy_changes) > limit}, "deployment_blockers": right.get("deployment_blockers", [])}

    @app.get("/api/report/{release_id}/{report_name}")
    async def report_file(request: Request, release_id: str, report_name: str):
        denied = await api_guard(request)
        if denied:
            return denied
        if not all(c in "0123456789abcdef" for c in release_id) or len(release_id) != 64 or report_name not in {"statistics.json", "shared-domains.json", "excluded-rules.json", "conflicts.json", "unsupported.json", "source-inventory.json", "categories.json", "missing-categories.json"}:
            return JSONResponse({"error": "报告路径无效"}, status_code=404)
        path = settings.data_root / "releases" / release_id / "reports" / report_name
        if not path.is_file() or path.is_symlink():
            return JSONResponse({"error": "报告不存在"}, status_code=404)
        return FileResponse(path, media_type="application/json", filename=report_name)

    @app.get("/api/files/{release_id}")
    async def candidate_files(request: Request, release_id: str):
        denied = await api_guard(request)
        if denied:
            return denied
        if len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            return JSONResponse({"error": "版本 ID 无效"}, status_code=404)
        root = settings.data_root / "releases" / release_id
        try:
            manifest = verify_manifest(root)
        except (OSError, BuildError):
            return JSONResponse({"error": "版本不存在或校验失败"}, status_code=404)
        files = sorted(name for name in manifest["artifact_sha256"] if name.startswith(("smartdns/", "mihomo/", "reports/")))
        return {"release_id": release_id, "files": files, "bundle": f"/api/bundle/{release_id}"}

    @app.get("/api/file/{release_id}/{file_path:path}")
    async def download_candidate_file(request: Request, release_id: str, file_path: str):
        denied = await api_guard(request)
        if denied:
            return denied
        if len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id) or file_path.startswith("/") or ".." in Path(file_path).parts:
            return JSONResponse({"error": "文件路径无效"}, status_code=404)
        if not file_path.startswith(("smartdns/", "mihomo/", "reports/")):
            return JSONResponse({"error": "仅允许下载规则、Provider 和报告文件"}, status_code=404)
        root = settings.data_root / "releases" / release_id
        path = (root / file_path).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
            return JSONResponse({"error": "文件不存在"}, status_code=404)
        try:
            verify_manifest(root)
        except (OSError, BuildError):
            return JSONResponse({"error": "版本校验失败，拒绝下载"}, status_code=409)
        return FileResponse(path, filename=path.name)

    @app.get("/api/bundle/{release_id}")
    async def download_candidate_bundle(request: Request, release_id: str):
        denied = await api_guard(request)
        if denied:
            return denied
        import io
        import zipfile
        if len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            return JSONResponse({"error": "版本 ID 无效"}, status_code=404)
        root = settings.data_root / "releases" / release_id
        try:
            manifest = verify_manifest(root)
        except (OSError, BuildError):
            return JSONResponse({"error": "版本不存在或校验失败"}, status_code=404)
        total = sum((root / name).stat().st_size for name in manifest["artifact_sha256"])
        if total > 128 * 1024 * 1024:
            return JSONResponse({"error": "完整规则包超过下载上限"}, status_code=413)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in sorted(manifest["artifact_sha256"]):
                archive.write(root / name, f"rules/{name}")
            archive.writestr("rules/manifest.json", (root / "manifest.json").read_bytes())
        from fastapi.responses import StreamingResponse
        output.seek(0)
        return StreamingResponse(output, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="rules-{release_id}.zip"'})

    return app

