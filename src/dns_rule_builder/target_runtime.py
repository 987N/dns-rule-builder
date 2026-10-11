"""Restricted runtime for the separately installed schema-v4 target agent."""

from __future__ import annotations

import hashlib
import grp
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

class TargetV4Error(RuntimeError):
    pass


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts) and "\\" not in value

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILES = 512
MAX_FILE = 16 * 1024 * 1024
MAX_HEADER = 128 * 1024


class TargetRuntimeError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


DIRECT_PROVIDER_NAMES = {"LAN", "Direct", "ChinaDownloadCDN", "iCloudChina", "ApplePushNotificationService", "AppleSoftwareUpdates", "ESET_China"}
REGIONAL_GROUPS = ("香港自动策略", "台湾自动策略", "日本自动策略", "韩国自动策略", "新国自动策略", "美国自动策略", "英国自动策略", "法国自动策略", "德国自动策略")


_SMARTDNS_CONFIG_ERROR_PATTERNS = (
    rb"\bparse(?:\s+|_)(?:error|failed|failure)\b",
    rb"\binvalid\s+(?:config(?:uration)?|domain|option|rule)\b",
    rb"\bunknown\s+(?:config\s+)?option\b",
    rb"\bfailed\s+to\s+(?:load|parse|read)\b",
    rb"\b(?:config(?:uration)?|domain|option)\s+(?:parse\s+)?(?:error|invalid)\b",
)


def _smartdns_reports_config_error(*outputs: bytes | str | None) -> bool:
    combined = b" ".join(
        value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
        for value in outputs
        if isinstance(value, (bytes, str))
    ).lower()
    return any(re.search(pattern, combined) for pattern in _SMARTDNS_CONFIG_ERROR_PATTERNS)


def _json_hash(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def _direct_exception_rows(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) != 14:
        raise TargetRuntimeError("direct-exception baseline must contain exactly 14 entries")
    rows = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"domain", "match_type"}:
            raise TargetRuntimeError("invalid direct-exception entry")
        domain, kind = item["domain"], item["match_type"]
        if not isinstance(domain, str) or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain):
            raise TargetRuntimeError("invalid direct-exception domain")
        if kind not in {"EXACT", "SUFFIX"}:
            raise TargetRuntimeError("invalid direct-exception match type")
        rows.append({"domain": domain, "match_type": kind})
    if len({(row["domain"], row["match_type"]) for row in rows}) != 14:
        raise TargetRuntimeError("duplicate direct-exception entry")
    return sorted(rows, key=lambda row: (row["match_type"], row["domain"]))


def _load_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
        raise TargetRuntimeError("target agent config must be a regular non-group/world-writable file")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TargetRuntimeError("cannot load target agent configuration") from exc
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise TargetRuntimeError("invalid target agent configuration")
    targets = config.get("targets")
    if not isinstance(targets, dict):
        raise TargetRuntimeError("target agent configuration has no targets")
    if len(targets) != 1:
        raise TargetRuntimeError("each target agent installation must be pinned to exactly one local target")
    if set(config) != {"schema_version", "default_target_id", "targets"}:
        raise TargetRuntimeError("target agent configuration has unknown fields")
    if not isinstance(config.get("default_target_id"), str) or config["default_target_id"] not in targets:
        raise TargetRuntimeError("default target is not configured")
    return config


