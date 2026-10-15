"""Schema-v4 target validation and a persistent local deployment simulator."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

from .engine import BuildError, canonical_domain, verify_manifest
from .store import Store
from .target_attestation import (
    AttestationError,
    check_preparation_candidate,
    create_attestation,
    sha256_json,
    verify_attestation,
)


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
    ssh_host: str | None = None
    ssh_user: str | None = None
    ssh_identity_file: str | None = None
    ssh_known_hosts_file: str | None = None
    ssh_port: int = 22
    ssh_connect_timeout_seconds: int = 15
    ssh_operation_timeout_seconds: int = 420


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts) and "\\" not in value


def load_target_specs(path: Path) -> tuple[bool, tuple[TargetSpec, ...]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TargetV4Error("cannot read schema-v4 target configuration") from exc
    legacy_enabled = data.get("deployment_enabled") if isinstance(data, dict) else None
    maintenance_disabled = data.get("maintenance_disabled") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("schema_version") != 4 or not isinstance(legacy_enabled, bool)
            or ("maintenance_disabled" in data and not isinstance(maintenance_disabled, bool))
            or ("automatic_deployment_enabled" in data and not isinstance(data["automatic_deployment_enabled"], bool))
            or not isinstance(data.get("targets"), dict)):
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
        ssh = raw.get("ssh", {})
        if not isinstance(ssh, dict):
            raise TargetV4Error(f"target {target_id}: invalid SSH configuration")
        host, user = ssh.get("host"), ssh.get("user")
        identity_file, known_hosts_file = ssh.get("identity_file"), ssh.get("known_hosts_file")
        if ssh and (
            not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}", host)
            or not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}", user)
            or not isinstance(identity_file, str) or not re.fullmatch(r"/run/secrets/[A-Za-z0-9_.-]{1,128}", identity_file)
            or not isinstance(known_hosts_file, str) or not re.fullmatch(r"/run/secrets/[A-Za-z0-9_.-]{1,128}", known_hosts_file)
        ):
            raise TargetV4Error(f"target {target_id}: SSH host, user, or secret-file paths are invalid")
        port = ssh.get("port", 22)
        connect_timeout = ssh.get("connect_timeout_seconds", 15)
        operation_timeout = ssh.get("operation_timeout_seconds", 420)
        if (type(port) is not int or not 1 <= port <= 65535 or type(connect_timeout) is not int or not 1 <= connect_timeout <= 120
                or type(operation_timeout) is not int or not connect_timeout <= operation_timeout <= 1800):
            raise TargetV4Error(f"target {target_id}: invalid SSH timeout or port")
        specs.append(TargetSpec(
            target_id, role, tuple(services), raw["smartdns_rule_path"], raw.get("mihomo_provider_config"), raw.get("mihomo_rules_config"),
            host, user, identity_file, known_hosts_file, port, connect_timeout, operation_timeout,
        ))
    if sorted(spec.role for spec in specs) != ["backup", "primary"]:
        raise TargetV4Error("schema-v4 requires one primary and one backup target")
    specs.sort(key=lambda spec: 0 if spec.role == "backup" else 1)
    # The legacy switch remains available to resume old persisted tasks only. The
    # web admission path separately requires an explicit maintenance_disabled: false.
    return (not maintenance_disabled if maintenance_disabled is not None else legacy_enabled), tuple(specs)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _direct_exceptions() -> list[dict[str, str]]:
    path = Path(__file__).resolve().parents[2] / "config/target-direct-exceptions.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TargetV4Error("cannot load the target direct-exception baseline") from exc
    rows = data.get("entries") if isinstance(data, dict) and data.get("schema_version") == 1 else None
    if not isinstance(rows, list) or len(rows) != 14:
        raise TargetV4Error("target direct-exception baseline is invalid")
    expected = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"domain", "match_type"}:
            raise TargetV4Error("target direct-exception entry is invalid")
        try:
            domain = canonical_domain(row["domain"])
        except (TypeError, ValueError) as exc:
            raise TargetV4Error("target direct-exception domain is invalid") from exc
        if row["match_type"] not in {"EXACT", "SUFFIX"}:
            raise TargetV4Error("target direct-exception type is invalid")
        expected.append({"domain": domain, "match_type": row["match_type"]})
    if len({(row["domain"], row["match_type"]) for row in expected}) != 14:
        raise TargetV4Error("target direct-exception baseline contains duplicates")
    return sorted(expected, key=lambda row: (row["match_type"], row["domain"]))


def target_manifest(release_dir: Path, spec: TargetSpec, *, allow_preparation: bool = False) -> dict[str, Any]:
    """Create a target-specific schema-v4 payload using only verified artifacts."""
    manifest = verify_manifest(release_dir)
    if not manifest.get("deployment_allowed"):
        if not allow_preparation:
            raise TargetV4Error("manifest blocks deployment")
        try:
            check_preparation_candidate(manifest)
        except AttestationError as exc:
            raise TargetV4Error(str(exc)) from exc
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
    regional_groups = ["香港自动策略", "台湾自动策略", "日本自动策略", "韩国自动策略", "新国自动策略", "美国自动策略", "英国自动策略", "法国自动策略", "德国自动策略"]
    if provider_map is not None:
        provider_policy_map = {item["name"]: item["policy_group"] for item in manifest["provider_map"]}
    else:
        provider_map = []
        provider_policy_map = {}
    direct_exceptions = _direct_exceptions()
    is_mihomo = "mihomo" in spec.services
    return {"schema_version": 4, "target_id": spec.target_id, "role": spec.role, "release_id": manifest["release_id"], "manifest_sha256": _digest(release_dir / "manifest.json"), "services": list(spec.services), "files": files, "mihomo_rule_order": manifest.get("mihomo_rule_order", []) if is_mihomo else [], "provider_map": manifest.get("provider_map", []) if is_mihomo else [], "provider_policy_map": provider_policy_map, "regional_groups": regional_groups if is_mihomo else [], "direct_exceptions": direct_exceptions, "direct_exceptions_sha256": hashlib.sha256(json.dumps(direct_exceptions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


class TargetHandler(Protocol):
    def stage(self, request_id: str, envelope: dict[str, Any], source_dir: Path) -> None: ...
    def validate(self, request_id: str, envelope: dict[str, Any]) -> None: ...
    def activate(self, request_id: str, envelope: dict[str, Any]) -> None: ...
    def rollback(self, request_id: str, previous_release: str | None) -> None: ...
    def current_release(self) -> str | None: ...


class DeploymentCoordinator:
    """Durable dual-target preflight and manually gated deployment runner."""
    def __init__(self, store: Store, data_root: Path, specs: tuple[TargetSpec, ...], handlers: dict[str, TargetHandler], deployment_enabled: bool = False, attestation_ttl_seconds: int = 3600):
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
        self.attestation_ttl_seconds = attestation_ttl_seconds

    @staticmethod
    def _request_id(value: str | None) -> str:
        request_id = value or str(uuid.uuid4())
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise TargetV4Error("request ID must be a canonical UUID") from exc
        return request_id

    def _candidate(self, release_id: str) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]:
        if not re.fullmatch(r"[0-9a-f]{64}", release_id):
            raise TargetV4Error("invalid release ID")
        release_dir = self.data_root / "releases" / release_id
        manifest = verify_manifest(release_dir)
        if manifest.get("release_id") != release_id:
            raise TargetV4Error("verified candidate release ID does not match its path")
        try:
            check_preparation_candidate(manifest)
        except AttestationError as exc:
            raise TargetV4Error(str(exc)) from exc
        envelopes = {spec.target_id: target_manifest(release_dir, spec, allow_preparation=True) for spec in self.specs}
        return release_dir, manifest, envelopes

    def prepare(self, release_id: str, request_id: str | None = None) -> dict[str, Any]:
        """Stage and validate both targets, recording evidence without activation."""
        request_id = self._request_id(request_id)
        key = f"target-preparation:{request_id}"
        state = self.store.get(key)
        if state is not None:
            if state.get("release_id") != release_id:
                raise TargetV4Error("request ID is already bound to another release")
            if state.get("phase") == "validated":
                return state
            if state.get("phase") in {"failed", "manual_intervention"}:
                raise TargetV4Error(f"preparation is terminal at {state['phase']}; inspect target state")
        release_dir, manifest, envelopes = self._candidate(release_id)
        if state is None:
            previous = {}
            try:
                for spec in self.specs:
                    previous[spec.target_id] = self.handlers[spec.target_id].current_release()
            except Exception as exc:
                raise TargetV4Error("target current version is unknown; manual inspection required") from exc
            state = {
                "request_id": request_id,
                "release_id": release_id,
                "manifest_sha256": _digest(release_dir / "manifest.json"),
                "phase": "stageboth",
                "completed_stage": [],
                "completed_validate": [],
                "previous": previous,
                "envelopes": envelopes,
                "validation_evidence": {},
                "intent": None,
                "error": None,
            }
            self.store.put(key, state)
        try:
            for spec in self.specs:
                if spec.target_id in state["completed_stage"]:
                    continue
                state["intent"] = {"operation": "stage", "target_id": spec.target_id}
                self.store.put(key, state)
                self.handlers[spec.target_id].stage(request_id, state["envelopes"][spec.target_id], release_dir)
                state["completed_stage"].append(spec.target_id)
                state["intent"] = None
                self.store.put(key, state)
            state["phase"] = "validateboth"
            self.store.put(key, state)
            for spec in self.specs:
                if spec.target_id in state["completed_validate"]:
                    continue
                state["intent"] = {"operation": "validate", "target_id": spec.target_id}
                self.store.put(key, state)
                response = self.handlers[spec.target_id].validate(request_id, state["envelopes"][spec.target_id])
                if not isinstance(response, dict):
                    raise TargetV4Error(f"{spec.target_id}: validation did not return attestation evidence")
                state["validation_evidence"][spec.target_id] = response
                state["completed_validate"].append(spec.target_id)
                state["intent"] = None
                self.store.put(key, state)
            attestation = create_attestation(
                request_id=request_id,
                release_id=release_id,
                manifest_sha256=state["manifest_sha256"],
                envelopes=state["envelopes"],
                previous_releases=state["previous"],
                validation_evidence=state["validation_evidence"],
                ttl_seconds=self.attestation_ttl_seconds,
            )
            self.store.put(f"target-attestation:{release_id}", attestation)
            state["attestation_sha256"] = attestation["attestation_sha256"]
            state["phase"] = "validated"
            state["intent"] = None
            self.store.put(key, state)
            return state
        except Exception as exc:
            state["error"] = str(exc)[:500]
            state["phase"] = "failed"
            self.store.put(key, state)
            raise

    def run(self, release_id: str, request_id: str | None = None) -> dict[str, Any]:
        request_id = self._request_id(request_id)
        key = f"deployment:{request_id}"
        state = self.store.get(key)
        if state is not None and state["release_id"] != release_id:
            raise TargetV4Error("request ID is already bound to another release")
        if state is not None and state["phase"] in {"succeeded", "failed_rolled_back", "rollback_failed", "manual_intervention"}:
            if state["phase"] == "succeeded":
                return state
            raise TargetV4Error(f"deployment is terminal at {state['phase']}; operator review is required")
        if state is not None and state["phase"] == "rolling_back":
            return self._rollback(key, state, "resuming persisted rollback")
        activation_started = bool(state is not None and (
            state.get("completed_activation")
            or (isinstance(state.get("intent"), dict) and state["intent"].get("operation") == "activate")
        ))
        if not self.deployment_enabled:
            if activation_started:
                state["phase"] = "rolling_back"
                self.store.put(key, state)
                return self._rollback(key, state, "deployment switch disabled during an in-progress activation")
            raise TargetV4Error("deployment master switch is disabled; activation will not continue")
        try:
            release_dir, manifest, envelopes = self._candidate(release_id)
            attestation = self.store.get(f"target-attestation:{release_id}")
            if not isinstance(attestation, dict):
                raise TargetV4Error("fresh target validation attestation is required before activation")
            verify_attestation(attestation, release_id=release_id, manifest_sha256=_digest(release_dir / "manifest.json"), envelopes=envelopes)
            # Re-read target-side binaries/configs immediately before activation so
            # the preflight cannot authorize a changed host configuration.
            for spec in self.specs:
                fresh = self.handlers[spec.target_id].validate(request_id, envelopes[spec.target_id])
                row = attestation["targets"][spec.target_id]
                if (not isinstance(fresh, dict) or fresh.get("result") != "ok"
                        or fresh.get("target_manifest_sha256") != row.get("target_manifest_sha256")
                        or fresh.get("binary_sha256") != row.get("binary_sha256")
                        or fresh.get("config_sha256") != row.get("config_sha256")
                        or fresh.get("strategy_validation_sha256") != row.get("strategy_validation_sha256")):
                    raise TargetV4Error(f"{spec.target_id}: target evidence changed after preflight; prepare both targets again")
            if state is not None and state.get("attestation_sha256") != attestation.get("attestation_sha256"):
                raise TargetV4Error("deployment request attestation changed; operator review is required")
            if state is None:
                previous = {target_id: row.get("previous_release_id") for target_id, row in attestation["targets"].items()}
                observed = {spec.target_id: self.handlers[spec.target_id].current_release() for spec in self.specs}
                if observed != previous:
                    raise TargetV4Error("target versions changed after validation; prepare both targets again")
        except Exception as exc:
            if activation_started:
                state["phase"] = "rolling_back"
                self.store.put(key, state)
                return self._rollback(key, state, f"activation recovery preflight failed: {type(exc).__name__}: {exc}")
            if isinstance(exc, TargetV4Error):
                raise
            if isinstance(exc, AttestationError):
                raise TargetV4Error(str(exc)) from exc
            raise TargetV4Error(f"activation preflight failed: {type(exc).__name__}") from exc
        if state is None:
            previous = {target_id: row.get("previous_release_id") for target_id, row in attestation["targets"].items()}
            state = {
                "request_id": request_id,
                "release_id": release_id,
                "phase": "backupactivate",
                "completed_activation": [],
                "previous": previous,
                "envelopes": envelopes,
                "attestation_sha256": attestation["attestation_sha256"],
                "intent": None,
                "error": None,
            }
            self.store.put(key, state)
        try:
            for spec in self.specs:
                current = self.handlers[spec.target_id].current_release()
                if current == release_id:
                    if spec.target_id not in state["completed_activation"]:
                        state["completed_activation"].append(spec.target_id)
                        self.store.put(key, state)
                    continue
                if current != state["previous"].get(spec.target_id):
                    state["phase"] = "manual_intervention"
                    state["intent"] = {"operation": "inspect", "target_id": spec.target_id, "status": "unexpected-current-release"}
                    self.store.put(key, state)
                    raise TargetV4Error(f"{spec.target_id}: target changed after preflight; manual inspection required")
                state["intent"] = {"operation": "activate", "target_id": spec.target_id}
                attempted = state.setdefault("attempted_activation", [])
                if spec.target_id not in attempted:
                    attempted.append(spec.target_id)
                state["phase"] = "backupactivate" if spec.role == "backup" else "mainactivate"
                self.store.put(key, state)
                self.handlers[spec.target_id].activate(request_id, state["envelopes"][spec.target_id])
                if self.handlers[spec.target_id].current_release() != release_id:
                    raise RuntimeError(f"{spec.target_id} activation readback mismatch")
                state["completed_activation"].append(spec.target_id)
                state["intent"] = None
                self.store.put(key, state)
            state["phase"] = "succeeded"; self.store.put(key, state)
            return state
        except Exception as exc:
            state["error"] = str(exc)[:500]
            state["phase"] = "rolling_back"; self.store.put(key, state)
            result = self._rollback(key, state, str(exc))
            raise TargetV4Error(f"deployment ended in {result['phase']}: {exc}") from exc

    def rollback_active(self, request_id: str) -> dict[str, Any]:
        """Manually roll back a persisted deployment after verifying live target IDs."""
        request_id = self._request_id(request_id)
        key = f"deployment:{request_id}"
        state = self.store.get(key)
        if not isinstance(state, dict) or state.get("phase") not in {"succeeded", "rolling_back", "manual_intervention"}:
            raise TargetV4Error("only a successful or interrupted deployment can be rolled back")
        if state.get("phase") == "manual_intervention":
            return self._rollback(key, state, "operator requested interrupted deployment recovery", explicit_recovery=True)
        if state.get("phase") == "succeeded":
            try:
                observed = {spec.target_id: self.handlers[spec.target_id].current_release() for spec in self.specs}
            except Exception as exc:
                state["phase"] = "manual_intervention"
                state["intent"] = {"operation": "inspect", "status": "unknown"}
                state["error"] = f"cannot determine target version: {type(exc).__name__}"
                self.store.put(key, state)
                raise TargetV4Error("target version is unknown; manual inspection is required") from exc
            if any(observed.get(spec.target_id) != state.get("release_id") for spec in self.specs):
                state["phase"] = "manual_intervention"
                state["intent"] = {"operation": "inspect", "status": "unexpected-current-release"}
                state["observed"] = observed
                self.store.put(key, state)
                raise TargetV4Error("target versions differ from the deployment; manual inspection is required")
            state["phase"] = "rolling_back"
            state["intent"] = {"operation": "rollback", "status": "requested"}
            self.store.put(key, state)
        return self._rollback(key, state, "operator requested rollback")

    def _rollback(self, key: str, state: dict[str, Any], cause: str, *, explicit_recovery: bool = False) -> dict[str, Any]:
        state["phase"] = "rolling_back"
        state["rollback_cause"] = cause[:500]
        self.store.put(key, state)
        observed = {}
        interrupted = set()
        try:
            for spec in reversed(self.specs):
                handler = self.handlers[spec.target_id]
                try:
                    observed[spec.target_id] = handler.current_release()
                except Exception:
                    probe = getattr(handler, "recovery_status", None)
                    if not callable(probe) or not (explicit_recovery or spec.target_id in state.get("attempted_activation", [])):
                        raise
                    previous = state.get("previous", {}).get(spec.target_id)
                    evidence = probe(state["request_id"], state["release_id"], previous)
                    if (not isinstance(evidence, dict) or evidence.get("ok") is not True or evidence.get("recovery_safe") is not True
                            or evidence.get("target_id") != spec.target_id or evidence.get("release_id") != state["release_id"]
                            or evidence.get("previous_release_id") != previous):
                        raise TargetV4Error("interrupted target recovery evidence does not match deployment")
                    interrupted.add(spec.target_id)
                    observed[spec.target_id] = None
                    state.setdefault("recovery_evidence", {})[spec.target_id] = evidence
                    self.store.put(key, state)
        except Exception as exc:
            state["phase"] = "manual_intervention"
            state["rollback_errors"] = [f"cannot determine target version: {type(exc).__name__}"]
            state["intent"] = {"operation": "inspect", "status": "unknown"}
            self.store.put(key, state)
            return state
        failures = []
        unknown = False
        unexpected = {
            target_id: current
            for target_id, current in observed.items()
            if target_id not in interrupted and current not in {state.get("previous", {}).get(target_id), state.get("release_id")}
        }
        if unexpected:
            state["phase"] = "manual_intervention"
            state["rollback_errors"] = [f"{target_id}: unexpected current release" for target_id in sorted(unexpected)]
            state["intent"] = {"operation": "inspect", "status": "unexpected-current-release"}
            state["observed"] = observed
            self.store.put(key, state)
            return state
        for spec in reversed(self.specs):
            target_id = spec.target_id
            current, previous = observed[target_id], state["previous"].get(target_id)
            if target_id not in interrupted and current == previous:
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


    def _mihomo_handler(self):
        spec = next((item for item in self.specs if item.role == "primary" and "mihomo" in item.services), None)
        if spec is None:
            raise TargetV4Error("no primary Mihomo target is configured")
        handler = self.handlers[spec.target_id]
        if not callable(getattr(handler, "strategies", None)):
            raise TargetV4Error("primary target does not support managed strategy operations")
        return handler

    def strategies(self) -> dict[str, Any]:
        return self._mihomo_handler().strategies()

    def strategy_preview(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any]) -> dict[str, Any]:
        return self._mihomo_handler().strategy_preview(group, revision, members, selected)

    def strategy_select(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any], preview_sha256: str) -> dict[str, Any]:
        return self._mihomo_handler().strategy_select(group, revision, members, selected, preview_sha256)

    def strategy_apply(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any], preview_sha256: str) -> dict[str, Any]:
        return self._mihomo_handler().strategy_apply(group, revision, members, selected, preview_sha256)


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
        self.staged[envelope["release_id"]] = envelope["release_id"]

    def validate(self, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        self._fail("validate")
        if self.staged.get(envelope["release_id"]) != envelope["release_id"]:
            raise RuntimeError("release not staged")
        digest = hashlib.sha256(f"{self.target_id}:{envelope['release_id']}:simulated".encode()).hexdigest()
        return {
            "result": "ok", "target_id": self.target_id, "release_id": envelope["release_id"],
            "target_manifest_sha256": sha256_json(envelope),
            "binary_sha256": {"simulated": digest}, "config_sha256": {"simulated": digest},
            "strategy_validation_sha256": digest,
        }

    def activate(self, request_id: str, envelope: dict[str, Any]) -> None:
        self._fail("activate")
        if self.staged.get(envelope["release_id"]) != envelope["release_id"]:
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
