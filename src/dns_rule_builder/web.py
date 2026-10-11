"""Single-process FastAPI web UI, auth boundary, and task API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import copy
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


def create_app(settings: Settings | None = None, client_factory=None, start_worker: bool = True) -> FastAPI:
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

    runner = TaskRunner(store, settings.project_root, settings.data_root, github, start_thread=start_worker)
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
        return {"state": normalized_state, "dirty": draft.get("dirty", False), "base_sha": draft.get("base_sha"), "synced_commit": (store.get("synced") or {}).get("commit"), "remote_status": store.get("remote_status"), "categories": categories, "policies": sorted(policies), "service_catalog": service_metadata_for_api(settings.project_root), "compatibility_warnings": compatibility_warnings, "tasks": store.tasks(20), "releases": store.releases(10), "candidate_release_id": candidate_release_id, "csrf": request.session["csrf"]}

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
        task_id = store.create_task("save", {"state": draft["state"], "expected_sha": draft.get("base_sha")})
        return JSONResponse({"task_id": task_id}, status_code=202)

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
        task_id = store.create_task("build", {"data_commit": synced["commit"], "daily": False})
        return JSONResponse({"task_id": task_id}, status_code=202)

    @app.get("/api/tasks")
    async def task_history(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        return {"tasks": store.tasks(100)}

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
    async def diff_candidate(request: Request):
        denied = await api_guard(request)
        if denied:
            return denied
        candidate, previous = settings.data_root / "candidate", settings.data_root / "previous"
        if not candidate.is_symlink() or not previous.is_symlink():
            return JSONResponse({"error": "需要两个已校验版本才能比较"}, status_code=404)
        left, right = verify_manifest(previous.resolve()), verify_manifest(candidate.resolve())
        return {"previous": left["release_id"], "candidate": right["release_id"], "rule_count_delta": right["statistics"]["rule_count"] - left["statistics"]["rule_count"], "changed_artifacts": sorted(name for name in set(left["artifact_sha256"]) & set(right["artifact_sha256"]) if left["artifact_sha256"][name] != right["artifact_sha256"][name]), "added_artifacts": sorted(set(right["artifact_sha256"]) - set(left["artifact_sha256"])), "removed_artifacts": sorted(set(left["artifact_sha256"]) - set(right["artifact_sha256"]))}

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