def _trusted_path(value: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise TargetRuntimeError("runtime paths must be absolute and locally configured")
    path = Path(value)
    if ".." in path.parts:
        raise TargetRuntimeError("runtime path contains traversal")
    # Reject symlinks in every existing component before resolving.
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise TargetRuntimeError("runtime path traverses a symlink")
    return path.resolve(strict=False)


class TargetRuntime:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = _load_config(config_path)

    def _target(self, target_id: str) -> dict[str, Any]:
        stored = self.config["targets"].get(target_id)
        if not isinstance(stored, dict):
            raise TargetRuntimeError("target ID is not configured on this agent")
        raw = dict(stored)
        required = {"role", "services", "managed_root", "legacy_link", "allowed_files", "binaries", "configs", "validate_commands", "systemd_units", "integration"}
        allowed = required | {"allowed_file_prefixes", "validation_environment", "validation_timeout_seconds", "validation_exit_codes", "restart", "status_commands", "binary_probe_commands", "smartdns_validator_stays_running", "managed_group", "managed_uid", "mihomo_controller_url", "guard_files", "service_readability_commands"}
        if not required <= set(raw) or not set(raw) <= allowed:
            raise TargetRuntimeError("target agent descriptor fields are incomplete or unknown")
        if raw.get("role") not in {"primary", "backup"} or raw.get("services") not in (["smartdns"], ["smartdns", "mihomo"]):
            raise TargetRuntimeError("target agent role/service contract is invalid")
        root_raw = raw.get("managed_root")
        if not isinstance(root_raw, str) or not root_raw.startswith("/"):
            raise TargetRuntimeError("managed root is not configured")
        root = Path(root_raw)
        _trusted_path(root_raw)
        raw["_target_id"] = target_id
        if not isinstance(raw.get("legacy_link"), str) or not raw["legacy_link"].startswith("/"):
            raise TargetRuntimeError("legacy schema-v3 link path is not configured")
        _trusted_path(str(Path(raw["legacy_link"]).parent))
        integration = dict(raw["integration"]) if isinstance(raw["integration"], dict) else raw["integration"]
        if not isinstance(integration, dict) or set(integration) != {"smartdns_config", "mihomo_config", "smartdns_rules_root", "mihomo_rules_root", "smartdns_nameserver_line", "smartdns_address_line", "direct_exceptions"}:
            raise TargetRuntimeError("target integration contract is incomplete")
        if (not isinstance(integration.get("smartdns_config"), str) or not integration["smartdns_config"].startswith("/")
                or not isinstance(integration.get("smartdns_rules_root"), str) or not integration["smartdns_rules_root"].startswith("/")
                or not isinstance(integration.get("smartdns_nameserver_line"), str)
                or integration.get("mihomo_config") is not None and (not isinstance(integration["mihomo_config"], str) or not integration["mihomo_config"].startswith("/"))
                or integration.get("mihomo_rules_root") is not None and (not isinstance(integration["mihomo_rules_root"], str) or not integration["mihomo_rules_root"].startswith("/"))):
            raise TargetRuntimeError("target integration paths or directives are invalid")
        controller_url = raw.get("mihomo_controller_url")
        if "mihomo" in raw["services"]:
            parsed = urllib.parse.urlparse(controller_url) if isinstance(controller_url, str) else None
            if (parsed is None or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path not in {"", "/"} or parsed.port is None or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise TargetRuntimeError("Mihomo controller endpoint must be a loopback HTTP origin")
        integration["direct_exceptions"] = _direct_exception_rows(integration.get("direct_exceptions"))
        raw["integration"] = integration
        if ("mihomo" in raw["services"]) != (integration.get("mihomo_config") is not None and integration.get("mihomo_rules_root") is not None):
            raise TargetRuntimeError("Mihomo integration does not match target services")
        raw["_root"] = root
        return raw

    def _release_dir(self, target: dict[str, Any], release_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", release_id):
            raise TargetRuntimeError("invalid release ID")
        return target["_root"] / "releases" / release_id

    def _current(self, target: dict[str, Any]) -> str | None:
        intent = target["_root"] / "activation-intent.json"
        if intent.exists():
            raise TargetRuntimeError("interrupted activation requires explicit rollback or manual inspection")
        marker = target["_root"] / "active-release.json"
        pointer = target["_root"] / "current"
        if marker.exists() or marker.is_symlink() or pointer.exists() or pointer.is_symlink():
            if not marker.is_file() or marker.is_symlink():
                raise TargetRuntimeError("managed current pointer has no valid active release marker")
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise TargetRuntimeError("active target release marker has an invalid structure")
                release_id = value.get("release_id")
                if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id):
                    raise TargetRuntimeError("active target release marker has an invalid release ID")
                if not pointer.is_symlink() or os.readlink(pointer) != f"releases/{release_id}":
                    raise TargetRuntimeError("active marker and current release pointer disagree")
                expected = value.get("candidate_config_sha256")
                if not isinstance(expected, dict) or self._live_hashes(target) != expected:
                    raise TargetRuntimeError("active release marker does not match running service configuration")
                expected_guards = value.get("guard_sha256")
                if not isinstance(expected_guards, dict) or self._guard_hashes(target) != expected_guards:
                    raise TargetRuntimeError("active release marker does not match target configuration or service units")
                self._check_service_status(target)
                return release_id
            except (OSError, json.JSONDecodeError):
                raise TargetRuntimeError("active target release marker is corrupt")
        return self._legacy_current(target)

    def _legacy_current(self, target: dict[str, Any]) -> str | None:
        link = Path(target["legacy_link"])
        if not link.is_symlink():
            return None
        destination = os.readlink(link)
        match = re.fullmatch(r"(?:versions/)?([0-9a-f]{64})", destination)
        release_id = match.group(1) if match else None
        if release_id is None and destination.startswith("/"):
            root = link.parent.resolve(strict=False)
            resolved = Path(destination).resolve(strict=False)
            candidates = {root / resolved.name, root / "versions" / resolved.name}
            if resolved in candidates and re.fullmatch(r"[0-9a-f]{64}", resolved.name):
                release_id = resolved.name
        if release_id is None:
            raise TargetRuntimeError("legacy schema-v3 current link is not a recognized version pointer")
        return release_id

    @staticmethod
    def _legacy_version_root(legacy_link: Path) -> Path:
        if not legacy_link.is_symlink():
            raise TargetRuntimeError("legacy schema-v3 provider snapshot is unavailable")
        destination = os.readlink(legacy_link)
        match = re.fullmatch(r"versions/([0-9a-f]{64})", destination)
        if match:
            return legacy_link.parent / "versions" / match.group(1)
        if destination.startswith("/"):
            allowed_root = legacy_link.parent.resolve(strict=False)
            resolved = Path(destination).resolve(strict=False)
            try:
                relative = resolved.relative_to(allowed_root)
            except ValueError as exc:
                raise TargetRuntimeError("legacy schema-v3 pointer escapes its rule root") from exc
            if len(relative.parts) == 1:
                release_id = relative.name
            elif len(relative.parts) == 2 and relative.parts[0] == "versions":
                release_id = relative.parts[1]
            else:
                raise TargetRuntimeError("legacy schema-v3 pointer is not an allowed version path")
            if re.fullmatch(r"[0-9a-f]{64}", release_id):
                return resolved if len(relative.parts) == 1 else allowed_root / "versions" / release_id
        raise TargetRuntimeError("legacy schema-v3 current link is not a recognized version pointer")

    def _managed_smartdns_path(self, target: dict[str, Any], release_id: str) -> str:
        root = target["integration"]["smartdns_rules_root"].rstrip("/")
        return f"{root}/releases/{release_id}/files/smartdns/proxy-domains.txt"

    def _smartdns_config(self, target: dict[str, Any], release_id: str, base: bytes) -> bytes:
        try:
            text = base.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TargetRuntimeError("SmartDNS base config is not UTF-8") from exc
        lines = text.splitlines()
        matches = [i for i, line in enumerate(lines) if line.startswith("domain-set -name proxy-domains ")]
        if len(matches) != 1:
            raise TargetRuntimeError("SmartDNS base must contain exactly one proxy-domains rule-set directive")
        index = matches[0]
        old = lines[index]
        if "-type list" not in old or "-file " not in old:
            raise TargetRuntimeError("SmartDNS proxy-domains directive has an unexpected shape")
        lines[index] = f"domain-set -name proxy-domains -type list -file {self._managed_smartdns_path(target, release_id)}"
        integration = target["integration"]
        nameserver_line = integration["smartdns_nameserver_line"]
        if lines.count(nameserver_line) != 1:
            raise TargetRuntimeError("SmartDNS base nameserver binding differs from approved contract")
        exceptions = []
        for row in integration["direct_exceptions"]:
            pattern = f"-.{row['domain']}" if row["match_type"] == "EXACT" else row["domain"]
            exceptions.append(f"nameserver /{pattern}/-")
        address_rows = []
        for row in integration["direct_exceptions"]:
            pattern = f"-.{row['domain']}" if row["match_type"] == "EXACT" else row["domain"]
            address_rows.append(f"address /{pattern}/-")
        if integration.get("smartdns_address_line"):
            address_binding = integration["smartdns_address_line"]
            if lines.count(address_binding) != 1:
                raise TargetRuntimeError("SmartDNS address-set binding differs from approved contract")
            address_index = lines.index(address_binding)
        else:
            address_index = next(i for i, line in enumerate(lines) if line.startswith("domain-set -name proxy-domains "))
        lines[address_index:address_index] = address_rows
        # Exceptions precede the proxy set declaration and retain normal default upstream behavior.
        index = next(i for i, line in enumerate(lines) if line.startswith("domain-set -name proxy-domains "))
        lines[index:index] = exceptions
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _mihomo_config(self, target: dict[str, Any], release_id: str, base: bytes, release_dir: Path, envelope: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
        try:
            config = yaml.safe_load(base.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise TargetRuntimeError("Mihomo base config is invalid YAML") from exc
        if not isinstance(config, dict) or not isinstance(config.get("rules"), list) or not isinstance(config.get("proxy-groups"), list):
            raise TargetRuntimeError("Mihomo base config lacks rules or proxy-groups")
        integration = target["integration"]
        groups = config["proxy-groups"]
        by_name = {item.get("name"): item for item in groups if isinstance(item, dict) and isinstance(item.get("name"), str)}
        if len(by_name) != len(groups):
            raise TargetRuntimeError("Mihomo base has invalid or duplicate policy groups")
        if "ProxySelect" not in by_name or not all(name in by_name for name in REGIONAL_GROUPS):
            raise TargetRuntimeError("Mihomo base is missing the existing ProxySelect or regional selectors")
        region_hash_before = _json_hash([by_name[name] for name in REGIONAL_GROUPS])
        provider_map = envelope.get("provider_map")
        order = envelope.get("mihomo_rule_order")
        if (not isinstance(provider_map, list) or not isinstance(order, list) or not all(isinstance(name, str) for name in order)
                or len(order) != len(set(order))
                or [row.get("name") for row in provider_map if isinstance(row, dict)] != order):
            raise TargetRuntimeError("Mihomo provider map/order does not match staged bundle")
        providers = config.setdefault("rule-providers", {})
        if not isinstance(providers, dict):
            raise TargetRuntimeError("Mihomo rule-providers is not a mapping")
        old_rules = config["rules"]
        if not all(isinstance(rule, str) for rule in old_rules):
            raise TargetRuntimeError("Mihomo rules contain non-string entries")
        preserved_direct_rules = [rule for rule in old_rules if rule.startswith("RULE-SET,") and len(rule.split(",")) >= 3 and rule.split(",", 2)[1] in DIRECT_PROVIDER_NAMES and rule.split(",", 2)[2] == "DIRECT"]
        preserved_static_rules = [rule for rule in old_rules if not rule.startswith("RULE-SET,") and not rule.startswith("MATCH,")]
        direct_rules = [f"DOMAIN,{row['domain']},DIRECT" if row["match_type"] == "EXACT" else f"DOMAIN-SUFFIX,{row['domain']},DIRECT" for row in envelope["direct_exceptions"]]
        base_root = integration["mihomo_rules_root"].rstrip("/")
        file_hashes: dict[str, str] = {}
        selected_choices = self._query_mihomo_selections(target, set(envelope.get("provider_policy_map", {}).values()), by_name)
        copied_provider_names = []
        for name in sorted(DIRECT_PROVIDER_NAMES):
            if name not in providers:
                raise TargetRuntimeError(f"protected direct provider {name} is missing from the base config")
            entry = providers[name]
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].startswith("/"):
                raise TargetRuntimeError(f"existing protected provider {name} has invalid path")
            source = Path(entry["path"])
            # The legacy `current` link is read only and is pinned to its resolved versions/<id> target.
            legacy_link = Path(target["legacy_link"])
            if not legacy_link.is_symlink():
                raise TargetRuntimeError("legacy schema-v3 provider snapshot is unavailable")
            legacy_root = self._legacy_version_root(legacy_link)
            try:
                relative = source.relative_to(legacy_link.parent / "current")
            except ValueError:
                try:
                    relative = source.relative_to(legacy_root)
                except ValueError as exc:
                    raise TargetRuntimeError(f"protected provider {name} is outside the legacy rule root") from exc
            source = legacy_root / relative
            if source.is_symlink() or not source.is_file():
                raise TargetRuntimeError(f"protected provider {name} source is missing or unsafe")
            destination_rel = f"mihomo/providers/{name}.yaml"
            destination = release_dir / "files" / destination_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
            digest = _sha(destination.read_bytes())
            file_hashes[destination_rel] = digest
            providers[name] = {**entry, "path": f"{base_root}/releases/{release_id}/files/{destination_rel}"}
            copied_provider_names.append(name)
        for name, entry in zip(order, provider_map, strict=True):
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name)
                    or name in DIRECT_PROVIDER_NAMES or not isinstance(entry, dict) or entry.get("name") != name):
                raise TargetRuntimeError("Mihomo provider identity is invalid")
            source_rel = f"mihomo/providers/{name}.yaml"
            source = release_dir / "files" / source_rel
            if source.is_symlink() or not source.is_file():
                raise TargetRuntimeError(f"dynamic provider {name} is missing")
            file_hashes[source_rel] = _sha(source.read_bytes())
            providers[name] = {"type": "file", "behavior": "classical", "format": "yaml", "path": f"{base_root}/releases/{release_id}/files/{source_rel}"}
            policy = entry.get("policy_group")
            if not isinstance(policy, str) or policy in {"DIRECT", "REJECT"}:
                raise TargetRuntimeError(f"provider {name} has unsupported policy group")
            if policy in DIRECT_PROVIDER_NAMES:
                raise TargetRuntimeError(f"dynamic policy {policy} collides with a protected provider")
            if policy not in by_name:
                group = {"name": policy}
                groups.append(group)
                by_name[policy] = group
            group = by_name[policy]
            if policy in REGIONAL_GROUPS or policy == "ProxySelect" or group.get("type") not in {"select", None}:
                raise TargetRuntimeError(f"dynamic policy group {policy} collides with a non-selector")
            selection = selected_choices.get(policy)
            members = ["ProxySelect", *REGIONAL_GROUPS, "REJECT"]
            if selection in members:
                members.remove(selection)
                members.insert(0, selection)
            group["type"] = "select"
            # Replace direct-node, include-all-provider, filter, and use based membership.
            for unsupported_field in ("use", "filter", "include-all", "include-all-providers", "exclude-filter"):
                group.pop(unsupported_field, None)
            group["proxies"] = list(dict.fromkeys(members))
        # Remove stale non-protection providers only when no preserved/static rules refer to them.
        keep_provider_names = DIRECT_PROVIDER_NAMES | set(order)
        referenced_by_proxy = set()
        for key in ("proxies", "proxy-groups"):
            value = config.get(key)
            if isinstance(value, list):
                blob = json.dumps(value, ensure_ascii=False)
                referenced_by_proxy.update(name for name in providers if name in blob)
        config["rule-providers"] = {name: value for name, value in providers.items() if name in keep_provider_names or name in referenced_by_proxy}
        config["rules"] = [*direct_rules, *preserved_direct_rules, *preserved_static_rules,
                            *(f"RULE-SET,{name},{entry['policy_group']}" for name, entry in zip(order, provider_map, strict=True)),
                            next((rule for rule in old_rules if rule.startswith("MATCH,")), "MATCH,DIRECT")]
        region_hash_after = _json_hash([by_name[name] for name in REGIONAL_GROUPS])
        if region_hash_after != region_hash_before:
            raise TargetRuntimeError("regional selector definitions changed during candidate rendering")
        candidate = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
        return candidate, {"regional_groups_sha256": region_hash_before, "provider_payload_sha256": dict(sorted(file_hashes.items())), "selected_policy_groups": selected_choices}

    def _query_mihomo_selections(self, target: dict[str, Any], policy_groups: set[str], by_name: dict[str, dict[str, Any]]) -> dict[str, str]:
        url = target.get("mihomo_controller_url")
        if not isinstance(url, str):
            if policy_groups:
                raise TargetRuntimeError("Mihomo selector preservation requires its configured loopback controller")
            return {}
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.port is None:
            raise TargetRuntimeError("Mihomo controller endpoint must be a loopback HTTP origin")
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/proxies", timeout=2) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise TargetRuntimeError("Mihomo selector response exceeds size limit")
                document = json.loads(body)
        except Exception as exc:
            raise TargetRuntimeError("cannot read current Mihomo selector state") from exc
        if not isinstance(document, dict) or not isinstance(document.get("proxies"), dict):
            raise TargetRuntimeError("Mihomo returned invalid selector state")
        proxy_data = document["proxies"]
        region_active: dict[str, set[str]] = {}
        for region in REGIONAL_GROUPS:
            row = proxy_data.get(region)
            if isinstance(row, dict):
                values = {value for value in [row.get("now"), *(row.get("all", []) if isinstance(row.get("all"), list) else [])] if isinstance(value, str)}
                region_active[region] = values
        result = {}
        for name in sorted(policy_groups):
            if name not in by_name:
                continue
            row = proxy_data.get(name)
            current = row.get("now") if isinstance(row, dict) else None
            if not isinstance(current, str):
                continue
            if current in REGIONAL_GROUPS:
                result[name] = current
                continue
            matching = [region for region, values in region_active.items() if current in values]
            if len(matching) == 1:
                result[name] = matching[0]
        return result

    def _prepare_service_configs(self, target: dict[str, Any], release_id: str, release_dir: Path, envelope: dict[str, Any]) -> dict[str, Any]:
        integration = target["integration"]
        configs = target.get("configs")
        if not isinstance(configs, dict) or set(configs) != set(target["services"]):
            raise TargetRuntimeError("live service configuration allowlist does not match services")
        service_root = release_dir / "integration"
        rollback_root = release_dir / "rollback"
        service_root.mkdir(mode=0o750, exist_ok=True)
        rollback_root.mkdir(mode=0o750, exist_ok=True)
        if "mihomo" in target["services"]:
            (service_root / "validation-home").mkdir(mode=0o750, exist_ok=True)
        baselines: dict[str, str] = {}
        candidates: dict[str, str] = {}
        metadata: dict[str, dict[str, int]] = {}
        for service in target["services"]:
            live = _trusted_path(configs[service])
            if not live.is_file() or live.is_symlink():
                raise TargetRuntimeError(f"live {service} configuration is missing or unsafe")
            original = live.read_bytes()
            baselines[service] = _sha(original)
            stat = live.stat()
            metadata[service] = {"mode": stat.st_mode & 0o777, "uid": stat.st_uid, "gid": stat.st_gid}
            (rollback_root / f"{service}.conf").write_bytes(original)
            if service == "smartdns":
                candidate = self._smartdns_config(target, release_id, original)
            else:
                candidate, evidence = self._mihomo_config(target, release_id, original, release_dir, envelope)
                metadata["strategy"] = evidence
            (service_root / f"{service}.conf").write_bytes(candidate)
            candidates[service] = _sha(candidate)
        exception_rows = integration["direct_exceptions"]
        if envelope.get("direct_exceptions") != exception_rows or envelope.get("direct_exceptions_sha256") != _json_hash(exception_rows):
            raise TargetRuntimeError("controller direct-exception contract differs from target-local approved baseline")
        summary = {
            "schema_version": 1,
            "release_id": release_id,
            "target_id": target["_target_id"],
            "services": list(target["services"]),
            "baseline_config_sha256": baselines,
            "baseline_guard_sha256": self._guard_hashes(target),
            "candidate_config_sha256": candidates,
            "metadata": metadata,
            "direct_exceptions_sha256": _json_hash(exception_rows),
            "regional_groups": list(REGIONAL_GROUPS) if "mihomo" in target["services"] else [],
            "mihomo_rule_order": envelope.get("mihomo_rule_order", []),
            "provider_policy_map": envelope.get("provider_policy_map", {}),
            "previous_release_id": self._current(target),
        }
        (service_root / "integration.json").write_text(json.dumps(summary, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return summary

    def _guard_hashes(self, target: dict[str, Any]) -> dict[str, str]:
        values = {"target_agent_config": _sha(self.config_path.read_bytes())}
        guard_files = target.get("guard_files", {})
        if not isinstance(guard_files, dict) or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) for key in guard_files):
            raise TargetRuntimeError("target guard-file allowlist is invalid")
        for name, value in guard_files.items():
            if not isinstance(value, str):
                raise TargetRuntimeError("target guard-file path is invalid")
            path = _trusted_path(value)
            if not path.is_file() or path.is_symlink():
                raise TargetRuntimeError(f"target guard file is missing or unsafe: {name}")
            values[name] = _sha(path.read_bytes())
        units = target.get("systemd_units", [])
        if units:
            for unit in units:
                if not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
                    raise TargetRuntimeError("systemd unit guard is invalid")
                result = subprocess.run(["systemctl", "show", "--property=FragmentPath", "--value", unit],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, timeout=10, check=False)
                if result.returncode != 0:
                    raise TargetRuntimeError(f"cannot resolve systemd unit file: {unit}")
                raw = result.stdout.decode("utf-8", "strict").strip()
                path = Path(raw)
                if not path.is_absolute() or not path.is_file():
                    raise TargetRuntimeError(f"systemd unit file is missing: {unit}")
                values[f"unit:{unit}"] = _sha(path.read_bytes())
        return dict(sorted(values.items()))

    def _check_service_readability(self, target: dict[str, Any], release_dir: Path) -> None:
        commands = target.get("service_readability_commands", {})
        if not isinstance(commands, dict):
            raise TargetRuntimeError("service readability checks are invalid")
        for service, argv in commands.items():
            if service not in target["services"] or not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                raise TargetRuntimeError("service readability command is invalid")
            expanded = [arg.replace("{release_dir}", str(release_dir)).replace("{managed_root}", str(target["_root"])) for arg in argv]
            result = subprocess.run(expanded, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, timeout=15, check=False)
            if result.returncode != 0:
                raise TargetRuntimeError(f"{service} cannot read staged rule files as its configured service identity")

    @staticmethod
    def _apply_release_permissions(target: dict[str, Any], release_dir: Path) -> None:
        group_name = target.get("managed_group", "root")
        owner_id = target.get("managed_uid", 0)
        if type(owner_id) is not int or owner_id < 0:
            raise TargetRuntimeError("configured managed UID is invalid")
        try:
            group_id = grp.getgrnam(group_name).gr_gid
        except KeyError as exc:
            raise TargetRuntimeError("configured managed service group does not exist") from exc
        os.chown(target["_root"], owner_id, group_id)
        os.chmod(target["_root"], 0o750)
        for path in sorted(release_dir.rglob("*"), key=lambda item: len(item.parts)):
            if path.is_symlink():
                raise TargetRuntimeError("staged release unexpectedly contains symlinks")
            os.chown(path, owner_id, group_id)
            os.chmod(path, 0o750 if path.is_dir() else 0o640)
        os.chown(release_dir, owner_id, group_id)
        os.chmod(release_dir, 0o750)

    def _save_legacy_snapshot(self, target: dict[str, Any], release_id: str, summary: dict[str, Any], release_dir: Path) -> None:
        legacy_id = summary.get("previous_release_id")
        if not isinstance(legacy_id, str) or not re.fullmatch(r"[0-9a-f]{64}", legacy_id):
            return
        # Preserve the actual old schema-v3 service files and pointer identity once. Never rewrite its link.
        legacy_root = target["_root"] / "legacy" / legacy_id
        if legacy_root.exists():
            return
        legacy_root.mkdir(mode=0o750, parents=True)
        for service in target["services"]:
            source = release_dir / "rollback" / f"{service}.conf"
            if source.is_file() and not source.is_symlink():
                shutil.copyfile(source, legacy_root / f"{service}.conf")
        snapshot = {
            "schema_version": 3,
            "release_id": legacy_id,
            "target_id": target["_target_id"],
            "current_link": os.readlink(target["legacy_link"]) if Path(target["legacy_link"]).is_symlink() else None,
            "config_sha256": summary["baseline_config_sha256"],
            "metadata": summary["metadata"],
        }
        (legacy_root / "snapshot.json").write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    def _integration(self, target: dict[str, Any], release_id: str) -> dict[str, Any]:
        root = self._release_dir(target, release_id) / "integration"
        if root.is_symlink() or not root.is_dir():
            raise TargetRuntimeError("staged service configuration is unavailable")
        try:
            summary = json.loads((root / "integration.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TargetRuntimeError("staged service configuration metadata is invalid") from exc
        if summary.get("schema_version") != 1 or summary.get("release_id") != release_id or summary.get("target_id") != target["_target_id"]:
            raise TargetRuntimeError("staged service configuration metadata identity mismatch")
        for service in target["services"]:
            path = root / f"{service}.conf"
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != summary.get("candidate_config_sha256", {}).get(service):
                raise TargetRuntimeError(f"staged {service} candidate configuration changed")
        strategy = summary.get("metadata", {}).get("strategy", {})
        for relative, digest in strategy.get("provider_payload_sha256", {}).items():
            path = self._release_dir(target, release_id) / "files" / relative
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != digest:
                raise TargetRuntimeError(f"managed provider snapshot changed: {relative}")
        return summary

    @staticmethod
    def _atomic_write(path: Path, data: bytes, metadata: dict[str, int]) -> None:
        if path.is_symlink():
            raise TargetRuntimeError("live configuration path may not be a symlink")
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise TargetRuntimeError("live configuration parent is missing or unsafe")
        temp = path.parent / f".{path.name}.dns-rule-builder-{uuid.uuid4().hex}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, metadata["mode"])
            os.chown(temp, metadata["uid"], metadata["gid"])
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _live_hashes(self, target: dict[str, Any]) -> dict[str, str]:
        return {name: _sha(_trusted_path(path).read_bytes()) for name, path in target["configs"].items()}

    def _check_service_status(self, target: dict[str, Any]) -> None:
        status_commands = target.get("status_commands", {})
        if not isinstance(status_commands, dict) or set(status_commands) != set(target["services"]):
            raise TargetRuntimeError("service readback allowlist is incomplete")
        timeout = int(target.get("validation_timeout_seconds", 60))
        for service, argv in status_commands.items():
            if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                raise TargetRuntimeError("service readback command is invalid")
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, timeout=timeout, check=False)
            if result.returncode != 0 or result.stdout.strip() not in {b"active", b"true", b"running"}:
                raise TargetRuntimeError(f"{service} service readback is not active")

    def _set_mihomo_selections(self, target: dict[str, Any], expected: dict[str, str]) -> None:
        if not expected:
            return
        url = target.get("mihomo_controller_url")
        if not isinstance(url, str):
            raise TargetRuntimeError("Mihomo selector restore requires its configured loopback controller")
        for group, choice in sorted(expected.items()):
            request = urllib.request.Request(url.rstrip("/") + "/proxies/" + urllib.parse.quote(group, safe=""),
                                             data=json.dumps({"name": choice}).encode(),
                                             headers={"Content-Type": "application/json"}, method="PUT")
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    if response.status not in {200, 204}:
                        raise TargetRuntimeError("Mihomo rejected selector restore")
            except Exception as exc:
                raise TargetRuntimeError(f"Mihomo selector restore failed for {group}") from exc
        actual = self._query_mihomo_selections(target, set(expected), {name: {} for name in expected})
        if actual != expected:
            raise TargetRuntimeError("Mihomo selector restore readback mismatch")

    def stage(self, target_id: str, request_id: str, release_id: str, header: dict[str, Any], archive_data: bytes) -> dict[str, Any]:
        target = self._target(target_id)
        envelope = header.get("envelope")
        if not isinstance(envelope, dict) or envelope.get("target_id") != target_id or envelope.get("release_id") != release_id:
            raise TargetRuntimeError("stage envelope does not match operation")
        expected_envelope_keys = {"schema_version", "target_id", "role", "release_id", "manifest_sha256", "services", "files", "mihomo_rule_order", "provider_map", "provider_policy_map", "regional_groups", "direct_exceptions", "direct_exceptions_sha256"}
        if (set(envelope) != expected_envelope_keys or envelope.get("schema_version") != 4 or envelope.get("role") != target.get("role")
                or envelope.get("services") != target.get("services") or not isinstance(envelope.get("files"), dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(envelope.get("manifest_sha256", "")))):
            raise TargetRuntimeError("invalid stage envelope")
        exceptions = _direct_exception_rows(envelope.get("direct_exceptions"))
        if exceptions != target["integration"]["direct_exceptions"] or envelope.get("direct_exceptions_sha256") != _json_hash(exceptions):
            raise TargetRuntimeError("stage direct-exception contract does not match target-local approval")
        if "mihomo" in target["services"]:
            if envelope.get("regional_groups") != list(REGIONAL_GROUPS) or not isinstance(envelope.get("provider_map"), list):
                raise TargetRuntimeError("stage Mihomo strategy contract is incomplete")
        elif envelope.get("regional_groups") != [] or envelope.get("provider_map") != [] or envelope.get("mihomo_rule_order") != []:
            raise TargetRuntimeError("SmartDNS-only target contains unexpected Mihomo data")
        if len(archive_data) > MAX_ARCHIVE:
            raise TargetRuntimeError("stage archive exceeds size limit")
        expected = envelope["files"]
        if len(expected) > MAX_FILES:
            raise TargetRuntimeError("too many staged files")
        allowed = target.get("allowed_files")
        prefixes = target.get("allowed_file_prefixes", [])
        if (not isinstance(allowed, list) or not isinstance(prefixes, list)
                or any(not isinstance(item, str) for item in [*allowed, *prefixes])
                or any(not _safe_relative(item) for item in allowed)
                or any(not item or item.startswith("/") or ".." in PurePosixPath(item).parts or "\\" in item for item in prefixes)
                or any(not any(path == item for item in allowed) and not any(path.startswith(prefix) for prefix in prefixes) for path in expected)):
            raise TargetRuntimeError("envelope includes files outside the local target allowlist")
        root = target["_root"]
        root.mkdir(mode=0o750, parents=True, exist_ok=True)
        releases_root = root / "releases"
        if releases_root.is_symlink():
            raise TargetRuntimeError("release directory may not be a symlink")
        releases_root.mkdir(mode=0o750, exist_ok=True)
        release_dir = self._release_dir(target, release_id)
        if release_dir.is_symlink():
            raise TargetRuntimeError("release directory may not be a symlink")
        temp = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
        try:
            with tarfile.open(fileobj=__import__("io").BytesIO(archive_data), mode="r:gz") as tar:
                members = tar.getmembers()
                if len(members) != len(expected) + 1 or len(members) > MAX_FILES + 1:
                    raise TargetRuntimeError("stage archive member count mismatch")
                seen: set[str] = set()
                total_size = 0
                for member in members:
                    if not member.isfile() or member.issym() or member.islnk() or member.size > MAX_FILE:
                        raise TargetRuntimeError("stage archive contains unsafe member")
                    total_size += member.size
                    if total_size > MAX_ARCHIVE:
                        raise TargetRuntimeError("expanded stage archive exceeds size limit")
                    if member.name == "envelope.json":
                        extracted = tar.extractfile(member)
                        if extracted is None or json.loads(extracted.read(MAX_HEADER + 1)) != envelope:
                            raise TargetRuntimeError("archive envelope mismatch")
                        continue
                    if not member.name.startswith("files/"):
                        raise TargetRuntimeError("unexpected stage archive member")
                    relative = member.name[6:]
                    if not _safe_relative(relative) or relative not in expected or relative in seen:
                        raise TargetRuntimeError("unsafe or unexpected staged path")
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        raise TargetRuntimeError("stage archive member is unreadable")
                    data = extracted.read(MAX_FILE + 1)
                    if len(data) != member.size or _sha(data) != expected[relative]:
                        raise TargetRuntimeError(f"staged file hash mismatch: {relative}")
                    destination = temp / "files" / relative
                    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    os.chmod(destination, 0o640)
                    seen.add(relative)
                if seen != set(expected):
                    raise TargetRuntimeError("stage archive is incomplete")
            if release_dir.exists():
                stored = json.loads((release_dir / "envelope.json").read_text(encoding="utf-8"))
                if stored != envelope:
                    raise TargetRuntimeError("release directory already contains another envelope")
                shutil.rmtree(temp)
                self._integration(target, release_id)
                self._check_service_readability(target, release_dir)
                return {"ok": True, "target_id": target_id, "release_id": release_id, "integration_sha256": _sha((release_dir / "integration/integration.json").read_bytes())}
            integration_summary = self._prepare_service_configs(target, release_id, temp, envelope)
            self._save_legacy_snapshot(target, release_id, integration_summary, temp)
            (temp / "envelope.json").write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            self._apply_release_permissions(target, temp)
            os.replace(temp, release_dir)
            self._check_service_readability(target, release_dir)
            return {"ok": True, "target_id": target_id, "release_id": release_id, "integration_sha256": _sha((release_dir / "integration/integration.json").read_bytes())}
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)

    def _validate(self, target_id: str, release_id: str) -> dict[str, Any]:
        target = self._target(target_id)
        release_dir = self._release_dir(target, release_id)
        envelope = json.loads((release_dir / "envelope.json").read_text(encoding="utf-8"))
        for relative, expected in envelope["files"].items():
            path = release_dir / "files" / relative
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != expected:
                raise TargetRuntimeError(f"staged file changed: {relative}")
        integration = self._integration(target, release_id)
        current_guards = self._guard_hashes(target)
        if current_guards != integration.get("baseline_guard_sha256"):
            raise TargetRuntimeError("target service configuration or local target contract changed after stage")
        self._check_service_readability(target, release_dir)
        live_hashes = self._live_hashes(target)
        expected_live = integration["candidate_config_sha256"] if self._current(target) == release_id else integration["baseline_config_sha256"]
        if live_hashes != expected_live:
            raise TargetRuntimeError("live configuration changed after stage or no longer matches candidate")
        binary_paths = target.get("binaries")
        config_paths = target.get("configs")
        commands = target.get("validate_commands")
        if not isinstance(binary_paths, dict) or not isinstance(config_paths, dict) or not isinstance(commands, dict):
            raise TargetRuntimeError("target validation allowlist is incomplete")
        binary_hashes = {name: _sha(_trusted_path(path).read_bytes()) for name, path in binary_paths.items()}
        probe_commands = target.get("binary_probe_commands", {})
        if not isinstance(probe_commands, dict):
            raise TargetRuntimeError("binary probe command allowlist is invalid")
        for name, argv in probe_commands.items():
            if name not in target.get("services", []) or not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                raise TargetRuntimeError("binary probe command is invalid")
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, timeout=30, check=False)
            match = re.match(rb"^([0-9a-f]{64})\b", result.stdout.strip())
            if result.returncode != 0 or not match:
                raise TargetRuntimeError(f"{name} binary probe failed")
            binary_hashes[name] = match.group(1).decode("ascii")
        config_hashes = {**integration["baseline_config_sha256"], **current_guards}
        for service, argv in commands.items():
            if service not in target.get("services", []) or not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                raise TargetRuntimeError("invalid validation command allowlist")
            release_dir = self._release_dir(target, release_id)
            expanded_argv = [arg.replace("{release_dir}", str(release_dir)).replace("{target_id}", target_id)
                             .replace("{smartdns_config}", str(release_dir / "integration/smartdns.conf"))
                             .replace("{mihomo_config}", str(release_dir / "integration/mihomo.conf"))
                             .replace("{validation_home}", str(release_dir / "integration/validation-home")) for arg in argv]
            env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
            configured_env = target.get("validation_environment", {}).get(service, {})
            if not isinstance(configured_env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) or not isinstance(v, str) or "\x00" in v for k, v in configured_env.items()):
                raise TargetRuntimeError("invalid validation environment")
            env.update(configured_env)
            timeout = int(target.get("validation_timeout_seconds", 60))
            timed_out_as_expected = False
            try:
                result = subprocess.run(expanded_argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                        timeout=timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                config_error = service == "smartdns" and _smartdns_reports_config_error(exc.stdout, exc.stderr)
                timed_out_as_expected = (service == "smartdns"
                                         and bool(target.get("smartdns_validator_stays_running", False))
                                         and not config_error)
                result = None
            accepted_exit_codes = target.get("validation_exit_codes", {}).get(service, [0])
            if not isinstance(accepted_exit_codes, list) or not all(type(code) is int for code in accepted_exit_codes):
                raise TargetRuntimeError("validation exit-code allowlist is invalid")
            config_error = (service == "smartdns" and result is not None
                            and result.returncode in accepted_exit_codes
                            and _smartdns_reports_config_error(result.stdout, result.stderr))
            if config_error:
                raise TargetRuntimeError("smartdns configuration validation reported a configuration error")
            if result is not None and result.returncode not in accepted_exit_codes or result is None and not timed_out_as_expected:
                status = "timeout" if result is None else str(result.returncode)
                raise TargetRuntimeError(f"{service} configuration validation failed (exit {status})")
        strategy = _json_hash({"envelope": envelope, "baseline_config": config_hashes,
                               "candidate_config": integration["candidate_config_sha256"],
                               "direct_exceptions": integration["direct_exceptions_sha256"],
                               "regional_groups": integration.get("metadata", {}).get("strategy", {}).get("regional_groups_sha256"),
                               "provider_payload_sha256": integration.get("metadata", {}).get("strategy", {}).get("provider_payload_sha256", {})})
        sha256_json = _json_hash
        return {"result": "ok", "target_id": target_id, "release_id": release_id,
                "target_manifest_sha256": sha256_json(envelope), "binary_sha256": binary_hashes,
                "config_sha256": config_hashes, "candidate_config_sha256": integration["candidate_config_sha256"],
                "running_config_sha256": live_hashes,
                "direct_exceptions_sha256": integration["direct_exceptions_sha256"],
                "regional_groups_sha256": integration.get("metadata", {}).get("strategy", {}).get("regional_groups_sha256"),
                "strategy_validation_sha256": strategy}

    def validate(self, target_id: str, release_id: str) -> dict[str, Any]:
        return {"ok": True, "target_id": target_id, "release_id": release_id, "evidence": self._validate(target_id, release_id)}

    def activate(self, target_id: str, release_id: str) -> dict[str, Any]:
        target = self._target(target_id)
        evidence = self._validate(target_id, release_id)
        integration = self._integration(target, release_id)
        # Recheck immediately before writing; preflight evidence alone cannot authorize stale bases.
        if self._live_hashes(target) != integration["baseline_config_sha256"] or self._guard_hashes(target) != integration.get("baseline_guard_sha256"):
            raise TargetRuntimeError("live base configuration drifted before activation")
        intent_path = target["_root"] / "activation-intent.json"
        if intent_path.exists():
            raise TargetRuntimeError("another interrupted activation requires recovery")
        intent_tmp = target["_root"] / f".activation-intent-{uuid.uuid4().hex}"
        intent_tmp.write_text(json.dumps({"release_id": release_id, "previous_release_id": integration.get("previous_release_id")}, sort_keys=True), encoding="utf-8")
        os.replace(intent_tmp, intent_path)
        for service, live_path in target["configs"].items():
            source = self._release_dir(target, release_id) / "integration" / f"{service}.conf"
            metadata = integration["metadata"][service]
            self._atomic_write(_trusted_path(live_path), source.read_bytes(), metadata)
        root = target["_root"]
        self._restart(target)
        if "mihomo" in target["services"]:
            selected = integration.get("metadata", {}).get("strategy", {}).get("selected_policy_groups", {})
            self._set_mihomo_selections(target, selected)
        if self._live_hashes(target) != integration["candidate_config_sha256"]:
            raise TargetRuntimeError("service configuration readback mismatch after activation")
        self._check_service_status(target)
        pointer = root / "current"
        temporary = root / f".current-{uuid.uuid4().hex}"
        os.symlink(f"releases/{release_id}", temporary)
        os.replace(temporary, pointer)
        marker = root / "active-release.json"
        marker_temp = root / f".active-{uuid.uuid4().hex}"
        marker_temp.write_text(json.dumps({"release_id": release_id, "candidate_config_sha256": evidence["candidate_config_sha256"], "guard_sha256": self._guard_hashes(target)}, sort_keys=True), encoding="utf-8")
        os.replace(marker_temp, marker)
        if os.readlink(pointer) != f"releases/{release_id}" or self._live_hashes(target) != integration["candidate_config_sha256"]:
            raise TargetRuntimeError("activation version/config readback mismatch")
        intent_path.unlink(missing_ok=True)
        if self._current(target) != release_id:
            raise TargetRuntimeError("activation release marker readback mismatch")
        return {"ok": True, "target_id": target_id, "release_id": release_id, "candidate_config_sha256": evidence["candidate_config_sha256"]}

    def rollback(self, target_id: str, previous_release_id: str | None) -> dict[str, Any]:
        target = self._target(target_id)
        root = target["_root"]
        intent_path = root / "activation-intent.json"
        intent = None
        if intent_path.is_file() and not intent_path.is_symlink():
            try:
                intent = json.loads(intent_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TargetRuntimeError("interrupted activation intent is corrupt") from exc
        active_id = intent.get("release_id") if isinstance(intent, dict) else self._current(target)
        if previous_release_id is not None and (root / "releases" / previous_release_id / "integration/integration.json").is_file():
            previous = self._release_dir(target, previous_release_id)
            previous_summary = self._integration(target, previous_release_id)
            restore_root = previous / "integration"
            restore_metadata = previous_summary["metadata"]
        elif previous_release_id is not None:
            legacy = root / "legacy" / previous_release_id
            try:
                snapshot = json.loads((legacy / "snapshot.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TargetRuntimeError("legacy schema-v3 rollback snapshot is unavailable") from exc
            if snapshot.get("release_id") != previous_release_id or snapshot.get("schema_version") != 3:
                raise TargetRuntimeError("legacy rollback snapshot identity mismatch")
            restore_root, restore_metadata = legacy, snapshot["metadata"]
        elif active_id is not None:
            active = self._release_dir(target, active_id)
            active_summary = self._integration(target, active_id)
            restore_root, restore_metadata = active / "rollback", active_summary["metadata"]
        else:
            raise TargetRuntimeError("no known prior service configuration is available")
        for service, live_path in target["configs"].items():
            source = restore_root / f"{service}.conf"
            if source.is_symlink() or not source.is_file():
                raise TargetRuntimeError(f"rollback {service} configuration snapshot is missing")
            self._atomic_write(_trusted_path(live_path), source.read_bytes(), restore_metadata[service])
        self._restart(target)
        expected_hashes = {service: _sha((restore_root / f"{service}.conf").read_bytes()) for service in target["services"]}
        if self._live_hashes(target) != expected_hashes:
            raise TargetRuntimeError("rollback configuration readback mismatch")
        self._check_service_status(target)
        pointer = root / "current"
        if previous_release_id is not None and (root / "releases" / previous_release_id).is_dir():
            temp = root / f".current-{uuid.uuid4().hex}"
            os.symlink(f"releases/{previous_release_id}", temp)
            os.replace(temp, pointer)
            marker_value = {"release_id": previous_release_id, "candidate_config_sha256": expected_hashes, "guard_sha256": previous_summary.get("baseline_guard_sha256", {})}
            (root / "active-release.json").write_text(json.dumps(marker_value, sort_keys=True), encoding="utf-8")
        else:
            if pointer.is_symlink():
                pointer.unlink()
            marker = root / "active-release.json"
            if marker.exists():
                marker.unlink()
        intent_path.unlink(missing_ok=True)
        if self._current(target) != previous_release_id:
            raise TargetRuntimeError("rollback version readback mismatch")
        return {"ok": True, "target_id": target_id, "current_release_id": previous_release_id}

    def _restart(self, target: dict[str, Any]) -> None:
        restart = target.get("restart", {"mode": "systemd"})
        if not isinstance(restart, dict) or restart.get("mode") not in {"systemd", "docker-compose"}:
            raise TargetRuntimeError("restart mode is not allowlisted")
        if restart["mode"] == "docker-compose":
            argv = restart.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and "\x00" not in item for item in argv):
                raise TargetRuntimeError("fixed Docker Compose restart argv is missing")
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, timeout=180, check=False)
            if result.returncode != 0:
                raise TargetRuntimeError("allowlisted Docker Compose activation failed")
            return
        units = target.get("systemd_units", [])
        if not isinstance(units, list) or not all(isinstance(unit, str) and re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit) for unit in units):
            raise TargetRuntimeError("invalid systemd unit allowlist")
        for unit in units:
            result = subprocess.run(["systemctl", "restart", unit], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False)
            if result.returncode != 0:
                raise TargetRuntimeError(f"allowlisted service restart failed ({unit})")

    def dispatch(self, operation: str, request_id: str, release_id: str, header: dict[str, Any], archive_data: bytes = b"") -> dict[str, Any]:
        try:
            uuid.UUID(request_id)
        except ValueError as exc:
            raise TargetRuntimeError("invalid request UUID") from exc
        target_id = header.get("target_id")
        if not isinstance(target_id, str):
            # target ID is deployment-local and supplied by the signed-in SSH account config.
            target_id = self.config.get("default_target_id")
        if not isinstance(target_id, str):
            raise TargetRuntimeError("target_id is missing")
        envelope = header.get("envelope")
        if operation in {"validate", "activate"}:
            if not isinstance(envelope, dict) or envelope.get("target_id") != target_id or envelope.get("release_id") != release_id:
                raise TargetRuntimeError("operation envelope does not match target or release")
            target = self._target(target_id)
            staged_envelope = json.loads((self._release_dir(target, release_id) / "envelope.json").read_text(encoding="utf-8"))
            if envelope != staged_envelope:
                raise TargetRuntimeError("operation envelope differs from staged release")
        if operation == "stage":
            return self.stage(target_id, request_id, release_id, header, archive_data)
        if operation == "validate":
            return self.validate(target_id, release_id)
        if operation == "activate":
            return self.activate(target_id, release_id)
        if operation == "rollback":
            return self.rollback(target_id, header.get("previous_release_id"))
        if operation == "status":
            target = self._target(target_id)
            current = self._current(target)
            if current and (target["_root"] / "releases" / current / "integration/integration.json").is_file():
                summary = self._integration(target, current)
                if self._live_hashes(target) != summary["candidate_config_sha256"]:
                    raise TargetRuntimeError("active release does not match live service configuration")
                self._check_service_status(target)
            return {"ok": True, "target_id": target_id, "current_release_id": current}
        raise TargetRuntimeError("unsupported operation")


def run_agent(config_path: Path, operation: str, request_id: str, release_id: str, stdin, stdout) -> int:
    try:
        raw_header = stdin.readline(MAX_HEADER + 1)
        if len(raw_header) > MAX_HEADER or not raw_header.endswith(b"\n"):
            raise TargetRuntimeError("request header exceeds limit")
        header = json.loads(raw_header)
        if not isinstance(header, dict):
            raise TargetRuntimeError("request header must be a JSON object")
        archive_data = stdin.read(MAX_ARCHIVE + 1) if operation == "stage" else stdin.read(1)
        if len(archive_data) > MAX_ARCHIVE or (operation != "stage" and archive_data):
            raise TargetRuntimeError("unexpected or oversized request body")
        response = TargetRuntime(config_path).dispatch(operation, request_id, release_id, header, archive_data)
        stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n")
        return 0
    except Exception as exc:
        # Never echo command output, paths, or request contents to the caller.
        stdout.write(json.dumps({"ok": False, "error": type(exc).__name__}, separators=(",", ":")).encode() + b"\n")
        return 1
