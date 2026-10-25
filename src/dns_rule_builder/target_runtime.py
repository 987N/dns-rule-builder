"""Restricted runtime for the separately installed schema-v4 target agent."""

from __future__ import annotations

import hashlib
import base64
import fcntl
import grp
import ipaddress
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
import urllib.error
import time
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


def _mihomo_controller_origin(value: Any) -> tuple[str, int]:
    """Validate a non-proxyable local Mihomo HTTP origin."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise TargetRuntimeError("Mihomo controller endpoint is invalid") from exc
    if (parsed.scheme != "http" or not host or port is None or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise TargetRuntimeError("Mihomo controller endpoint must be a local HTTP origin")
    normalized = host.lower()
    if normalized != "localhost":
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError as exc:
            raise TargetRuntimeError("Mihomo controller host must be a literal local IP") from exc
        if address.is_loopback:
            normalized = address.compressed
        elif (address.version != 4 or not address.is_private or address.is_link_local
              or address.is_multicast or address.is_unspecified or address.is_reserved):
            raise TargetRuntimeError("Mihomo controller host must be loopback or a private IPv4 address")
        else:
            normalized = address.compressed
    return normalized, port


def _config_controller_origin(value: Any) -> tuple[str, int]:
    if not isinstance(value, str) or not value or value != value.strip() or "/" in value or "@" in value or "?" in value or "#" in value:
        raise TargetRuntimeError("live Mihomo external-controller is invalid")
    try:
        parsed = urllib.parse.urlsplit("//" + value)
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.hostname is None or parsed.port is None:
            raise ValueError("invalid authority")
        return _mihomo_controller_origin(f"http://{parsed.netloc}")
    except (TypeError, ValueError, TargetRuntimeError) as exc:
        raise TargetRuntimeError("live Mihomo external-controller is invalid") from exc


def _is_current_local_ipv4(host: str) -> bool:
    """Use only the fixed iproute2 command; never resolve or probe a remote host."""
    try:
        result = subprocess.run(
            ["/usr/sbin/ip", "-j", "address", "show"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3, check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if result.returncode != 0:
            return False
        interfaces = json.loads(result.stdout)
        return isinstance(interfaces, list) and any(
            isinstance(interface, dict) and isinstance(interface.get("addr_info"), list)
            and any(isinstance(address, dict) and address.get("family") == "inet" and address.get("local") == host
                    for address in interface["addr_info"])
            for interface in interfaces
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


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


def _managed_config_hash(service: str, data: bytes) -> str:
    """Hash managed configuration semantics, excluding Mihomo subscription inputs."""
    if service != "mihomo":
        return _sha(data)
    try:
        config = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TargetRuntimeError("Mihomo config cannot be hashed safely") from exc
    if not isinstance(config, dict):
        raise TargetRuntimeError("Mihomo config cannot be hashed safely")
    config = dict(config)
    config.pop("proxy-providers", None)
    return _json_hash(config)


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
        self._monotonic = time.monotonic
        self._sleep = time.sleep

    _MIHOMO_READY_TIMEOUT = 5.0
    _MIHOMO_RETRY_INTERVAL = 0.2

    @staticmethod
    def _transient_http_error(exc: BaseException) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code not in {400, 401, 403, 405, 422} and (exc.code in {404, 408, 409, 425, 429} or exc.code >= 500)
        return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))

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
            _mihomo_controller_origin(controller_url)
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

    @staticmethod
    def _strategy_preferences_path(target: dict[str, Any]) -> Path:
        return target["_root"] / "strategy-preferences.json"

    @staticmethod
    def _runtime_gid(target: dict[str, Any]) -> int:
        return 0 if target.get("managed_uid", 0) == 0 else os.getegid()

    @staticmethod
    def _managed_policy_groups(config: dict[str, Any], managed_names: set[str]) -> set[str]:
        protected = set(REGIONAL_GROUPS) | {"ProxySelect"} | DIRECT_PROVIDER_NAMES
        groups = {item.get("name"): item for item in config.get("proxy-groups", []) if isinstance(item, dict)}
        return {name for name in managed_names if name not in protected and isinstance(groups.get(name), dict)
                and groups[name].get("type") == "select"}

    def _managed_policy_names(self, target: dict[str, Any], release_id: str | None) -> set[str]:
        if not isinstance(release_id, str):
            return set()
        path = self._release_dir(target, release_id) / "envelope.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        provider_policy_map = envelope.get("provider_policy_map") if isinstance(envelope, dict) else None
        if not isinstance(provider_policy_map, dict):
            return set()
        return {value for value in provider_policy_map.values() if isinstance(value, str)}

    @staticmethod
    def _load_strategy_preferences(target: dict[str, Any]) -> dict[str, Any]:
        path = TargetRuntime._strategy_preferences_path(target)
        if not path.exists():
            return {"schema_version": 1, "revision": 0, "groups": {}}
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise TargetRuntimeError("strategy preference file is unsafe")
        stat = path.stat()
        if stat.st_uid != target.get("managed_uid", 0) or stat.st_size > 2 * 1024 * 1024:
            raise TargetRuntimeError("strategy preference file owner or size is invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TargetRuntimeError("strategy preference file is invalid") from exc
        if (not isinstance(value, dict) or set(value) != {"schema_version", "revision", "groups"}
                or value.get("schema_version") != 1 or type(value.get("revision")) is not int
                or value["revision"] < 0 or not isinstance(value.get("groups"), dict)):
            raise TargetRuntimeError("strategy preference schema is invalid")
        return value

    @staticmethod
    def _write_strategy_preferences(target: dict[str, Any], preferences: dict[str, Any]) -> None:
        path = TargetRuntime._strategy_preferences_path(target)
        root = target["_root"]
        root.mkdir(mode=0o750, parents=True, exist_ok=True)
        if root.is_symlink() or path.is_symlink():
            raise TargetRuntimeError("strategy preference path is unsafe")
        data = json.dumps(preferences, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        temp = root / f".strategy-preferences-{uuid.uuid4().hex}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temp, target.get("managed_uid", 0), TargetRuntime._runtime_gid(target))
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _reference_name(reference: Any) -> str:
        if not isinstance(reference, dict) or set(reference) - {"kind", "name", "provider"}:
            raise TargetRuntimeError("strategy member reference is invalid")
        kind, name = reference.get("kind"), reference.get("name")
        if kind not in {"group", "proxy", "provider", "special"} or not isinstance(name, str) or not name or len(name) > 128:
            raise TargetRuntimeError("strategy member reference is invalid")
        if kind == "provider":
            provider = reference.get("provider")
            if not isinstance(provider, str) or not provider or len(provider) > 128:
                raise TargetRuntimeError("provider member reference is invalid")
            return name
        if "provider" in reference:
            raise TargetRuntimeError("unexpected provider identity")
        return name

    @staticmethod
    def _prepare_releases_root(target: dict[str, Any]) -> Path:
        """Create the managed release parent with only the configured service-group access."""
        root = target["_root"]
        releases_root = root / "releases"
        # Re-check the configured path at use time: these paths may have changed since
        # the target descriptor was loaded.
        if _trusted_path(str(root)) != root or _trusted_path(str(releases_root)) != releases_root:
            raise TargetRuntimeError("managed release path resolved outside its configured root")
        root.mkdir(mode=0o750, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise TargetRuntimeError("managed root is not a real directory")
        releases_root.mkdir(mode=0o750, exist_ok=True)
        if releases_root.is_symlink() or not releases_root.is_dir() or releases_root.resolve().parent != root.resolve():
            raise TargetRuntimeError("release directory is not a controlled child of managed root")
        owner_id = target.get("managed_uid", 0)
        group_name = target.get("managed_group", "root")
        if type(owner_id) is not int or owner_id < 0:
            raise TargetRuntimeError("configured managed UID is invalid")
        try:
            group_id = grp.getgrnam(group_name).gr_gid
        except (KeyError, TypeError) as exc:
            raise TargetRuntimeError("configured managed service group does not exist") from exc
        os.chown(root, owner_id, group_id)
        os.chmod(root, 0o750)
        os.chown(releases_root, owner_id, group_id)
        os.chmod(releases_root, 0o750)
        return releases_root

    def _mihomo_request(self, target: dict[str, Any], api_path: str, *, data: bytes | None = None,
                        method: str | None = None, timeout: int = 3):
        url = target.get("mihomo_controller_url")
        origin = _mihomo_controller_origin(url)
        host, _port = origin
        if host != "localhost":
            address = ipaddress.ip_address(host)
            if not address.is_loopback and not _is_current_local_ipv4(host):
                raise TargetRuntimeError("Mihomo controller IPv4 is not assigned to this machine")

        config_path = target["integration"].get("mihomo_config")
        try:
            live_path = _trusted_path(config_path)
            live = yaml.safe_load(live_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError, TypeError) as exc:
            raise TargetRuntimeError("cannot verify live Mihomo controller configuration") from exc
        if not isinstance(live, dict):
            raise TargetRuntimeError("live Mihomo configuration is invalid")
        if _config_controller_origin(live.get("external-controller")) != origin:
            raise TargetRuntimeError("configured Mihomo endpoint does not match live external-controller")
        secret = live.get("secret", "")
        if not isinstance(secret, str):
            raise TargetRuntimeError("live Mihomo API secret is invalid")

        request_host = "127.0.0.1" if host == "localhost" else host
        if ":" in request_host:
            request_host = f"[{request_host}]"
        endpoint = f"http://{request_host}:{origin[1]}" + api_path
        headers = {}
        if secret:
            headers["Authorization"] = "Bearer " + secret
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        # Never inherit HTTP_PROXY/HTTPS_PROXY and never forward credentials across a redirect.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        return opener.open(request, timeout=timeout)

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
                live_raw = self._live_hashes(target)
                live_managed = self._managed_hashes(target)
                if not self._marker_hashes_match(target, release_id, value, expected, live_raw, live_managed):
                    raise TargetRuntimeError("active release marker does not match running service configuration")
                expected_guards = value.get("guard_sha256")
                if not isinstance(expected_guards, dict) or self._guard_hashes(target) != expected_guards:
                    raise TargetRuntimeError("active release marker does not match target configuration or service units")
                preferences_path = self._strategy_preferences_path(target)
                if value.get("strategy_preferences_revision") is not None:
                    preferences = self._load_strategy_preferences(target)
                    current_preferences_hash = _sha(preferences_path.read_bytes()) if preferences_path.exists() else _json_hash({"revision": 0, "groups": {}})
                    if (value["strategy_preferences_revision"] != preferences["revision"]
                            or value.get("strategy_preferences_sha256") != current_preferences_hash):
                        raise TargetRuntimeError("active release marker does not match strategy preferences")
                elif preferences_path.exists():
                    raise TargetRuntimeError("strategy preferences changed outside a verified target operation")
                self._check_service_status(target)
                return release_id
            except (OSError, json.JSONDecodeError):
                raise TargetRuntimeError("active target release marker is corrupt")
        return self._legacy_current(target)

    def _marker_hashes_match(self, target: dict[str, Any], release_id: str, marker: dict[str, Any],
                            expected_raw: Any, live_raw: dict[str, str], live_managed: dict[str, str]) -> bool:
        services = set(target["services"])
        if (not isinstance(expected_raw, dict) or set(expected_raw) != services
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in expected_raw.values())):
            return False
        version = marker.get("hash_schema_version")
        if version is not None and version != 2:
            return False
        if version == 2:
            expected_managed = marker.get("managed_config_sha256")
            return (isinstance(expected_managed, dict) and set(expected_managed) == services
                    and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                            for value in expected_managed.values())
                    and live_managed == expected_managed)
        if live_raw == expected_raw:
            return True
        # Legacy compatibility is deliberately narrow: only accept subscription-only drift
        # when the old raw marker hash is independently backed by the exact staged artifact.
        if "mihomo" not in services or any(live_raw[name] != expected_raw[name] for name in services - {"mihomo"}):
            return False
        try:
            integration = self._integration(target, release_id)
            artifact = self._release_dir(target, release_id) / "integration/mihomo.conf"
            artifact_raw = integration["candidate_config_sha256"]["mihomo"]
            if expected_raw.get("mihomo") != artifact_raw:
                return False
            candidate = artifact.read_bytes()
            return live_managed["mihomo"] == _managed_config_hash("mihomo", candidate)
        except (TargetRuntimeError, OSError, KeyError, TypeError):
            return False

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
        address_rows = []
        for row in integration["direct_exceptions"]:
            pattern = f"-.{row['domain']}" if row["match_type"] == "EXACT" else row["domain"]
            exceptions.append(f"nameserver /{pattern}/-")
            address_rows.append(f"address /{pattern}/-")
        # A live schema-4 base already contains the approved overlay from its
        # previous release. Replace those exact generated lines so each candidate
        # contains one copy while unrelated SmartDNS rules remain untouched.
        managed_exception_lines = set(exceptions) | set(address_rows)
        lines = [line for line in lines if line not in managed_exception_lines]
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
        active_release_id = self._current(target)
        previous_release = None
        previous_summary = None
        previous_order: list[str] = []
        previous_exceptions: list[dict[str, str]] = []
        if active_release_id:
            previous_path = self._release_dir(target, active_release_id)
            if previous_path.exists() or previous_path.is_symlink():
                if previous_path.is_symlink() or not previous_path.is_dir():
                    raise TargetRuntimeError("active managed release path is unsafe")
                try:
                    previous_envelope = json.loads((previous_path / "envelope.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise TargetRuntimeError("active managed release envelope is invalid") from exc
                if (not isinstance(previous_envelope, dict) or previous_envelope.get("release_id") != active_release_id
                        or previous_envelope.get("target_id") != target["_target_id"]
                        or previous_envelope.get("role") != target["role"]):
                    raise TargetRuntimeError("active managed release identity mismatch")
                previous_order = previous_envelope.get("mihomo_rule_order")
                previous_exceptions = _direct_exception_rows(previous_envelope.get("direct_exceptions"))
                if (not isinstance(previous_order, list) or not all(isinstance(name, str) for name in previous_order)
                        or len(previous_order) != len(set(previous_order))):
                    raise TargetRuntimeError("active managed provider order is invalid")
                previous_provider_map = previous_envelope.get("provider_map")
                if not isinstance(previous_provider_map, list) or not all(isinstance(row, dict) for row in previous_provider_map):
                    raise TargetRuntimeError("active managed provider map is invalid")
                previous_release = previous_path
                self._release_tree_paths(previous_release)
                previous_summary = self._integration(target, active_release_id)
                expected_names = [row.get("name") for row in previous_provider_map]
                if (expected_names != previous_order
                        or previous_summary.get("mihomo_rule_order") != previous_order
                        or previous_summary.get("provider_policy_map") != previous_envelope.get("provider_policy_map")):
                    raise TargetRuntimeError("active managed provider metadata disagrees with its envelope")
        preserved_direct_rules = [rule for rule in old_rules if rule.startswith("RULE-SET,") and len(rule.split(",")) >= 3 and rule.split(",", 2)[1] in DIRECT_PROVIDER_NAMES and rule.split(",", 2)[2] == "DIRECT"]
        current_exceptions = _direct_exception_rows(envelope.get("direct_exceptions"))
        old_exception_shapes = {
            ("DOMAIN" if row["match_type"] == "EXACT" else "DOMAIN-SUFFIX", row["domain"])
            for row in previous_exceptions
        }
        current_exception_shapes = {
            ("DOMAIN" if row["match_type"] == "EXACT" else "DOMAIN-SUFFIX", row["domain"])
            for row in current_exceptions
        }
        static_rules: list[str] = []
        old_exception_counts = {shape: 0 for shape in old_exception_shapes}
        for rule in old_rules:
            if rule.startswith("RULE-SET,") or rule.startswith("MATCH,"):
                continue
            parts = rule.split(",")
            shape = (parts[0], parts[1]) if len(parts) >= 2 else None
            if shape in old_exception_shapes | current_exception_shapes:
                if len(parts) != 3 or parts[2] != "DIRECT":
                    raise TargetRuntimeError("approved Mihomo direct exception conflicts with a base rule")
                if shape in old_exception_counts:
                    old_exception_counts[shape] += 1
                continue
            static_rules.append(rule)
        if previous_release is not None and any(count != 1 for count in old_exception_counts.values()):
            raise TargetRuntimeError("active managed Mihomo direct exceptions are missing or duplicated")
        preserved_static_rules = static_rules
        direct_rules = [f"DOMAIN,{row['domain']},DIRECT" if row["match_type"] == "EXACT" else f"DOMAIN-SUFFIX,{row['domain']},DIRECT" for row in current_exceptions]
        base_root = integration["mihomo_rules_root"].rstrip("/")
        file_hashes: dict[str, str] = {}
        selected_choices = self._query_mihomo_selections(target, set(envelope.get("provider_policy_map", {}).values()), by_name)
        copied_provider_names = []
        legacy_link = Path(target["legacy_link"])
        legacy_root = None
        if previous_release is None:
            if not legacy_link.is_symlink():
                raise TargetRuntimeError("legacy schema-v3 provider snapshot is unavailable")
            legacy_root = self._legacy_version_root(legacy_link)
        for name in sorted(DIRECT_PROVIDER_NAMES):
            if name not in providers:
                raise TargetRuntimeError(f"protected direct provider {name} is missing from the base config")
            entry = providers[name]
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].startswith("/"):
                raise TargetRuntimeError(f"existing protected provider {name} has invalid path")
            relative = Path("mihomo/providers") / f"{name}.yaml"
            if previous_release is not None:
                expected_path = (Path(integration["mihomo_rules_root"]) / "releases" / active_release_id / "files" / relative)
                if Path(entry["path"]) != expected_path:
                    raise TargetRuntimeError(f"active managed provider path changed: {name}")
                source = previous_release / "files" / relative
                if previous_summary is None:
                    raise TargetRuntimeError("active managed release summary is unavailable")
                recorded_hash = previous_summary.get("metadata", {}).get("strategy", {}).get("provider_payload_sha256", {}).get(relative.as_posix())
                if source.is_symlink() or not source.is_file() or _sha(source.read_bytes()) != recorded_hash:
                    raise TargetRuntimeError(f"active managed protected provider changed: {name}")
            else:
                source_config_path = Path(entry["path"])
                try:
                    source_relative = source_config_path.relative_to(legacy_link.parent / "current")
                except ValueError as exc:
                    try:
                        source_relative = source_config_path.relative_to(legacy_root)
                    except ValueError:
                        raise TargetRuntimeError(f"protected provider {name} is outside the legacy rule root") from exc
                if any(part in {".", ".."} for part in source_relative.parts):
                    raise TargetRuntimeError(f"protected provider {name} has an unsafe legacy path")
                source = legacy_root / source_relative
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
            current_ref = None
            if isinstance(selection, str) and selection not in members:
                current_ref = self._current_selection_reference(target, selection, config)
                if current_ref["kind"] == "provider":
                    group["use"] = [current_ref["provider"]]
                    group["filter"] = "^(?:" + re.escape(selection) + ")$"
                else:
                    members.insert(0, selection)
            if selection in members:
                members.remove(selection)
                members.insert(0, selection)
            group["type"] = "select"
            # Replace direct-node, include-all-provider, filter, and use based membership.
            for unsupported_field in ("use", "filter", "include-all", "include-all-proxies", "include-all-providers", "exclude-filter"):
                if not (current_ref and current_ref["kind"] == "provider" and unsupported_field in {"use", "filter"}):
                    group.pop(unsupported_field, None)
            group["proxies"] = list(dict.fromkeys(members))
        preferences = self._load_strategy_preferences(target)
        managed_policies = self._managed_policy_groups(config, {row.get("policy_group") for row in provider_map if isinstance(row, dict) and isinstance(row.get("policy_group"), str)})
        for policy, preference in preferences["groups"].items():
            # Keep preferences for policies absent from this release dormant.
            if policy not in by_name or policy not in managed_policies:
                continue
            if not isinstance(preference, dict) or set(preference) != {"members", "selected"}:
                raise TargetRuntimeError(f"saved strategy preference for {policy} is invalid")
            members = preference["members"]
            if not isinstance(members, list) or not members:
                raise TargetRuntimeError(f"saved strategy candidates for {policy} are empty")
            names: set[str] = set()
            used: list[str] = []
            provider_names: list[str] = []
            concrete: list[str] = []
            for member in members:
                name = self._reference_name(member)
                kind = member["kind"]
                if name in names:
                    raise TargetRuntimeError(f"saved strategy candidates for {policy} contain duplicate names")
                names.add(name)
                if kind == "group":
                    if name not in by_name or name == policy:
                        raise TargetRuntimeError(f"saved strategy group for {policy} is unavailable")
                    concrete.append(name)
                elif kind == "proxy":
                    proxies_raw = config.get("proxies", [])
                    if sum(1 for row in proxies_raw if isinstance(row, dict) and row.get("name") == name) != 1:
                        raise TargetRuntimeError(f"saved strategy node for {policy} is unavailable or ambiguous")
                    concrete.append(name)
                elif kind == "special":
                    if name != "REJECT":
                        raise TargetRuntimeError(f"saved special strategy member for {policy} is unsupported")
                    concrete.append(name)
                else:
                    provider_name = member["provider"]
                    providers_raw = config.get("proxy-providers", {})
                    if provider_name not in providers_raw:
                        raise TargetRuntimeError(f"saved strategy provider for {policy} is unavailable")
                    used.append(provider_name)
                    provider_names.append(name)
            selected = preference["selected"]
            selected_name = self._reference_name(selected)
            if selected not in members:
                raise TargetRuntimeError(f"saved selection for {policy} is no longer a candidate")
            if any(member["kind"] == "provider" for member in members):
                provider_catalog = self._strategy_provider_members(target)
                for member in members:
                    if member["kind"] == "provider":
                        matches = [provider for provider, nodes in provider_catalog.items() if member["name"] in nodes]
                        if matches != [member["provider"]]:
                            raise TargetRuntimeError(f"saved provider node for {policy} is unavailable or ambiguous; repair strategy preferences before preparing rules")
            for member in members:
                if member["kind"] == "special" and member["name"] != "REJECT":
                    raise TargetRuntimeError(f"saved special strategy member for {policy} is unsupported")
            group["proxies"] = concrete
            if used:
                group["use"] = list(dict.fromkeys(used))
                group["filter"] = "^(?:" + "|".join(re.escape(name) for name in provider_names) + ")$"
            else:
                group.pop("use", None)
                group.pop("filter", None)
            selected_choices[policy] = selected_name
        self._replace_mihomo_fake_ip_filters(config, order, previous_order, previous_exceptions,
                                             current_exceptions, previous_release is None)
        # Remove stale non-protection providers only when no preserved/static rules refer to them.
        keep_provider_names = DIRECT_PROVIDER_NAMES | set(order)
        referenced_by_proxy = set()
        for key in ("proxies", "proxy-groups"):
            value = config.get(key)
            if isinstance(value, list):
                blob = json.dumps(value, ensure_ascii=False)
                referenced_by_proxy.update(name for name in providers if name in blob)
        dns_filters = config.get("dns", {}).get("fake-ip-filter", [])
        if isinstance(dns_filters, list):
            referenced_by_proxy.update(
                parts[1] for item in dns_filters if isinstance(item, str)
                for parts in [item.split(",")]
                if len(parts) == 3 and parts[0] == "RULE-SET" and parts[1] in providers
            )
        config["rule-providers"] = {name: value for name, value in providers.items() if name in keep_provider_names or name in referenced_by_proxy}
        config["rules"] = [*direct_rules, *preserved_direct_rules, *preserved_static_rules,
                            *(f"RULE-SET,{name},{entry['policy_group']}" for name, entry in zip(order, provider_map, strict=True)),
                            next((rule for rule in old_rules if rule.startswith("MATCH,")), "MATCH,DIRECT")]
        region_hash_after = _json_hash([by_name[name] for name in REGIONAL_GROUPS])
        if region_hash_after != region_hash_before:
            raise TargetRuntimeError("regional selector definitions changed during candidate rendering")
        candidate = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
        return candidate, {"regional_groups_sha256": region_hash_before, "provider_payload_sha256": dict(sorted(file_hashes.items())), "selected_policy_groups": selected_choices}

    @staticmethod
    def _replace_mihomo_fake_ip_filters(config: dict[str, Any], current_order: list[str],
                                        previous_order: list[str], previous_exceptions: list[dict[str, str]],
                                        current_exceptions: list[dict[str, str]], first_migration: bool) -> None:
        dns = config.get("dns")
        if not isinstance(dns, dict) or dns.get("fake-ip-filter-mode") != "rule":
            raise TargetRuntimeError("Mihomo dns.fake-ip-filter-mode must be rule")
        filters = dns.get("fake-ip-filter")
        if not isinstance(filters, list) or not filters or not all(isinstance(item, str) for item in filters):
            raise TargetRuntimeError("Mihomo dns.fake-ip-filter must be a non-empty string list")
        parsed: list[tuple[str, str, str]] = []
        match_indices = []
        for index, item in enumerate(filters):
            parts = item.split(",")
            if parts[0] == "MATCH":
                if parts != ["MATCH", "real-ip"]:
                    raise TargetRuntimeError("Mihomo dns.fake-ip-filter MATCH must be MATCH,real-ip")
                parsed.append(("MATCH", "", "real-ip"))
                match_indices.append(index)
                continue
            if (len(parts) != 3 or any(not part or part.strip() != part or any(ord(char) < 32 for char in part) for part in parts)
                    or parts[0] not in {"RULE-SET", "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "GEOSITE"}
                    or parts[2] not in {"fake-ip", "real-ip"}):
                raise TargetRuntimeError("Mihomo dns.fake-ip-filter contains an unsupported rule")
            if parts[0] == "RULE-SET":
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", parts[1]):
                    raise TargetRuntimeError("Mihomo dns.fake-ip-filter has an invalid provider name")
            elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", parts[1]):
                raise TargetRuntimeError("Mihomo dns.fake-ip-filter has an invalid rule value")
            parsed.append((parts[0], parts[1], parts[2]))
        if match_indices != [len(filters) - 1]:
            raise TargetRuntimeError("Mihomo dns.fake-ip-filter must end with exactly one MATCH,real-ip")

        current_exception_rules = {
            ("DOMAIN" if row["match_type"] == "EXACT" else "DOMAIN-SUFFIX", row["domain"], "real-ip")
            for row in current_exceptions
        }
        current_exception_shapes = {(kind, value) for kind, value, _action in current_exception_rules}
        previous_exception_rules = {
            ("DOMAIN" if row["match_type"] == "EXACT" else "DOMAIN-SUFFIX", row["domain"], "real-ip")
            for row in previous_exceptions
        }
        previous_exception_shapes = {(kind, value) for kind, value, _action in previous_exception_rules}
        old_names = set(previous_order)
        if first_migration:
            old_names.add("proxy-domains")
        managed_names = old_names | set(current_order)
        counts = {name: 0 for name in old_names}
        previous_exception_counts = {rule: 0 for rule in previous_exception_rules}
        observed_previous_order: list[str] = []
        retained: list[str] = []
        insertion = None
        for index, (kind, value, action) in enumerate(parsed):
            key = (kind, value, action)
            managed = False
            if kind == "RULE-SET" and value in managed_names:
                if value in current_order and value not in old_names:
                    raise TargetRuntimeError("new Mihomo DNS provider collides with an existing fake-IP filter")
                if action != "fake-ip":
                    raise TargetRuntimeError("managed Mihomo DNS provider has an unexpected fake-IP action")
                managed = value in old_names
                if managed:
                    counts[value] += 1
                    observed_previous_order.append(value)
            if (kind, value) in previous_exception_shapes | current_exception_shapes:
                if action != "real-ip":
                    raise TargetRuntimeError("approved direct exception lost its real-IP action")
                managed = True
                if key in previous_exception_counts:
                    previous_exception_counts[key] += 1
            if managed:
                if insertion is None:
                    insertion = len(retained)
            else:
                retained.append(filters[index])
        if not first_migration:
            if (any(count != 1 for count in counts.values())
                    or observed_previous_order != previous_order
                    or any(count != 1 for count in previous_exception_counts.values())):
                raise TargetRuntimeError("active managed DNS filter fragments are missing or duplicated")
        if first_migration and counts.get("proxy-domains") != 1:
            raise TargetRuntimeError("schema-v3 proxy-domains DNS filter reference is missing or duplicated")
        if insertion is None:
            raise TargetRuntimeError("no previous managed DNS filter block can be replaced")
        direct_block = [f"{'DOMAIN' if row['match_type'] == 'EXACT' else 'DOMAIN-SUFFIX'},{row['domain']},real-ip"
                        for row in current_exceptions]
        provider_block = [f"RULE-SET,{name},fake-ip" for name in current_order]
        retained[insertion:insertion] = provider_block
        retained[0:0] = direct_block
        if not retained or retained[-1] != "MATCH,real-ip":
            raise TargetRuntimeError("Mihomo dns.fake-ip-filter lost its real-IP fallback")
        dns["fake-ip-filter"] = retained

    def _query_mihomo_selections(self, target: dict[str, Any], policy_groups: set[str], by_name: dict[str, dict[str, Any]]) -> dict[str, str]:
        url = target.get("mihomo_controller_url")
        if not isinstance(url, str):
            if policy_groups:
                raise TargetRuntimeError("Mihomo selector preservation requires its configured local controller")
            return {}
        try:
            with self._mihomo_request(target, "/proxies", timeout=2) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise TargetRuntimeError("Mihomo selector response exceeds size limit")
                document = json.loads(body)
        except TargetRuntimeError:
            raise
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
            result[name] = current
        return result

    def _strategy_live(self, target: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if "mihomo" not in target["services"]:
            raise TargetRuntimeError("strategies are available only on the Mihomo target")
        config_path = _trusted_path(target["integration"]["mihomo_config"])
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise TargetRuntimeError("cannot read live Mihomo configuration") from exc
        if not isinstance(config, dict):
            raise TargetRuntimeError("live Mihomo configuration is invalid")
        try:
            with self._mihomo_request(target, "/proxies", timeout=3) as response:
                proxies = json.loads(response.read(4 * 1024 * 1024 + 1))
            with self._mihomo_request(target, "/providers/proxies", timeout=3) as response:
                providers = json.loads(response.read(4 * 1024 * 1024 + 1))
        except Exception as exc:
            raise TargetRuntimeError("cannot read the live Mihomo strategy catalog") from exc
        if (not isinstance(proxies, dict) or not isinstance(proxies.get("proxies"), dict)
                or not isinstance(providers, dict) or not isinstance(providers.get("providers", {}), dict)):
            raise TargetRuntimeError("Mihomo returned an invalid strategy catalog")
        return config, proxies["proxies"], self._configured_provider_rows(config, providers.get("providers", {}))

    @staticmethod
    def _configured_provider_rows(config: dict[str, Any], rows: dict[str, Any]) -> dict[str, Any]:
        configured = config.get("proxy-providers", {})
        if not isinstance(configured, dict):
            raise TargetRuntimeError("Mihomo proxy-providers configuration is invalid")
        return {name: rows[name] for name in configured if isinstance(name, str) and isinstance(rows.get(name), dict)}

    def _strategy_provider_members(self, target: dict[str, Any]) -> dict[str, set[str]]:
        try:
            with self._mihomo_request(target, "/providers/proxies", timeout=3) as response:
                document = json.loads(response.read(4 * 1024 * 1024 + 1))
        except Exception as exc:
            raise TargetRuntimeError("cannot verify the live Mihomo provider node catalog") from exc
        rows = document.get("providers") if isinstance(document, dict) else None
        if not isinstance(rows, dict):
            raise TargetRuntimeError("Mihomo returned an invalid provider node catalog")
        config_path = _trusted_path(target["integration"]["mihomo_config"])
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise TargetRuntimeError("cannot read Mihomo providers configuration") from exc
        if not isinstance(config, dict):
            raise TargetRuntimeError("Mihomo providers configuration is invalid")
        rows = self._configured_provider_rows(config, rows)
        return {
            provider: {item["name"] for item in row.get("proxies", [])
                       if isinstance(item, dict) and isinstance(item.get("name"), str)}
            for provider, row in rows.items() if isinstance(provider, str) and isinstance(row, dict)
        }

    @staticmethod
    def _strategy_reference(name: str, config: dict[str, Any], provider_members: dict[str, set[str]]) -> dict[str, str]:
        if name == "REJECT":
            return {"kind": "special", "name": name}
        groups = [row for row in config.get("proxy-groups", []) if isinstance(row, dict) and row.get("name") == name]
        proxies = [row for row in config.get("proxies", []) if isinstance(row, dict) and row.get("name") == name]
        providers = [provider for provider, members in provider_members.items() if name in members]
        if len(groups) + len(proxies) + len(providers) != 1:
            raise TargetRuntimeError("strategy member identity is ambiguous or unavailable")
        if groups:
            return {"kind": "group", "name": name}
        if proxies:
            return {"kind": "proxy", "name": name}
        return {"kind": "provider", "provider": providers[0], "name": name}

    def _current_selection_reference(self, target: dict[str, Any], name: str, config: dict[str, Any]) -> dict[str, str]:
        return self._strategy_reference(name, config, self._strategy_provider_members(target))

    def _strategy_catalog(self, target: dict[str, Any]) -> dict[str, Any]:
        config, live, provider_rows = self._strategy_live(target)
        active_release_id = self._current(target)
        managed_groups = self._managed_policy_groups(config, self._managed_policy_names(target, active_release_id))
        provider_members = {
            provider: {item["name"] for item in row.get("proxies", []) if isinstance(item, dict) and isinstance(item.get("name"), str)}
            for provider, row in provider_rows.items() if isinstance(row, dict)
        }
        references: list[dict[str, Any]] = []
        for row in config.get("proxy-groups", []):
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                references.append({"ref": {"kind": "group", "name": row["name"]}, "name": row["name"], "kind": "group", "provider": None})
        for row in config.get("proxies", []):
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                references.append({"ref": {"kind": "proxy", "name": row["name"]}, "name": row["name"], "kind": "node", "provider": None})
        for provider, names in sorted(provider_members.items()):
            for name in sorted(names):
                references.append({"ref": {"kind": "provider", "provider": provider, "name": name}, "name": name, "kind": "node", "provider": provider})
        references.append({"ref": {"kind": "special", "name": "REJECT"}, "name": "REJECT", "kind": "special", "provider": None})
        occurrences: dict[str, int] = {}
        for item in references:
            occurrences[item["name"]] = occurrences.get(item["name"], 0) + 1
        for item in references:
            item["selectable"] = occurrences[item["name"]] == 1
            if not item["selectable"]:
                item["warning"] = "名称存在重复来源，Mihomo 无法精确区分"
        preferences = self._load_strategy_preferences(target)
        groups = []
        for name in sorted(managed_groups):
            group_config = next(row for row in config["proxy-groups"] if row.get("name") == name)
            live_group = live.get(name)
            if not isinstance(live_group, dict):
                raise TargetRuntimeError(f"managed strategy group {name} is missing from Mihomo")
            names = live_group.get("all")
            if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
                raise TargetRuntimeError(f"managed strategy group {name} returned invalid candidates")
            stored = preferences["groups"].get(name)
            warnings = []
            if stored is None:
                members = []
                for candidate in names:
                    try:
                        members.append(self._strategy_reference(candidate, config, provider_members))
                    except TargetRuntimeError:
                        warnings.append(f"当前成员 {candidate} 身份不唯一或已失效")
                try:
                    selected = self._strategy_reference(live_group.get("now"), config, provider_members)
                except TargetRuntimeError:
                    selected = {"kind": "unresolved", "name": str(live_group.get("now", ""))}
                    warnings.append("当前实际选择无法精确识别")
            else:
                members, selected = stored.get("members"), stored.get("selected")
                for reference in members if isinstance(members, list) else []:
                    available = next((item for item in references if item["ref"] == reference), None)
                    if available is None:
                        warnings.append(f"已保存成员 {reference.get('name', '') if isinstance(reference, dict) else ''} 已失效")
                    elif not available["selectable"]:
                        warnings.append(f"已保存成员 {available['name']} 存在重复来源")
                if selected not in [item["ref"] for item in references]:
                    warnings.append("已保存选择已失效")
            groups.append({"name": name, "selected": selected, "actual": live_group.get("now"), "members": members,
                           "warnings": warnings, "revision": preferences["revision"], "managed": True})
        return {"target_id": target["_target_id"], "revision": preferences["revision"], "groups": groups,
                "catalog": references}

    @staticmethod
    def _validate_strategy_members(policy: str, members: Any, selected: Any, config: dict[str, Any], live: dict[str, Any], provider_rows: dict[str, Any]) -> list[str]:
        if not isinstance(members, list) or not members or len(members) > 256:
            raise TargetRuntimeError("strategy candidates must be a non-empty bounded list")
        provider_members = {
            provider: {item["name"] for item in row.get("proxies", []) if isinstance(item, dict) and isinstance(item.get("name"), str)}
            for provider, row in provider_rows.items() if isinstance(row, dict)
        }
        names = []
        rendered = []
        for reference in members:
            name = TargetRuntime._reference_name(reference)
            if reference["kind"] not in {"group", "proxy", "provider", "special"}:
                raise TargetRuntimeError("unsupported strategy reference type")
            if reference["kind"] == "special" and name != "REJECT":
                raise TargetRuntimeError("unsupported special strategy member")
            resolved = TargetRuntime._strategy_reference(name, config, provider_members)
            if resolved != reference:
                raise TargetRuntimeError("strategy member identity does not match the live catalog")
            if reference["kind"] == "group" and name == policy:
                raise TargetRuntimeError("a strategy group cannot reference itself")
            if name in names:
                raise TargetRuntimeError("strategy candidates contain duplicate names")
            names.append(name)
            rendered.append(name)
        if selected not in members:
            raise TargetRuntimeError("selected strategy member must be in the candidate list")
        if live.get(policy, {}).get("now") not in names and selected is None:
            raise TargetRuntimeError("current strategy selection is unavailable")
        return rendered

    def _strategy_operation(self, target_id: str, operation: str, header: dict[str, Any]) -> dict[str, Any]:
        target = self._target(target_id)
        # Refuse strategy changes when the active release/configuration has drifted.
        active_release_id = self._current(target)
        preferences = self._load_strategy_preferences(target)
        if operation == "strategies":
            return {"ok": True, **self._strategy_catalog(target)}
        policy = header.get("group")
        if not isinstance(policy, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", policy):
            raise TargetRuntimeError("invalid managed strategy group")
        if type(header.get("revision")) is not int or header["revision"] != preferences["revision"]:
            raise TargetRuntimeError("strategy preferences changed; refresh before retrying")
        config, live, provider_rows = self._strategy_live(target)
        if policy not in self._managed_policy_groups(config, self._managed_policy_names(target, active_release_id)):
            raise TargetRuntimeError("strategy group is not currently managed by active rules")
        members = header.get("members")
        selected = header.get("selected")
        rendered_names = self._validate_strategy_members(policy, members, selected, config, live, provider_rows)
        if operation == "strategy-select":
            actual_members = live.get(policy, {}).get("all")
            if not isinstance(actual_members, list) or len(actual_members) != len(set(actual_members)):
                raise TargetRuntimeError("current strategy candidates are invalid")
            if set(rendered_names) != set(actual_members):
                raise TargetRuntimeError("candidate list changed; membership must be applied before selection can be saved")
        new_preferences = json.loads(json.dumps(preferences))
        new_preferences["revision"] += 1
        new_preferences["groups"][policy] = {"members": members, "selected": selected}
        current_row = live.get(policy)
        current_members = current_row.get("all") if isinstance(current_row, dict) else None
        current_selection = current_row.get("now") if isinstance(current_row, dict) else None
        if not isinstance(current_selection, str) or not isinstance(current_members, list) or not all(isinstance(item, str) for item in current_members):
            raise TargetRuntimeError("current Mihomo strategy state is incomplete")
        digest = _json_hash({"group": policy, "members": members, "selected": selected,
                             "revision": preferences["revision"], "config_sha256": _sha(_trusted_path(target["integration"]["mihomo_config"]).read_bytes()),
                             "current_selection": current_selection, "current_members": current_members})
        if operation == "strategy-preview":
            return {"ok": True, "revision": preferences["revision"], "preview_sha256": digest,
                    "group": policy, "members": members, "selected": selected}
        if header.get("preview_sha256") != digest:
            raise TargetRuntimeError("strategy preview is stale; preview the current configuration again")
        if operation == "strategy-apply":
            return self._apply_strategy_membership(target, policy, members, selected, rendered_names, new_preferences)
        if operation == "strategy-select":
            return self._select_strategy(target, policy, members, selected, live, new_preferences)
        raise TargetRuntimeError("unsupported strategy operation")

    @staticmethod
    def _validate_policy_cycles(policy: str, members: list[dict[str, str]], config: dict[str, Any]) -> None:
        groups = {row.get("name"): row for row in config.get("proxy-groups", []) if isinstance(row, dict)}
        graph = {name: [item for item in row.get("proxies", [])
                        if isinstance(item, str) and item in groups]
                 for name, row in groups.items()}
        graph[policy] = [item["name"] for item in members if item["kind"] == "group"]
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(name: str) -> None:
            if name in visiting:
                raise TargetRuntimeError("strategy group references contain a cycle")
            if name in visited:
                return
            visiting.add(name)
            for child in graph.get(name, []):
                visit(child)
            visiting.remove(name)
            visited.add(name)
        visit(policy)

    def _validate_mihomo_candidate(self, target: dict[str, Any], config_path: Path) -> None:
        commands = target.get("validate_commands", {})
        argv = commands.get("mihomo") if isinstance(commands, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise TargetRuntimeError("Mihomo validation command is not configured")
        validation_home = target["_root"] / "strategy-validation-home"
        validation_home.mkdir(mode=0o750, exist_ok=True)
        if validation_home.is_symlink() or not validation_home.is_dir():
            raise TargetRuntimeError("strategy validation home is unsafe")
        try:
            group_id = grp.getgrnam(target.get("managed_group", "root")).gr_gid
        except KeyError as exc:
            raise TargetRuntimeError("configured Mihomo group is missing") from exc
        os.chown(validation_home, target.get("managed_uid", 0), group_id)
        os.chmod(validation_home, 0o750)
        if config_path.is_symlink() or not config_path.is_file():
            raise TargetRuntimeError("strategy candidate config is unsafe")
        config_stat = config_path.stat()
        os.chown(config_path, target.get("managed_uid", 0), group_id)
        os.chmod(config_path, 0o640)
        expanded = [item.replace("{mihomo_config}", str(config_path)).replace("{validation_home}", str(validation_home)) for item in argv]
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
        extra = target.get("validation_environment", {}).get("mihomo", {})
        if isinstance(extra, dict):
            env.update({str(key): str(value).replace("{managed_root}", str(target["_root"])) for key, value in extra.items()})
        timeout = int(target.get("validation_timeout_seconds", 10))
        try:
            result = subprocess.run(expanded, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise TargetRuntimeError("Mihomo configuration validation could not run") from exc
        allowed = target.get("validation_exit_codes", {}).get("mihomo", [0])
        if result.returncode not in allowed:
            raise TargetRuntimeError("Mihomo rejected the strategy configuration")

    def _select_strategy(self, target: dict[str, Any], policy: str, members: list[dict[str, str]], selected: dict[str, str],
                         live: dict[str, Any], new_preferences: dict[str, Any]) -> dict[str, Any]:
        self._validate_policy_cycles(policy, members, yaml.safe_load(_trusted_path(target["integration"]["mihomo_config"]).read_text()))
        actual = live.get(policy, {}).get("now")
        new_name = self._reference_name(selected)
        journal = {"schema_version": 1, "operation": "select", "group": policy, "before": actual,
                   "after": new_name, "previous_preferences": self._load_strategy_preferences(target),
                   "preferences": new_preferences}
        self._write_strategy_journal(target, journal)
        try:
            self._set_mihomo_selections(target, {policy: new_name})
            self._write_strategy_preferences(target, new_preferences)
            self._refresh_active_marker(target, _trusted_path(target["integration"]["mihomo_config"]))
            self._clear_strategy_journal(target)
        except Exception:
            if isinstance(actual, str):
                try:
                    self._set_mihomo_selections(target, {policy: actual})
                    self._write_strategy_preferences(target, journal["previous_preferences"])
                    self._refresh_active_marker(target, _trusted_path(target["integration"]["mihomo_config"]))
                except Exception as rollback_exc:
                    raise TargetRuntimeError("strategy selection failed and the prior choice could not be restored") from rollback_exc
            self._clear_strategy_journal(target)
            raise
        return {"ok": True, "group": policy, "selected": new_name, "revision": new_preferences["revision"], "actual": new_name}

    def _apply_strategy_membership(self, target: dict[str, Any], policy: str, members: list[dict[str, str]], selected: dict[str, str],
                                   rendered_names: list[str], new_preferences: dict[str, Any]) -> dict[str, Any]:
        self._current(target)
        path = _trusted_path(target["integration"]["mihomo_config"])
        before = path.read_bytes()
        try:
            config = yaml.safe_load(before)
        except yaml.YAMLError as exc:
            raise TargetRuntimeError("live Mihomo configuration is invalid") from exc
        self._validate_policy_cycles(policy, members, config)
        old_selections = self._all_mihomo_selections(target, config)
        desired_selections = {**old_selections, policy: self._reference_name(selected)}
        groups = [row for row in config.get("proxy-groups", []) if isinstance(row, dict) and row.get("name") == policy]
        if len(groups) != 1:
            raise TargetRuntimeError("managed strategy group is missing or ambiguous")
        group = groups[0]
        concrete = [self._reference_name(item) for item in members if item["kind"] != "provider"]
        used = list(dict.fromkeys(item["provider"] for item in members if item["kind"] == "provider"))
        provider_names = [self._reference_name(item) for item in members if item["kind"] == "provider"]
        group["type"] = "select"
        group["proxies"] = concrete
        for key in ("include-all", "include-all-proxies", "include-all-providers", "exclude-filter"):
            group.pop(key, None)
        if used:
            group["use"] = used
            group["filter"] = "^(?:" + "|".join(re.escape(name) for name in provider_names) + ")$"
        else:
            group.pop("use", None)
            group.pop("filter", None)
        candidate = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
        candidate = self._merge_live_proxy_providers(target, candidate)
        if _managed_config_hash("mihomo", before) != self._managed_hashes(target)["mihomo"]:
            raise TargetRuntimeError("managed Mihomo config changed during strategy update")
        temp_path = target["_root"] / f"strategy-candidate-{uuid.uuid4().hex}.yaml"
        temp_path.write_bytes(candidate)
        try:
            self._validate_mihomo_candidate(target, temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
        if _sha(path.read_bytes()) != _sha(before):
            raise TargetRuntimeError("live Mihomo config changed during strategy validation")
        metadata = path.stat()
        journal = {"schema_version": 1, "operation": "apply", "path": str(path),
                   "before": base64.b64encode(before).decode(), "after": base64.b64encode(candidate).decode(),
                   "mode": metadata.st_mode & 0o777, "uid": metadata.st_uid, "gid": metadata.st_gid,
                   "previous_preferences": self._load_strategy_preferences(target),
                   "preferences": new_preferences, "previous_selections": old_selections,
                   "selections": desired_selections}
        self._write_strategy_journal(target, journal)
        try:
            self._atomic_write(path, candidate, {"mode": metadata.st_mode & 0o777, "uid": metadata.st_uid, "gid": metadata.st_gid})
            self._reload_mihomo(target, path)
            self._set_mihomo_selections(target, desired_selections)
            self._write_strategy_preferences(target, new_preferences)
            self._refresh_active_marker(target, path, expected_before_sha256=_sha(before),
                                        expected_before_managed_sha256=_managed_config_hash("mihomo", before))
            self._clear_strategy_journal(target)
        except Exception as exc:
            try:
                before_with_current_providers = self._merge_live_proxy_providers(target, before)
                self._atomic_write(path, before_with_current_providers, {"mode": metadata.st_mode & 0o777, "uid": metadata.st_uid, "gid": metadata.st_gid})
                self._reload_mihomo(target, path)
                self._set_mihomo_selections(target, old_selections)
                self._write_strategy_preferences(target, journal["previous_preferences"])
                self._refresh_active_marker(target, path)
                self._clear_strategy_journal(target)
            except Exception as rollback_exc:
                raise TargetRuntimeError("strategy membership failed and configuration recovery needs manual review") from rollback_exc
            raise TargetRuntimeError("Mihomo could not apply the strategy membership") from exc
        return {"ok": True, "group": policy, "selected": self._reference_name(selected),
                "revision": new_preferences["revision"], "configuration_sha256": _sha(candidate)}

    @staticmethod
    def _write_strategy_journal(target: dict[str, Any], journal: dict[str, Any]) -> None:
        path = target["_root"] / "strategy-intent.json"
        data = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
        temp = target["_root"] / f".strategy-intent-{uuid.uuid4().hex}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp, target.get("managed_uid", 0), TargetRuntime._runtime_gid(target))
        os.replace(temp, path)
        descriptor = os.open(target["_root"], os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _clear_strategy_journal(target: dict[str, Any]) -> None:
        (target["_root"] / "strategy-intent.json").unlink(missing_ok=True)

    @staticmethod
    def _write_rollback_journal(target: dict[str, Any], journal: dict[str, Any]) -> None:
        root = target["_root"]
        path = root / "rollback-intent.json"
        temp = root / f".rollback-intent-{uuid.uuid4().hex}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(journal, sort_keys=True, separators=(",", ":")).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temp, target.get("managed_uid", 0), TargetRuntime._runtime_gid(target))
            os.replace(temp, path)
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temp.unlink(missing_ok=True)

    def _reload_mihomo(self, target: dict[str, Any], path: Path) -> None:
        payload = json.dumps({"path": str(path)}).encode()
        with self._mihomo_request(target, "/configs?force=true", data=payload, method="PUT", timeout=10) as response:
            if response.status not in {200, 204}:
                raise TargetRuntimeError("Mihomo rejected the strategy reload")
        mihomo_target = {**target, "services": ["mihomo"],
                         "status_commands": {"mihomo": target.get("status_commands", {}).get("mihomo")}}
        self._check_service_status(mihomo_target)

    def _refresh_active_marker(self, target: dict[str, Any], path: Path, *, expected_before_sha256: str | None = None,
                                expected_before_managed_sha256: str | tuple[str, ...] | None = None) -> None:
        marker = target["_root"] / "active-release.json"
        if not marker.exists():
            return
        if marker.is_symlink() or not marker.is_file():
            raise TargetRuntimeError("active release marker is unsafe")
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TargetRuntimeError("active release marker is invalid") from exc
        if not isinstance(value, dict) or not isinstance(value.get("candidate_config_sha256"), dict):
            raise TargetRuntimeError("active release marker configuration hashes are invalid")
        hashes = self._live_hashes(target)
        managed_hashes = self._managed_hashes(target)
        expected = value["candidate_config_sha256"]
        service_name = next((name for name, configured in target["configs"].items() if str(_trusted_path(configured)) == str(path)), None)
        if service_name is None or service_name not in expected:
            raise TargetRuntimeError("strategy config does not match an active managed service")
        for service, observed in hashes.items():
            if service == service_name:
                continue
            expected_managed = value.get("managed_config_sha256") if value.get("hash_schema_version") == 2 else None
            if (expected_managed.get(service) != managed_hashes[service]
                    if isinstance(expected_managed, dict) else expected.get(service) != observed):
                raise TargetRuntimeError("unrelated live service configuration drifted during strategy update")
        prior = expected.get(service_name)
        expected_before_managed = ({expected_before_managed_sha256}
                                    if isinstance(expected_before_managed_sha256, str)
                                    else set(expected_before_managed_sha256 or ()))
        if prior == hashes[service_name]:
            expected_before_managed.add(managed_hashes[service_name])
        if expected_before_sha256 is not None and value.get("hash_schema_version") == 2:
            prior_managed = value.get("managed_config_sha256", {}).get(service_name)
            raw_reference_matches = prior in {expected_before_sha256, hashes[service_name]}
            managed_reference_matches = prior_managed in expected_before_managed | {managed_hashes[service_name]}
            if not raw_reference_matches and not managed_reference_matches:
                raise TargetRuntimeError("active managed Mihomo config changed before strategy update")
        if (expected_before_sha256 is not None and value.get("hash_schema_version") != 2
                and prior not in {expected_before_sha256, hashes[service_name]}):
            # Older strategy-updated markers may not have a reference artifact for their raw hash.
            # Preserve their fail-closed behavior when this update cannot prove its before state.
            raise TargetRuntimeError("active managed Mihomo config drifted during strategy selection")
        if expected_before_sha256 is None and value.get("hash_schema_version") == 2 and prior != hashes[service_name]:
            if value.get("managed_config_sha256", {}).get(service_name) != managed_hashes[service_name]:
                raise TargetRuntimeError("active managed Mihomo config drifted during strategy selection")
        if expected_before_sha256 is None and value.get("hash_schema_version") != 2 and prior != hashes[service_name]:
            raise TargetRuntimeError("active managed Mihomo config drifted during strategy selection")
        guard_expected = value.get("guard_sha256")
        if not isinstance(guard_expected, dict) or guard_expected != self._guard_hashes(target):
            raise TargetRuntimeError("target guard configuration drifted during strategy update")
        expected[service_name] = hashes[service_name]
        value["candidate_config_sha256"] = expected
        value["hash_schema_version"] = 2
        value["managed_config_sha256"] = managed_hashes
        value["strategy_preferences_revision"] = self._load_strategy_preferences(target)["revision"]
        value["strategy_preferences_sha256"] = _sha(self._strategy_preferences_path(target).read_bytes()) if self._strategy_preferences_path(target).exists() else _json_hash({"revision": 0, "groups": {}})
        stat = marker.stat()
        self._atomic_write(marker, json.dumps(value, sort_keys=True).encode(), {"mode": stat.st_mode & 0o777, "uid": stat.st_uid, "gid": stat.st_gid})

    def _recover_strategy_intent(self, target: dict[str, Any]) -> None:
        path = target["_root"] / "strategy-intent.json"
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise TargetRuntimeError("strategy recovery journal is unsafe")
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TargetRuntimeError("strategy recovery journal is corrupt") from exc
        if not isinstance(journal, dict) or journal.get("schema_version") != 1:
            raise TargetRuntimeError("strategy recovery journal requires manual review")
        if journal.get("operation") == "select":
            _config, live, _providers = self._strategy_live(target)
            current = live.get(journal.get("group"), {}).get("now")
            if current == journal.get("after"):
                self._write_strategy_preferences(target, journal["preferences"])
                self._refresh_active_marker(target, _trusted_path(target["integration"]["mihomo_config"]))
            elif current == journal.get("before"):
                self._write_strategy_preferences(target, journal["previous_preferences"])
                self._refresh_active_marker(target, _trusted_path(target["integration"]["mihomo_config"]))
            else:
                raise TargetRuntimeError("interrupted strategy selection has an unknown live value")
            self._clear_strategy_journal(target)
            return
        if journal.get("operation") == "apply":
            expected_path = str(_trusted_path(target["integration"]["mihomo_config"]))
            if journal.get("path") != expected_path:
                raise TargetRuntimeError("strategy recovery journal targets an unexpected config")
            live_path = Path(expected_path)
            current = live_path.read_bytes()
            try:
                before = base64.b64decode(journal["before"], validate=True)
                after = base64.b64decode(journal["after"], validate=True)
            except (KeyError, ValueError, TypeError) as exc:
                raise TargetRuntimeError("strategy recovery journal payload is invalid") from exc
            metadata = {key: journal[key] for key in ("mode", "uid", "gid")}
            current_managed = _managed_config_hash("mihomo", current)
            before_managed = _managed_config_hash("mihomo", before)
            after_managed = _managed_config_hash("mihomo", after)
            if current_managed == after_managed:
                self._reload_mihomo(target, live_path)
                self._set_mihomo_selections(target, journal["selections"])
                self._write_strategy_preferences(target, journal["preferences"])
                self._refresh_active_marker(target, live_path, expected_before_sha256=_sha(before),
                                            expected_before_managed_sha256=(before_managed, after_managed))
            elif current_managed == before_managed:
                self._set_mihomo_selections(target, journal["previous_selections"])
                self._write_strategy_preferences(target, journal["previous_preferences"])
                self._refresh_active_marker(target, live_path, expected_before_sha256=_sha(after),
                                            expected_before_managed_sha256=(before_managed, after_managed))
            else:
                raise TargetRuntimeError("interrupted strategy config has an unknown live value")
            self._clear_strategy_journal(target)
            return
        raise TargetRuntimeError("strategy recovery journal operation is unknown")

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
        baseline_managed: dict[str, str] = {}
        candidate_managed: dict[str, str] = {}
        metadata: dict[str, dict[str, int]] = {}
        for service in target["services"]:
            live = _trusted_path(configs[service])
            if not live.is_file() or live.is_symlink():
                raise TargetRuntimeError(f"live {service} configuration is missing or unsafe")
            original = live.read_bytes()
            baselines[service] = _sha(original)
            baseline_managed[service] = _managed_config_hash(service, original)
            stat = live.stat()
            metadata[service] = {"mode": stat.st_mode & 0o777, "uid": stat.st_uid, "gid": stat.st_gid}
            (rollback_root / f"{service}.conf").write_bytes(original)
            if service == "smartdns":
                candidate = self._smartdns_config(target, release_id, original)
            else:
                candidate, evidence = self._mihomo_config(target, release_id, original, release_dir, envelope)
                preferences = self._load_strategy_preferences(target)
                evidence["strategy_preferences_revision"] = preferences["revision"]
                pref_path = self._strategy_preferences_path(target)
                evidence["strategy_preferences_sha256"] = _sha(pref_path.read_bytes()) if pref_path.exists() else _json_hash({"revision": 0, "groups": {}})
                metadata["strategy"] = evidence
            (service_root / f"{service}.conf").write_bytes(candidate)
            candidates[service] = _sha(candidate)
            candidate_managed[service] = _managed_config_hash(service, candidate)
        exception_rows = integration["direct_exceptions"]
        if envelope.get("direct_exceptions") != exception_rows or envelope.get("direct_exceptions_sha256") != _json_hash(exception_rows):
            raise TargetRuntimeError("controller direct-exception contract differs from target-local approved baseline")
        summary = {
            "schema_version": 1,
            "managed_hash_schema_version": 1,
            "release_id": release_id,
            "target_id": target["_target_id"],
            "services": list(target["services"]),
            "baseline_config_sha256": baselines,
            "baseline_managed_config_sha256": baseline_managed,
            "baseline_guard_sha256": self._guard_hashes(target),
            "candidate_config_sha256": candidates,
            "candidate_managed_config_sha256": candidate_managed,
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
    def _release_tree_paths(release_dir: Path) -> list[Path]:
        if release_dir.is_symlink() or not release_dir.is_dir():
            raise TargetRuntimeError("staged release is not a real directory")
        resolved_root = release_dir.resolve()
        paths = sorted(release_dir.rglob("*"), key=lambda item: len(item.parts))
        for path in paths:
            if path.is_symlink():
                raise TargetRuntimeError("staged release unexpectedly contains symlinks")
            resolved_path = path.resolve()
            if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
                raise TargetRuntimeError("staged release path escapes its controlled directory")
        return paths

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
        releases_root = TargetRuntime._prepare_releases_root(target)
        if release_dir.is_symlink() or not release_dir.is_dir() or release_dir.parent != releases_root:
            raise TargetRuntimeError("staged release is not a controlled child of releases")
        paths = TargetRuntime._release_tree_paths(release_dir)
        os.chown(target["_root"], owner_id, group_id)
        os.chmod(target["_root"], 0o750)
        for path in paths:
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
            managed = summary.get("candidate_managed_config_sha256")
            if (summary.get("managed_hash_schema_version") == 1
                    and (not isinstance(managed, dict)
                         or _managed_config_hash(service, path.read_bytes()) != managed.get(service))):
                raise TargetRuntimeError(f"staged {service} managed configuration metadata changed")
        strategy = summary.get("metadata", {}).get("strategy", {})
        for relative, digest in strategy.get("provider_payload_sha256", {}).items():
            path = self._release_dir(target, release_id) / "files" / relative
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != digest:
                raise TargetRuntimeError(f"managed provider snapshot changed: {relative}")
        return summary

    def _integration_managed_hashes(self, target: dict[str, Any], release_id: str, kind: str) -> dict[str, str]:
        integration = self._integration(target, release_id)
        raw_key = f"{kind}_config_sha256"
        raw_values = integration.get(raw_key)
        if not isinstance(raw_values, dict) or set(raw_values) != set(target["services"]):
            raise TargetRuntimeError("staged configuration comparison metadata is incomplete")
        # Older integrations retain full raw bytes; verify them before deriving their managed projection.
        folder = "rollback" if kind == "baseline" else "integration"
        root = self._release_dir(target, release_id) / folder
        actual: dict[str, str] = {}
        for service in target["services"]:
            path = root / f"{service}.conf"
            if path.is_symlink() or not path.is_file():
                raise TargetRuntimeError("staged configuration comparison source is unsafe")
            data = path.read_bytes()
            if _sha(data) != raw_values.get(service):
                raise TargetRuntimeError("staged configuration comparison source changed")
            actual[service] = _managed_config_hash(service, data)
        if integration.get("managed_hash_schema_version") == 1:
            values = integration.get(f"{kind}_managed_config_sha256")
            if not isinstance(values, dict) or values != actual:
                raise TargetRuntimeError("staged managed configuration metadata is invalid")
        return actual

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

    def _managed_hashes(self, target: dict[str, Any]) -> dict[str, str]:
        return {name: _managed_config_hash(name, _trusted_path(path).read_bytes())
                for name, path in target["configs"].items()}

    @staticmethod
    def _merge_live_proxy_providers(target: dict[str, Any], candidate: bytes) -> bytes:
        """Keep subscription-owned provider definitions from the latest live config."""
        live_path = _trusted_path(target["integration"]["mihomo_config"])
        try:
            live = yaml.safe_load(live_path.read_bytes())
            rendered = yaml.safe_load(candidate)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise TargetRuntimeError("Mihomo config cannot preserve live proxy providers") from exc
        if not isinstance(live, dict) or not isinstance(rendered, dict):
            raise TargetRuntimeError("Mihomo config cannot preserve live proxy providers")
        if "proxy-providers" in live:
            if not isinstance(live["proxy-providers"], dict):
                raise TargetRuntimeError("live Mihomo proxy-providers must be a mapping")
            rendered["proxy-providers"] = live["proxy-providers"]
        else:
            rendered.pop("proxy-providers", None)
        return yaml.safe_dump(rendered, allow_unicode=True, sort_keys=False).encode("utf-8")

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
            raise TargetRuntimeError("Mihomo selector restore requires its configured local controller")
        deadline = self._monotonic() + self._MIHOMO_READY_TIMEOUT
        while True:
            try:
                rows = self._mihomo_proxy_rows(target)
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}:
                    raise TargetRuntimeError("Mihomo selector API authorization failed") from exc
                if not self._transient_http_error(exc) or self._monotonic() >= deadline:
                    raise TargetRuntimeError("Mihomo selector API did not become ready") from exc
                self._sleep(min(self._MIHOMO_RETRY_INTERVAL, max(0, deadline - self._monotonic())))
                continue
            unavailable = []
            for group, choice in expected.items():
                if group not in rows:
                    unavailable.append(group)
                    continue
                choices = rows[group].get("all")
                if not isinstance(choices, list) or not all(isinstance(item, str) for item in choices):
                    raise TargetRuntimeError("Mihomo returned an invalid selector member list")
                if choice not in choices:
                    unavailable.append(group)
            if not unavailable:
                break
            if self._monotonic() >= deadline:
                raise TargetRuntimeError("Mihomo selector group or member remained unavailable")
            self._sleep(min(self._MIHOMO_RETRY_INTERVAL, max(0, deadline - self._monotonic())))
        for group, choice in sorted(expected.items()):
            while True:
                try:
                    payload = json.dumps({"name": choice}).encode()
                    with self._mihomo_request(target, "/proxies/" + urllib.parse.quote(group, safe=""),
                                              data=payload, method="PUT", timeout=3) as response:
                        if response.status not in {200, 204}:
                            raise TargetRuntimeError("Mihomo rejected selector restore")
                    break
                except Exception as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}:
                        raise TargetRuntimeError(f"Mihomo rejected selector restore authorization for {group}") from exc
                    if not self._transient_http_error(exc) or self._monotonic() >= deadline:
                        raise TargetRuntimeError(f"Mihomo selector restore failed for {group}") from exc
                    self._sleep(min(self._MIHOMO_RETRY_INTERVAL, max(0, deadline - self._monotonic())))
        while True:
            try:
                rows = self._mihomo_proxy_rows(target)
                actual = {}
                for name in expected:
                    if name not in rows:
                        continue
                    current = rows[name].get("now")
                    if not isinstance(current, str):
                        raise TargetRuntimeError("Mihomo returned an invalid selector readback")
                    actual[name] = current
            except Exception as exc:
                if not self._transient_http_error(exc) or self._monotonic() >= deadline:
                    raise TargetRuntimeError("Mihomo selector restore readback failed") from exc
            else:
                if actual == expected:
                    return
                if self._monotonic() >= deadline:
                    raise TargetRuntimeError("Mihomo selector restore readback mismatch")
            self._sleep(min(self._MIHOMO_RETRY_INTERVAL, max(0, deadline - self._monotonic())))

    def _mihomo_proxy_rows(self, target: dict[str, Any]) -> dict[str, Any]:
        with self._mihomo_request(target, "/proxies", timeout=3) as response:
            document = json.loads(response.read(2 * 1024 * 1024 + 1))
        rows = document.get("proxies") if isinstance(document, dict) else None
        if not isinstance(rows, dict) or any(not isinstance(name, str) or not isinstance(row, dict) for name, row in rows.items()):
            raise TargetRuntimeError("Mihomo returned invalid selector state")
        return rows

    def _read_mihomo_selection_values(self, target: dict[str, Any], names: set[str]) -> dict[str, str]:
        try:
            with self._mihomo_request(target, "/proxies", timeout=3) as response:
                document = json.loads(response.read(2 * 1024 * 1024 + 1))
        except Exception as exc:
            raise TargetRuntimeError("cannot read current Mihomo selections") from exc
        rows = document.get("proxies") if isinstance(document, dict) else None
        if not isinstance(rows, dict):
            raise TargetRuntimeError("Mihomo returned invalid selection state")
        actual = {}
        for name in names:
            row = rows.get(name)
            if not isinstance(row, dict) or not isinstance(row.get("now"), str):
                raise TargetRuntimeError(f"Mihomo selector readback is missing {name}")
            actual[name] = row["now"]
        return actual

    def _all_mihomo_selections(self, target: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
        names = {row.get("name") for row in config.get("proxy-groups", [])
                 if isinstance(row, dict) and row.get("type") == "select" and isinstance(row.get("name"), str)}
        return self._read_mihomo_selection_values(target, names)

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
        releases_root = self._prepare_releases_root(target)
        release_dir = self._release_dir(target, release_id)
        if release_dir.is_symlink() or release_dir.parent != releases_root:
            raise TargetRuntimeError("release directory may not be a symlink or escape releases root")
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
                self._release_tree_paths(release_dir)
                stored = json.loads((release_dir / "envelope.json").read_text(encoding="utf-8"))
                if stored != envelope:
                    raise TargetRuntimeError("release directory already contains another envelope")
                for relative, digest in expected.items():
                    existing = release_dir / "files" / relative
                    if not _safe_relative(relative) or existing.is_symlink() or not existing.is_file() or _sha(existing.read_bytes()) != digest:
                        raise TargetRuntimeError("existing staged release content failed integrity validation")
                self._integration(target, release_id)
                self._apply_release_permissions(target, release_dir)
                shutil.rmtree(temp)
                self._check_service_readability(target, release_dir)
                return {"ok": True, "target_id": target_id, "release_id": release_id, "integration_sha256": _sha((release_dir / "integration/integration.json").read_bytes())}
            integration_summary = self._prepare_service_configs(target, release_id, temp, envelope)
            self._save_legacy_snapshot(target, release_id, integration_summary, temp)
            (temp / "envelope.json").write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, release_dir)
            self._apply_release_permissions(target, release_dir)
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
        strategy_metadata = integration.get("metadata", {}).get("strategy", {})
        if "mihomo" in target["services"]:
            current_preferences = self._load_strategy_preferences(target)
            preferences_path = self._strategy_preferences_path(target)
            current_preferences_hash = _sha(preferences_path.read_bytes()) if preferences_path.exists() else _json_hash({"revision": 0, "groups": {}})
            if (strategy_metadata.get("strategy_preferences_revision", 0) != current_preferences["revision"]
                    or strategy_metadata.get("strategy_preferences_sha256", _json_hash({"revision": 0, "groups": {}})) != current_preferences_hash):
                raise TargetRuntimeError("strategy preferences changed after staging; prepare both targets again")
        live_hashes = self._live_hashes(target)
        live_managed_hashes = self._managed_hashes(target)
        expected_live_managed = self._integration_managed_hashes(
            target, release_id, "candidate" if self._current(target) == release_id else "baseline")
        if live_managed_hashes != expected_live_managed:
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
        managed_config_hashes = {**self._integration_managed_hashes(target, release_id, "baseline"), **current_guards}
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
        candidate_managed_hashes = self._integration_managed_hashes(target, release_id, "candidate")
        strategy = _json_hash({"envelope": envelope, "baseline_config": managed_config_hashes,
                               "candidate_config": candidate_managed_hashes,
                               "strategy_preferences_revision": strategy_metadata.get("strategy_preferences_revision", 0),
                               "strategy_preferences_sha256": strategy_metadata.get("strategy_preferences_sha256"),
                               "direct_exceptions": integration["direct_exceptions_sha256"],
                               "regional_groups": integration.get("metadata", {}).get("strategy", {}).get("regional_groups_sha256"),
                               "provider_payload_sha256": integration.get("metadata", {}).get("strategy", {}).get("provider_payload_sha256", {})})
        sha256_json = _json_hash
        return {"result": "ok", "target_id": target_id, "release_id": release_id,
                "target_manifest_sha256": sha256_json(envelope), "binary_sha256": binary_hashes,
                "config_sha256": config_hashes, "candidate_config_sha256": integration["candidate_config_sha256"],
                "managed_config_sha256": managed_config_hashes,
                "candidate_managed_config_sha256": candidate_managed_hashes,
                "running_config_sha256": live_hashes, "running_managed_config_sha256": live_managed_hashes,
                "direct_exceptions_sha256": integration["direct_exceptions_sha256"],
                "regional_groups_sha256": integration.get("metadata", {}).get("strategy", {}).get("regional_groups_sha256"),
                "strategy_validation_sha256": strategy,
                "strategy_preferences_revision": strategy_metadata.get("strategy_preferences_revision", 0),
                "strategy_preferences_sha256": strategy_metadata.get("strategy_preferences_sha256", _json_hash({"revision": 0, "groups": {}}))}

    def validate(self, target_id: str, release_id: str) -> dict[str, Any]:
        return {"ok": True, "target_id": target_id, "release_id": release_id, "evidence": self._validate(target_id, release_id)}

    def _write_activation_intent(self, target: dict[str, Any], release_id: str,
                                 previous_release_id: str | None, phase: str) -> None:
        if phase not in {"started", "configs_written", "services_restarted", "selector_restore_started",
                         "selector_restore_complete", "verified"}:
            raise TargetRuntimeError("invalid activation phase")
        path = target["_root"] / "activation-intent.json"
        value = {"release_id": release_id, "previous_release_id": previous_release_id, "phase": phase}
        temporary = target["_root"] / f".activation-intent-{uuid.uuid4().hex}"
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def recovery_status(self, target_id: str, release_id: str,
                        previous_release_id: str | None) -> dict[str, Any]:
        target = self._target(target_id)
        root = target["_root"]
        intent_path = root / "activation-intent.json"
        if intent_path.is_symlink() or not intent_path.is_file():
            raise TargetRuntimeError("matching interrupted activation intent is unavailable")
        try:
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TargetRuntimeError("interrupted activation intent is corrupt") from exc
        if (not isinstance(intent, dict) or set(intent) not in ({"release_id", "previous_release_id"},
                                                                  {"release_id", "previous_release_id", "phase"})
                or "phase" in intent and intent["phase"] not in {"started", "configs_written", "services_restarted",
                                                                  "selector_restore_started", "selector_restore_complete", "verified"}
                or not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id)
                or previous_release_id is not None and (not isinstance(previous_release_id, str)
                                                        or not re.fullmatch(r"[0-9a-f]{64}", previous_release_id))
                or previous_release_id == release_id
                or intent.get("release_id") != release_id
                or intent.get("previous_release_id") != previous_release_id):
            raise TargetRuntimeError("interrupted activation IDs do not match recovery request")
        integration = self._integration(target, release_id)
        if integration.get("previous_release_id") != previous_release_id:
            raise TargetRuntimeError("candidate metadata does not match interrupted activation")
        services = set(target["services"])
        baseline = integration.get("baseline_config_sha256")
        candidate = integration.get("candidate_config_sha256")
        guards = integration.get("baseline_guard_sha256")
        if (not isinstance(baseline, dict) or set(baseline) != services
                or not isinstance(candidate, dict) or set(candidate) != services
                or not isinstance(guards, dict)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for values in (baseline, candidate) for value in values.values())):
            raise TargetRuntimeError("candidate recovery comparison metadata is incomplete")
        live_hashes = self._managed_hashes(target)
        baseline_managed = self._integration_managed_hashes(target, release_id, "baseline")
        candidate_managed = self._integration_managed_hashes(target, release_id, "candidate")
        if any(live_hashes[service] not in {baseline_managed[service], candidate_managed[service]} for service in services):
            raise TargetRuntimeError("live service configuration is outside interrupted activation evidence")
        if self._guard_hashes(target) != guards:
            raise TargetRuntimeError("target guard files changed during interrupted activation")
        allowed_ids = {release_id}
        if previous_release_id is not None:
            allowed_ids.add(previous_release_id)
        pointer = root / "current"
        pointer_id = None
        if pointer.exists() or pointer.is_symlink():
            if not pointer.is_symlink():
                raise TargetRuntimeError("interrupted activation pointer is unsafe")
            match = re.fullmatch(r"releases/([0-9a-f]{64})", os.readlink(pointer))
            pointer_id = match.group(1) if match else None
            if pointer_id not in allowed_ids:
                raise TargetRuntimeError("interrupted activation pointer is outside transaction")
        marker_path = root / "active-release.json"
        marker_id = None
        if marker_path.exists() or marker_path.is_symlink():
            if marker_path.is_symlink() or not marker_path.is_file():
                raise TargetRuntimeError("interrupted activation marker is unsafe")
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TargetRuntimeError("interrupted activation marker is corrupt") from exc
            marker_id = marker.get("release_id") if isinstance(marker, dict) else None
            expected_marker_hashes = candidate if marker_id == release_id else baseline
            expected_marker_managed = candidate_managed if marker_id == release_id else baseline_managed
            marker_hashes_valid = (
                marker.get("hash_schema_version") == 2
                and marker.get("managed_config_sha256") == expected_marker_managed
            )
            if marker.get("hash_schema_version") != 2:
                marker_hashes_valid = marker.get("candidate_config_sha256") == expected_marker_hashes
            if (marker_id not in allowed_ids or not marker_hashes_valid or marker.get("guard_sha256") != guards):
                raise TargetRuntimeError("interrupted activation marker is outside transaction evidence")
        if marker_id == release_id and pointer_id != release_id:
            raise TargetRuntimeError("interrupted activation marker advanced ahead of its pointer")
        if pointer_id is None and marker_id is None:
            if self._legacy_current(target) != previous_release_id:
                raise TargetRuntimeError("interrupted activation has no matching prior version pointer")
        return {"ok": True, "target_id": target_id, "release_id": release_id,
                "previous_release_id": previous_release_id, "recovery_safe": True}

    def activate(self, target_id: str, release_id: str) -> dict[str, Any]:
        target = self._target(target_id)
        evidence = self._validate(target_id, release_id)
        integration = self._integration(target, release_id)
        # Recheck immediately before writing; preflight evidence alone cannot authorize stale bases.
        activation_source_hashes = self._live_hashes(target)
        if (self._managed_hashes(target) != self._integration_managed_hashes(target, release_id, "baseline")
                or self._guard_hashes(target) != integration.get("baseline_guard_sha256")):
            raise TargetRuntimeError("live base configuration drifted before activation")
        activation_contents: dict[str, bytes] = {}
        for service in target["services"]:
            source = self._release_dir(target, release_id) / "integration" / f"{service}.conf"
            content = source.read_bytes()
            if service == "mihomo":
                content = self._merge_live_proxy_providers(target, content)
                temp = target["_root"] / f"activation-candidate-{uuid.uuid4().hex}.yaml"
                temp.write_bytes(content)
                try:
                    self._validate_mihomo_candidate(target, temp)
                finally:
                    temp.unlink(missing_ok=True)
            activation_contents[service] = content
        if ({name: _managed_config_hash(name, data) for name, data in activation_contents.items()}
                != self._integration_managed_hashes(target, release_id, "candidate")):
            raise TargetRuntimeError("live subscription merge changed managed candidate configuration")
        if self._live_hashes(target) != activation_source_hashes:
            raise TargetRuntimeError("live service configuration changed during activation validation")
        intent_path = target["_root"] / "activation-intent.json"
        if intent_path.exists():
            raise TargetRuntimeError("another interrupted activation requires recovery")
        previous_release_id = integration.get("previous_release_id")
        self._write_activation_intent(target, release_id, previous_release_id, "started")
        for service, live_path in target["configs"].items():
            metadata = integration["metadata"][service]
            self._atomic_write(_trusted_path(live_path), activation_contents[service], metadata)
        self._write_activation_intent(target, release_id, previous_release_id, "configs_written")
        root = target["_root"]
        self._restart(target)
        self._write_activation_intent(target, release_id, previous_release_id, "services_restarted")
        if "mihomo" in target["services"]:
            selected = integration.get("metadata", {}).get("strategy", {}).get("selected_policy_groups", {})
            self._write_activation_intent(target, release_id, previous_release_id, "selector_restore_started")
            self._set_mihomo_selections(target, selected)
            self._write_activation_intent(target, release_id, previous_release_id, "selector_restore_complete")
        if self._managed_hashes(target) != self._integration_managed_hashes(target, release_id, "candidate"):
            raise TargetRuntimeError("service configuration readback mismatch after activation")
        self._check_service_status(target)
        pointer = root / "current"
        temporary = root / f".current-{uuid.uuid4().hex}"
        os.symlink(f"releases/{release_id}", temporary)
        os.replace(temporary, pointer)
        marker = root / "active-release.json"
        marker_temp = root / f".active-{uuid.uuid4().hex}"
        marker_temp.write_text(json.dumps({"release_id": release_id, "candidate_config_sha256": self._live_hashes(target),
                                           "hash_schema_version": 2, "managed_config_sha256": self._managed_hashes(target),
                                           "guard_sha256": self._guard_hashes(target),
                                           "strategy_preferences_revision": evidence.get("strategy_preferences_revision", 0),
                                           "strategy_preferences_sha256": evidence.get("strategy_preferences_sha256")}, sort_keys=True), encoding="utf-8")
        os.replace(marker_temp, marker)
        if (os.readlink(pointer) != f"releases/{release_id}"
                or self._managed_hashes(target) != self._integration_managed_hashes(target, release_id, "candidate")):
            raise TargetRuntimeError("activation version/config readback mismatch")
        self._write_activation_intent(target, release_id, previous_release_id, "verified")
        intent_path.unlink(missing_ok=True)
        if self._current(target) != release_id:
            raise TargetRuntimeError("activation release marker readback mismatch")
        return {"ok": True, "target_id": target_id, "release_id": release_id, "candidate_config_sha256": evidence["candidate_config_sha256"]}

    def rollback(self, target_id: str, previous_release_id: str | None) -> dict[str, Any]:
        target = self._target(target_id)
        root = target["_root"]
        intent_path = root / "activation-intent.json"
        if intent_path.exists() or intent_path.is_symlink():
            try:
                intent = json.loads(intent_path.read_text(encoding="utf-8")) if intent_path.is_file() and not intent_path.is_symlink() else None
            except (OSError, json.JSONDecodeError) as exc:
                raise TargetRuntimeError("interrupted activation intent is corrupt") from exc
            active_id = intent.get("release_id") if isinstance(intent, dict) else None
            self.recovery_status(target_id, active_id, previous_release_id)
        else:
            active_id = self._current(target)
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
        rollback_source_hashes = self._live_hashes(target)
        rollback_selections: dict[str, str] = {}
        rollback_content: bytes | None = None
        if "mihomo" in target["services"]:
            live_config = yaml.safe_load(_trusted_path(target["integration"]["mihomo_config"]).read_text(encoding="utf-8"))
            rollback_selections = self._all_mihomo_selections(target, live_config)
            source = restore_root / "mihomo.conf"
            if source.is_symlink() or not source.is_file():
                raise TargetRuntimeError("rollback Mihomo configuration snapshot is missing")
            config = yaml.safe_load(source.read_bytes())
            if not isinstance(config, dict):
                raise TargetRuntimeError("rollback Mihomo configuration snapshot is invalid")
            if "proxy-providers" in live_config:
                if not isinstance(live_config["proxy-providers"], dict):
                    raise TargetRuntimeError("live Mihomo proxy-providers must be a mapping")
                config["proxy-providers"] = live_config["proxy-providers"]
            else:
                config.pop("proxy-providers", None)
            managed_names = self._managed_policy_names(target, previous_release_id) | self._managed_policy_names(target, active_id)
            rollback_managed = self._managed_policy_groups(config, managed_names)
            preferences = self._load_strategy_preferences(target)
            try:
                with self._mihomo_request(target, "/providers/proxies", timeout=3) as response:
                    provider_document = json.loads(response.read(4 * 1024 * 1024 + 1))
                provider_rows_raw = provider_document.get("providers", {})
                provider_rows = self._configured_provider_rows(config, provider_rows_raw)
            except Exception as exc:
                raise TargetRuntimeError("cannot verify current providers during Mihomo rollback") from exc
            groups = {row.get("name"): row for row in config.get("proxy-groups", []) if isinstance(row, dict)}
            for policy, preference in preferences["groups"].items():
                if policy not in rollback_managed:
                    continue
                if policy not in groups:
                    raise TargetRuntimeError(f"saved strategy group {policy} is unavailable in rollback configuration; repair strategy preferences first")
                try:
                    self._validate_strategy_members(policy, preference["members"], preference["selected"], config, {}, provider_rows)
                except (TargetRuntimeError, KeyError, TypeError) as exc:
                    raise TargetRuntimeError(f"saved strategy for {policy} cannot be restored by this rollback; repair strategy preferences first") from exc
                group = groups[policy]
                group["proxies"] = [self._reference_name(item) for item in preference["members"] if item["kind"] != "provider"]
                used = list(dict.fromkeys(item["provider"] for item in preference["members"] if item["kind"] == "provider"))
                provider_names = [self._reference_name(item) for item in preference["members"] if item["kind"] == "provider"]
                for key in ("include-all", "include-all-proxies", "include-all-providers", "exclude-filter"):
                    group.pop(key, None)
                if used:
                    group["use"] = used
                    group["filter"] = "^(?:" + "|".join(re.escape(name) for name in provider_names) + ")$"
                else:
                    group.pop("use", None)
                    group.pop("filter", None)
                rollback_selections[policy] = self._reference_name(preference["selected"])
            for policy, preference in preferences["groups"].items():
                if policy in rollback_managed:
                    self._validate_policy_cycles(policy, preference["members"], config)
            rollback_content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
            validation_path = root / f"rollback-validation-{uuid.uuid4().hex}.yaml"
            validation_path.write_bytes(rollback_content)
            try:
                self._validate_mihomo_candidate(target, validation_path)
            finally:
                validation_path.unlink(missing_ok=True)
        restore_contents: dict[str, bytes] = {}
        for service in target["services"]:
            source = restore_root / f"{service}.conf"
            if source.is_symlink() or not source.is_file():
                raise TargetRuntimeError(f"rollback {service} configuration snapshot is missing")
            restore_contents[service] = rollback_content if service == "mihomo" and rollback_content is not None else source.read_bytes()
        rollback_journal = root / "rollback-intent.json"
        if rollback_journal.exists() or rollback_journal.is_symlink():
            raise TargetRuntimeError("an interrupted rollback requires manual recovery before another operation")
        if self._live_hashes(target) != rollback_source_hashes:
            raise TargetRuntimeError("live service configuration changed during rollback validation")
        self._write_rollback_journal(target, {
            "schema_version": 1,
            "previous_release_id": previous_release_id,
            "before_config_sha256": self._live_hashes(target),
            "restore_config_sha256": {service: _sha(content) for service, content in restore_contents.items()},
        })
        for service, live_path in target["configs"].items():
            content = restore_contents[service]
            self._atomic_write(_trusted_path(live_path), content, restore_metadata[service])
        self._restart(target)
        if "mihomo" in target["services"]:
            config = yaml.safe_load(_trusted_path(target["integration"]["mihomo_config"]).read_text(encoding="utf-8"))
            available = {row.get("name") for row in config.get("proxy-groups", []) if isinstance(row, dict)}
            self._set_mihomo_selections(target, {name: value for name, value in rollback_selections.items() if name in available})
        expected_hashes = {service: _sha(content) for service, content in restore_contents.items()}
        expected_managed_hashes = {service: _managed_config_hash(service, content) for service, content in restore_contents.items()}
        if self._managed_hashes(target) != expected_managed_hashes:
            raise TargetRuntimeError("rollback configuration readback mismatch")
        self._check_service_status(target)
        pointer = root / "current"
        if previous_release_id is not None and (root / "releases" / previous_release_id).is_dir():
            temp = root / f".current-{uuid.uuid4().hex}"
            os.symlink(f"releases/{previous_release_id}", temp)
            os.replace(temp, pointer)
            preferences = self._load_strategy_preferences(target)
            pref_path = self._strategy_preferences_path(target)
            marker_value = {"release_id": previous_release_id, "candidate_config_sha256": self._live_hashes(target),
                            "hash_schema_version": 2, "managed_config_sha256": self._managed_hashes(target),
                            "guard_sha256": previous_summary.get("baseline_guard_sha256", {}),
                            "strategy_preferences_revision": preferences["revision"],
                            "strategy_preferences_sha256": _sha(pref_path.read_bytes()) if pref_path.exists() else _json_hash({"revision": 0, "groups": {}})}
            marker_path = root / "active-release.json"
            metadata = marker_path.stat() if marker_path.exists() and not marker_path.is_symlink() else None
            if metadata:
                self._atomic_write(marker_path, json.dumps(marker_value, sort_keys=True).encode(), {"mode": metadata.st_mode & 0o777, "uid": metadata.st_uid, "gid": metadata.st_gid})
            else:
                marker_path.write_text(json.dumps(marker_value, sort_keys=True), encoding="utf-8")
        else:
            if pointer.is_symlink():
                pointer.unlink()
            marker = root / "active-release.json"
            if marker.exists():
                marker.unlink()
        intent_path.unlink(missing_ok=True)
        if self._current(target) != previous_release_id:
            raise TargetRuntimeError("rollback version readback mismatch")
        rollback_journal.unlink(missing_ok=True)
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
        target_id = header.get("target_id") if isinstance(header, dict) else None
        target_id = target_id if isinstance(target_id, str) else self.config.get("default_target_id")
        if not isinstance(target_id, str):
            raise TargetRuntimeError("target_id is missing")
        target = self._target(target_id)
        lock_path = target["_root"] / "operation.lock"
        target["_root"].mkdir(mode=0o750, parents=True, exist_ok=True)
        if lock_path.is_symlink():
            raise TargetRuntimeError("target operation lock is unsafe")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            rollback_journal = target["_root"] / "rollback-intent.json"
            if rollback_journal.exists() or rollback_journal.is_symlink():
                raise TargetRuntimeError("an interrupted rollback requires manual recovery before another operation")
            if operation != "recovery-status":
                self._recover_strategy_intent(target)
            return self._dispatch_unlocked(operation, request_id, release_id, header, archive_data)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _dispatch_unlocked(self, operation: str, request_id: str, release_id: str, header: dict[str, Any], archive_data: bytes = b"") -> dict[str, Any]:
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
        if operation == "recovery-status":
            return self.recovery_status(target_id, release_id, header.get("previous_release_id"))
        if operation == "status":
            target = self._target(target_id)
            current = self._current(target)
            if current and (target["_root"] / "releases" / current / "integration/integration.json").is_file():
                # _current verifies the atomically refreshed active marker, which
                # tracks approved strategy overlays on top of the staged release.
                self._integration(target, current)
                self._check_service_status(target)
            return {"ok": True, "target_id": target_id, "current_release_id": current}
        if operation in {"strategies", "strategy-select", "strategy-preview", "strategy-apply"}:
            return self._strategy_operation(target_id, operation, header)
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
