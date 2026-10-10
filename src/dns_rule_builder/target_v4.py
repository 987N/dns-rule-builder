"""Schema-v4 target validation and a persistent local deployment simulator."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

from .engine import BuildError, canonical_domain, verify_manifest
from .store import Store


class TargetV4Error(BuildError):
    pass


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    role: str
    services: tuple[str, ...]
    smartdns_rule_path: str
    mihomo_provider_config: str | None = None
    mihomo_rules_config: str | None = None


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts) and "\\" not in value


def load_target_specs(path: Path) -> tuple[bool, tuple[TargetSpec, ...]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TargetV4Error("cannot read schema-v4 target configuration") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 4 or not isinstance(data.get("deployment_enabled"), bool) or not isinstance(data.get("targets"), dict):
        raise TargetV4Error("invalid schema-v4 target configuration")
    specs = []
    for target_id, raw in data["targets"].items():
        if not isinstance(target_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", target_id) or not isinstance(raw, dict):
            raise TargetV4Error("invalid schema-v4 target ID or descriptor")
        role, services = raw.get("role"), raw.get("services")
        if not isinstance(role, str) or role not in {"primary", "backup"} or not isinstance(services, list) or not all(isinstance(service, str) for service in services) or len(services) != len(set(services)) or not set(services) <= {"smartdns", "mihomo"} or "smartdns" not in services:
            raise TargetV4Error(f"target {target_id}: invalid role or services")
        if role == "backup" and "mihomo" in services:
            raise TargetV4Error(f"target {target_id}: backup role may manage SmartDNS only")
        if role == "primary" and "mihomo" not in services:
            raise TargetV4Error(f"target {target_id}: primary target must manage SmartDNS and Mihomo")
        for key in ("smartdns_rule_path", "mihomo_provider_config", "mihomo_rules_config"):
            value = raw.get(key)
            required = key == "smartdns_rule_path" or key.startswith("mihomo_") and "mihomo" in services
            if required and (not isinstance(value, str) or not _safe_relative(value)):
                raise TargetV4Error(f"target {target_id}: unsafe or missing {key}")
            if not required and value is not None:
                raise TargetV4Error(f"target {target_id}: unexpected {key}")
        specs.append(TargetSpec(target_id, role, tuple(services), raw["smartdns_rule_path"], raw.get("mihomo_provider_config"), raw.get("mihomo_rules_config")))
    if sorted(spec.role for spec in specs) != ["backup", "primary"]:
        raise TargetV4Error("schema-v4 requires one primary and one backup target")
    specs.sort(key=lambda spec: 0 if spec.role == "backup" else 1)
    return data["deployment_enabled"], tuple(specs)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_manifest(release_dir: Path, spec: TargetSpec) -> dict[str, Any]:
    """Create a target-specific schema-v4 payload using only verified artifacts."""
    manifest = verify_manifest(release_dir)
    if not manifest.get("deployment_allowed"):
        raise TargetV4Error("manifest blocks deployment")
    paths = [spec.smartdns_rule_path]
    provider_map = None
    if "mihomo" in spec.services:
        paths.extend([spec.mihomo_provider_config or "", spec.mihomo_rules_config or ""])
        providers = yaml.safe_load((release_dir / (spec.mihomo_provider_config or "")).read_text(encoding="utf-8"))
        rules_cfg = yaml.safe_load((release_dir / (spec.mihomo_rules_config or "")).read_text(encoding="utf-8"))
        order = manifest.get("mihomo_rule_order")
        provider_map = providers.get("rule-providers") if isinstance(providers, dict) else None
        rule_lines = rules_cfg.get("rules") if isinstance(rules_cfg, dict) else None
        if not isinstance(order, list) or not isinstance(provider_map, dict) or not isinstance(rule_lines, list):
            raise TargetV4Error("Mihomo dynamic provider configuration is invalid")
        actual_order = [line.split(",", 2)[1] for line in rule_lines if isinstance(line, str) and line.startswith("RULE-SET,")]
        if list(provider_map) != order or actual_order != order:
            raise TargetV4Error("Mihomo provider order differs from the verified manifest")
        for name in order:
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
                raise TargetV4Error("unsafe Mihomo dynamic provider name")
            entry = provider_map[name]
            if not isinstance(entry, dict) or entry.get("path") != f"./providers/{name}.yaml" or entry.get("type") != "file" or entry.get("behavior") != "classical" or entry.get("format") != "yaml":
                raise TargetV4Error(f"provider {name}: path does not match its managed file")
            paths.append(f"mihomo/providers/{name}.yaml")
    files = {}
    for relative in paths:
        if not _safe_relative(relative):
            raise TargetV4Error("target artifact path is unsafe")
        path = release_dir / relative
        if path.is_symlink() or not path.is_file() or relative not in manifest["artifact_sha256"]:
            raise TargetV4Error(f"required target artifact is missing or not managed: {relative}")
        digest = _digest(path)
        if digest != manifest["artifact_sha256"][relative]:
            raise TargetV4Error(f"target artifact hash mismatch: {relative}")
        files[relative] = digest
    for line in (release_dir / spec.smartdns_rule_path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        canonical_domain(line[2:] if line.startswith("-.") else line)
    if provider_map is not None:
        normalized = manifest["identity_inputs"]["normalized_rules"]
        for relative in (name for name in files if name.startswith("mihomo/providers/")):
            provider_data = yaml.safe_load((release_dir / relative).read_text(encoding="utf-8"))
            if not isinstance(provider_data, dict) or not isinstance(provider_data.get("payload"), list):
                raise TargetV4Error(f"invalid Mihomo provider {relative}")
            for rule in provider_data["payload"]:
                if not isinstance(rule, str) or not rule.startswith(("DOMAIN,", "DOMAIN-SUFFIX,")):
                    raise TargetV4Error(f"unsupported Mihomo rule type in {relative}")
                kind, domain = rule.split(",", 1)
                canonical_domain(domain)
                match_type = "EXACT" if kind == "DOMAIN" else "SUFFIX"
                if not any(row.get("domain") == domain and row.get("match_type") == match_type for row in normalized):
                    raise TargetV4Error(f"{match_type} rule semantic mismatch: {domain}")
    return {"schema_version": 4, "target_id": spec.target_id, "role": spec.role, "release_id": manifest["release_id"], "manifest_sha256": _digest(release_dir / "manifest.json"), "services": list(spec.services), "files": files}


class TargetHandler(Protocol):
    def stage(self, request_id: str, envelope: dict[str, Any], source_dir: Path) -> None: ...
    def validate(self, request_id: str, envelope: dict[str, Any]) -> None: ...
    def activate(self, request_id: str, envelope: dict[str, Any]) -> None: ...
    def rollback(self, request_id: str, previous_release: str | None) -> None: ...
    def current_release(self) -> str | None: ...


class DeploymentCoordinator:
    """Durable dual-target transaction runner. Production transports are intentionally absent."""
    def __init__(self, store: Store, data_root: Path, specs: tuple[TargetSpec, ...], handlers: dict[str, TargetHandler], deployment_enabled: bool = False):
        if len(specs) != 2 or sorted(spec.role for spec in specs) != ["backup", "primary"]:
            raise TargetV4Error("coordinator requires one primary and one backup target")
        ids = {spec.target_id for spec in specs}
        if len(ids) != len(specs) or set(handlers) != ids:
            raise TargetV4Error("coordinator target handlers do not match configured targets")
        for spec in specs:
            if spec.role == "backup" and spec.services != ("smartdns",):
                raise TargetV4Error("backup target must manage SmartDNS only")
            if spec.role == "primary" and set(spec.services) != {"smartdns", "mihomo"}:
                raise TargetV4Error("primary target must manage SmartDNS and Mihomo")
        self.store, self.data_root = store, data_root.resolve()
        self.specs = tuple(sorted(specs, key=lambda spec: 0 if spec.role == "backup" else 1))
        self.handlers = handlers
        self.deployment_enabled = deployment_enabled

    def run(self, release_id: str, request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id or str(uuid.uuid4())
        key = f"deployment:{request_id}"
        state = self.store.get(key)
        if state is None:
            if not self.deployment_enabled:
                raise TargetV4Error("deployment master switch is disabled")
            if not re.fullmatch(r"[0-9a-f]{64}", release_id):
                raise TargetV4Error("invalid release ID")
            release_dir = self.data_root / "releases" / release_id
            verified = verify_manifest(release_dir)
            if verified["release_id"] != release_id or not verified["deployment_allowed"]:
                raise TargetV4Error("verified candidate is not allowed for deployment")
            envelopes = {spec.target_id: target_manifest(release_dir, spec) for spec in self.specs}
            try:
                previous = {spec.target_id: self.handlers[spec.target_id].current_release() for spec in self.specs}
            except Exception as exc:
                raise TargetV4Error("target current version is unknown; manual inspection required") from exc
            state = {"request_id": request_id, "release_id": release_id, "phase": "stageboth", "completed": [], "completed_activation": [], "previous": previous, "envelopes": envelopes, "intent": None, "error": None}
            self.store.put(key, state)
        elif state["release_id"] != release_id:
            raise TargetV4Error("request ID is already bound to another release")
        if state["phase"] in {"succeeded", "failed_rolled_back", "rollback_failed", "manual_intervention"}:
            if state["phase"] == "succeeded":
                return state
            raise TargetV4Error(f"deployment is terminal at {state['phase']}; operator review is required")
        if state["phase"] == "rolling_back":
            return self._rollback(key, state, "resuming persisted rollback")
        if not self.deployment_enabled:
            if state.get("completed_activation") or state.get("phase") in {"backupactivate", "mainactivate"}:
                state["phase"] = "rolling_back"
                self.store.put(key, state)
                return self._rollback(key, state, "deployment switch disabled during an in-progress activation")
            raise TargetV4Error("deployment master switch is disabled; activation will not continue")
        try:
            for spec in self.specs:
                if spec.target_id not in state["completed"]:
                    state["intent"] = {"operation": "stage", "target_id": spec.target_id}; self.store.put(key, state)
                    self.handlers[spec.target_id].stage(request_id, state["envelopes"][spec.target_id], self.data_root / "releases" / release_id)
                    state["completed"].append(spec.target_id)
                    state["intent"] = None
                    self.store.put(key, state)
            state["phase"] = "validateboth"; self.store.put(key, state)
            for spec in self.specs:
                state["intent"] = {"operation": "validate", "target_id": spec.target_id}; self.store.put(key, state)
                self.handlers[spec.target_id].validate(request_id, state["envelopes"][spec.target_id])
            state["intent"] = None
            state["phase"] = "backupactivate"; self.store.put(key, state)
            for spec in self.specs:
                if spec.role != "backup":
                    continue
                if self.handlers[spec.target_id].current_release() != release_id:
                    state["intent"] = {"operation": "activate", "target_id": spec.target_id}; self.store.put(key, state)
                    self.handlers[spec.target_id].activate(request_id, state["envelopes"][spec.target_id])
                    if self.handlers[spec.target_id].current_release() != release_id:
                        raise RuntimeError(f"{spec.target_id} activation readback mismatch")
                state["completed_activation"] = [spec.target_id]
                state["intent"] = None
                state["phase"] = "mainactivate"; self.store.put(key, state)
            for spec in self.specs:
                if spec.role != "primary":
                    continue
                if self.handlers[spec.target_id].current_release() != release_id:
                    state["intent"] = {"operation": "activate", "target_id": spec.target_id}; self.store.put(key, state)
                    self.handlers[spec.target_id].activate(request_id, state["envelopes"][spec.target_id])
                    if self.handlers[spec.target_id].current_release() != release_id:
                        raise RuntimeError(f"{spec.target_id} activation readback mismatch")
                state["completed_activation"].append(spec.target_id)
                state["intent"] = None
            state["phase"] = "succeeded"; self.store.put(key, state)
            return state
        except Exception as exc:
            state["error"] = str(exc)[:500]
            state["phase"] = "rolling_back"; self.store.put(key, state)
            result = self._rollback(key, state, str(exc))
            raise TargetV4Error(f"deployment ended in {result['phase']}: {exc}") from exc

    def _rollback(self, key: str, state: dict[str, Any], cause: str) -> dict[str, Any]:
        state["phase"] = "rolling_back"
        state["rollback_cause"] = cause[:500]
        self.store.put(key, state)
        observed = {}
        try:
            for spec in reversed(self.specs):
                observed[spec.target_id] = self.handlers[spec.target_id].current_release()
        except Exception as exc:
            state["phase"] = "manual_intervention"
            state["rollback_errors"] = [f"cannot determine target version: {type(exc).__name__}"]
            state["intent"] = {"operation": "inspect", "status": "unknown"}
            self.store.put(key, state)
            return state
        failures = []
        unknown = False
        for spec in reversed(self.specs):
            target_id = spec.target_id
            current, previous = observed[target_id], state["previous"].get(target_id)
            if current == previous:
                continue
            state["intent"] = {"operation": "rollback", "target_id": target_id, "expected_previous": previous}
            self.store.put(key, state)
            try:
                self.handlers[target_id].rollback(state["request_id"], previous)
            except Exception as exc:
                failures.append(f"{target_id}: {type(exc).__name__}")
                state["intent"] = {"operation": "rollback", "target_id": target_id, "status": "failed"}
                self.store.put(key, state)
                break
            try:
                actual = self.handlers[target_id].current_release()
                if actual != previous:
                    raise RuntimeError("rollback readback mismatch")
            except Exception as exc:
                failures.append(f"{target_id}: {type(exc).__name__}")
                state["intent"] = {"operation": "rollback", "target_id": target_id, "status": "unknown"}
                self.store.put(key, state)
                unknown = True
                break
            state["intent"] = None
            self.store.put(key, state)
        state["rollback_errors"] = failures
        state["phase"] = "manual_intervention" if unknown else "rollback_failed" if failures else "failed_rolled_back"
        self.store.put(key, state)
        return state


class SimulatedTarget:
    """Fault-injectable local target; it never contacts or changes a device."""
    def __init__(self, target_id: str, current: str | None = None, fail_at: str | None = None, fail_rollback: bool = False, events: list | None = None):
        self.target_id, self.current, self.fail_at, self.fail_rollback = target_id, current, fail_at, fail_rollback
        self.events = events
        self.staged: dict[str, str] = {}
        self.calls: list[str] = []

    def _fail(self, phase: str):
        self.calls.append(phase)
        if self.events is not None:
            self.events.append((self.target_id, phase))
        if self.fail_at == phase:
            raise RuntimeError(f"simulated {self.target_id} {phase} failure")

    def stage(self, request_id: str, envelope: dict[str, Any], source_dir: Path) -> None:
        self._fail("stage")
        for relative, digest in envelope["files"].items():
            path = source_dir / relative
            if path.is_symlink() or not path.is_file() or _digest(path) != digest:
                raise TargetV4Error("staged file verification failed")
        self.staged[request_id] = envelope["release_id"]

    def validate(self, request_id: str, envelope: dict[str, Any]) -> None:
        self._fail("validate")
        if self.staged.get(request_id) != envelope["release_id"]:
            raise RuntimeError("release not staged")

    def activate(self, request_id: str, envelope: dict[str, Any]) -> None:
        self._fail("activate")
        if self.staged.get(request_id) != envelope["release_id"]:
            raise RuntimeError("release not staged")
        self.current = envelope["release_id"]

    def rollback(self, request_id: str, previous_release: str | None) -> None:
        if self.fail_rollback:
            self.calls.append("rollback")
            if self.events is not None:
                self.events.append((self.target_id, "rollback"))
            raise RuntimeError(f"simulated {self.target_id} rollback failure")
        self.calls.append("rollback")
        if self.events is not None:
            self.events.append((self.target_id, "rollback"))
        self.current = previous_release

    def current_release(self) -> str | None:
        return self.current
