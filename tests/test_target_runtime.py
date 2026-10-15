from __future__ import annotations

import grp
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import socket
import tarfile
import uuid
import threading
import urllib.error
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.ssh_transport import bundle_bytes
from dns_rule_builder.target_runtime import REGIONAL_GROUPS, TargetRuntime, TargetRuntimeError, _json_hash

DIRECT = [
    *[{"domain": f"alt{i}-mtalk.google.com", "match_type": "EXACT"} for i in range(1, 9)],
    *[{"domain": name, "match_type": "EXACT"} for name in ("clientservices.googleapis.com", "dl.google.com", "dl.l.google.com", "mtalk.google.com", "update.googleapis.com")],
    {"domain": "googletraveladservices.com", "match_type": "SUFFIX"},
]
OLD_ID = "a" * 64
RID = "f52d6f04-fb33-4a1f-9796-36ea7e8c1681"


def _fake_mihomo_controller(host="127.0.0.1"):
    state = {"requests": 0, "unauthorized": 0, "selected": {"AI": "node-hk"}}
    class Handler(BaseHTTPRequestHandler):
        def _authorized(self):
            state["requests"] += 1
            if self.headers.get("Authorization") != "Bearer secret-fixture":
                state["unauthorized"] += 1
                self.send_response(401)
                self.end_headers()
                return False
            return True

        def do_GET(self):
            if not self._authorized():
                return
            if self.path == "/providers/proxies":
                payload = json.dumps({"providers": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            groups = {name: {"now": f"node-{index}", "all": [f"node-{index}"]} for index, name in enumerate(REGIONAL_GROUPS)}
            groups[REGIONAL_GROUPS[0]] = {"now": "node-hk", "all": ["node-hk"]}
            groups["AI"] = {"now": state["selected"]["AI"], "all": ["node-hk", *REGIONAL_GROUPS]}
            payload = json.dumps({"proxies": groups}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_PUT(self):
            if not self._authorized():
                return
            if self.path.startswith("/proxies/"):
                group = self.path.rsplit("/", 1)[1]
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                state["selected"][group] = body["name"]
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer((host, 0), Handler)
    server.request_state = state
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _config(root: Path, *, primary: bool = False, target_id: str = "test-target", controller_host="127.0.0.1"):
    root.mkdir(parents=True, exist_ok=True)
    live_smartdns = root / "smartdns.conf"
    base_smartdns = "server-tls 192.0.2.1\ndomain-set -name proxy-domains -type list -file /legacy/proxy-domains.txt\nnameserver /domain-set:proxy-domains/mihomo\n"
    if not primary:
        base_smartdns += "address /domain-set:proxy-domains/#\n"
    live_smartdns.write_text(base_smartdns)
    services = ["smartdns", "mihomo"] if primary else ["smartdns"]
    controller = _fake_mihomo_controller(controller_host) if primary else None
    configs = {"smartdns": str(live_smartdns)}
    binaries = {"smartdns": str(root / "smartdns")}
    (root / "smartdns").write_bytes(b"smartdns-binary")
    integration = {
        "smartdns_config": str(live_smartdns),
        "mihomo_config": None,
        "smartdns_rules_root": str(root / "managed"),
        "mihomo_rules_root": None,
        "smartdns_nameserver_line": "nameserver /domain-set:proxy-domains/mihomo",
        "smartdns_address_line": None if primary else "address /domain-set:proxy-domains/#",
        "direct_exceptions": DIRECT,
    }
    legacy = root / "legacy" / "current"
    (legacy.parent / "versions" / OLD_ID / "mihomo/providers").mkdir(parents=True)
    legacy.symlink_to(f"versions/{OLD_ID}")
    if primary:
        live_mihomo = root / "mihomo.yaml"
        regions = [{"name": name, "type": "url-test", "url": "https://example.test", "interval": 300, "proxies": ["node-hk"]} for name in REGIONAL_GROUPS]
        groups = [{"name": "ProxySelect", "type": "select", "proxies": [*REGIONAL_GROUPS, "REJECT"]}, *regions]
        direct_names = ["LAN", "Direct", "ChinaDownloadCDN", "iCloudChina", "ApplePushNotificationService", "AppleSoftwareUpdates", "ESET_China"]
        providers = {}
        for name in direct_names:
            source_relative = Path("mihomo/providers") / (f"nested/{name}.yaml" if name == "Direct" else f"{name}.yaml")
            old_file = legacy.parent / "versions" / OLD_ID / source_relative
            old_file.parent.mkdir(parents=True, exist_ok=True)
            old_file.write_text("payload: []\n")
            providers[name] = {"type": "file", "behavior": "classical", "format": "yaml", "path": str(legacy / source_relative)}
        live_mihomo.write_text(yaml.safe_dump({
            "external-controller": f"{controller_host}:{controller.server_port}",
            "secret": "secret-fixture",
            "mixed-port": 7890,
            "proxies": [{"name": "node-hk", "type": "ss", "server": "example.test", "port": 443, "cipher": "x", "password": "secret-fixture"}],
            "proxy-groups": [*groups, {"name": "AI", "type": "select", "proxies": ["node-hk"]}, {"name": "Google", "type": "select", "proxies": ["香港自动策略", "REJECT"]}],
            "rule-providers": providers,
            "rules": [*(f"RULE-SET,{name},DIRECT" for name in direct_names), "IP-CIDR,192.0.2.0/24,DIRECT", "DOMAIN,idmsa.apple.com,DIRECT", "RULE-SET,OldAI,AI", "MATCH,兜底后备策略"],
            "dns": {"enable": True, "fake-ip-filter-mode": "rule",
                    "fake-ip-filter": ["RULE-SET,proxy-domains,fake-ip", "MATCH,real-ip"],
                    "nameserver": ["tls://192.0.2.53"]},
        }, sort_keys=False))
        configs["mihomo"] = str(live_mihomo)
        binary = root / "mihomo"
        binary.write_bytes(b"mihomo-binary")
        binaries["mihomo"] = str(binary)
        integration["mihomo_config"] = str(live_mihomo)
        integration["mihomo_rules_root"] = str(root / "managed")
    target = {
        "role": "primary" if primary else "backup", "services": services, "managed_root": str(root / "managed"),
        "legacy_link": str(legacy), "allowed_files": ["smartdns/proxy-domains.txt", "mihomo/rule-providers.yaml", "mihomo/rules.yaml"],
        "allowed_file_prefixes": ["mihomo/providers/"], "binaries": binaries, "configs": configs,
        "integration": integration, "validate_commands": {name: ["/usr/bin/true"] for name in services},
        "validation_environment": {}, "validation_timeout_seconds": 4, "systemd_units": [],
        "restart": {"mode": "systemd"}, "status_commands": {name: ["/usr/bin/printf", "active"] for name in services},
        "managed_uid": os.geteuid(), "managed_group": grp.getgrgid(os.getegid()).gr_name,
    }
    if primary:
        target["validation_environment"] = {"mihomo": {"SAFE_PATHS": str(root / "managed")}}
        target["mihomo_controller_url"] = f"http://{controller_host}:{controller.server_port}"
    config_file = root / "agent.yaml"
    config_file.write_text(yaml.safe_dump({"schema_version": 1, "default_target_id": target_id, "targets": {target_id: target}}, sort_keys=False))
    runtime = TargetRuntime(config_file)
    if controller is not None:
        runtime.test_controller = controller
    return runtime, target


def test_mihomo_controller_local_bind_uses_live_secret_and_actual_local_server(tmp_path, monkeypatch):
    runtime, target = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    # The existing loopback endpoint stays compatible and reaches an actual bound local API.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    result = runtime._query_mihomo_selections(target, {"AI"}, {"AI": {}})
    assert result["AI"] == "node-hk"
    assert runtime.test_controller.request_state["requests"] == 1
    assert runtime.test_controller.request_state["unauthorized"] == 0


def test_mihomo_private_local_ipv4_bind_succeeds(tmp_path, monkeypatch):
    candidates = []
    for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
        address = item[4][0]
        if address.startswith(("127.", "169.254.")):
            continue
        try:
            import ipaddress
            parsed = ipaddress.ip_address(address)
            if parsed.version == 4 and parsed.is_private and not parsed.is_link_local:
                candidates.append(address)
        except ValueError:
            pass
    server = None
    runtime = target = None
    root = tmp_path / "private-local-bind"
    for candidate in dict.fromkeys(candidates):
        try:
            runtime, _raw_target = _config(root, primary=True, controller_host=candidate)
            server = runtime.test_controller
            target = runtime._target("test-target")
            break
        except OSError:
            continue
    if server is None:
        pytest.skip("no bindable private IPv4 interface is available in this test environment")

    class Result:
        returncode = 0
        stdout = json.dumps([{"ifname": "test0", "addr_info": [{"family": "inet", "local": candidates[0]}]}]).encode()
        stderr = b""

    # The server is genuinely bound to the host interface; stub only the fixed iproute2 read.
    local_host = target["mihomo_controller_url"].split("//", 1)[1].rsplit(":", 1)[0]
    Result.stdout = json.dumps([{"ifname": "test0", "addr_info": [{"family": "inet", "local": local_host}]}]).encode()
    monkeypatch.setattr("dns_rule_builder.target_runtime.subprocess.run", lambda *args, **kwargs: Result())
    try:
        assert runtime._query_mihomo_selections(target, {"AI"}, {"AI": {}})["AI"] == "node-hk"
        assert server.request_state["requests"] == 1
        assert server.request_state["unauthorized"] == 0
    finally:
        server.shutdown()


def test_mihomo_nonlocal_private_controller_is_rejected_before_request_or_secret(monkeypatch, tmp_path):
    runtime, raw_target = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    target["mihomo_controller_url"] = "http://10.255.50.99:9090"
    live_path = Path(target["integration"]["mihomo_config"])
    document = yaml.safe_load(live_path.read_text())
    document["external-controller"] = "10.255.50.99:9090"
    live_path.write_text(yaml.safe_dump(document, sort_keys=False))

    class Result:
        returncode = 0
        stdout = b'[{"ifname":"eth0","addr_info":[{"family":"inet","local":"10.255.50.2"}]}]'
        stderr = b""

    monkeypatch.setattr("dns_rule_builder.target_runtime.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("dns_rule_builder.target_runtime.urllib.request.build_opener", lambda *args, **kwargs: pytest.fail("non-local endpoint must be rejected before opening a request"))
    with pytest.raises(TargetRuntimeError, match="not assigned to this machine"):
        runtime._query_mihomo_selections(target, {"AI"}, {"AI": {}})
    assert runtime.test_controller.request_state["requests"] == 0


def test_mihomo_controller_port_must_match_live_config_before_request(tmp_path, monkeypatch):
    runtime, _target = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    target["mihomo_controller_url"] = target["mihomo_controller_url"].rsplit(":", 1)[0] + ":1"
    monkeypatch.setattr("dns_rule_builder.target_runtime.urllib.request.build_opener", lambda *args, **kwargs: pytest.fail("config mismatch must be rejected before opening a request"))
    with pytest.raises(TargetRuntimeError, match="does not match live external-controller"):
        runtime._query_mihomo_selections(target, {"AI"}, {"AI": {}})
    assert runtime.test_controller.request_state["requests"] == 0


def test_strategy_selection_is_revisioned_and_requires_current_membership(tmp_path, monkeypatch):
    runtime, _raw = _config(tmp_path)
    base = runtime._target("test-target")
    config_path = Path(base["integration"]["smartdns_config"])
    config = {
        "proxy-groups": [{"name": "AI", "type": "select", "proxies": ["node-a", "node-b"]}],
        "proxies": [{"name": "node-a", "type": "ss"}, {"name": "node-b", "type": "ss"}],
        "rules": ["RULE-SET,Demo,AI"], "proxy-providers": {},
    }
    mihomo_path = tmp_path / "mihomo.yaml"
    mihomo_path.write_text(yaml.safe_dump(config, sort_keys=False))
    target = {**base, "services": ["smartdns", "mihomo"], "mihomo_controller_url": "http://127.0.0.1:1",
              "integration": {**base["integration"], "mihomo_config": str(mihomo_path)},
              "configs": {"smartdns": str(config_path), "mihomo": str(mihomo_path)}}
    live = {"AI": {"now": "node-a", "all": ["node-a", "node-b"]}}
    monkeypatch.setattr(runtime, "_target", lambda _target_id: target)
    monkeypatch.setattr(runtime, "_managed_policy_names", lambda *_args: {"AI"})
    monkeypatch.setattr(runtime, "_strategy_live", lambda _target: (config, live, {}))

    def set_selected(_target, expected):
        live["AI"]["now"] = expected["AI"]
    monkeypatch.setattr(runtime, "_set_mihomo_selections", set_selected)

    members = [{"kind": "proxy", "name": "node-a"}, {"kind": "proxy", "name": "node-b"}]
    selected = members[1]
    preview = runtime.dispatch("strategy-preview", RID, "none", {
        "target_id": "test-target", "group": "AI", "revision": 0, "members": members, "selected": selected,
    })
    result = runtime.dispatch("strategy-select", RID, "none", {
        "target_id": "test-target", "group": "AI", "revision": 0, "members": members,
        "selected": selected, "preview_sha256": preview["preview_sha256"],
    })
    assert result["actual"] == "node-b"
    saved = json.loads((Path(target["managed_root"]) / "strategy-preferences.json").read_text())
    assert saved["revision"] == 1
    assert saved["groups"]["AI"] == {"members": members, "selected": selected}

    with pytest.raises(TargetRuntimeError, match="preferences changed"):
        runtime.dispatch("strategy-select", RID, "none", {
            "target_id": "test-target", "group": "AI", "revision": 0, "members": members,
            "selected": members[0], "preview_sha256": preview["preview_sha256"],
        })


def test_strategy_management_uses_active_builder_policy_map_only():
    config = {
        "proxy-groups": [
            {"name": "AI", "type": "select"}, {"name": "DNS", "type": "select"},
            {"name": "External", "type": "select"}, {"name": "ProxySelect", "type": "select"},
        ],
        "rules": ["RULE-SET,BuilderAI,AI", "RULE-SET,Other,DNS", "RULE-SET,ExternalList,External"],
    }
    assert TargetRuntime._managed_policy_groups(config, {"AI", "DNS", "External", "ProxySelect"}) == {"AI", "DNS", "External"}
    assert TargetRuntime._managed_policy_groups(config, {"AI"}) == {"AI"}


def test_strategy_reference_ignores_mihomo_compatible_provider_rows():
    config = {"proxy-groups": [{"name": "AI", "type": "select"}],
              "proxy-providers": {"actual-subscription": {"type": "http"}}}
    api_rows = {"AI": {"proxies": [{"name": "AI Compatible"}]},
                "ProxySelect": {"proxies": [{"name": "ProxySelect Compatible"}]},
                "actual-subscription": {"proxies": [{"name": "node-a"}]},
                "default": {"proxies": [{"name": "node-b"}]}}

    providers = TargetRuntime._configured_provider_rows(config, api_rows)

    assert set(providers) == {"actual-subscription"}
    assert TargetRuntime._strategy_reference("AI", config, {name: {row["name"] for row in rows["proxies"]}
        for name, rows in providers.items()}) == {"kind": "group", "name": "AI"}


def test_strategy_reload_checks_only_mihomo_service_status(tmp_path, monkeypatch):
    runtime, _raw = _config(tmp_path)
    target = runtime._target("test-target")
    target["services"] = ["smartdns", "mihomo"]
    target["status_commands"] = {"smartdns": ["smartdns-status"], "mihomo": ["mihomo-status"]}
    seen = {}

    class Response:
        status = 204

    monkeypatch.setattr(runtime, "_mihomo_request", lambda *_args, **_kwargs: nullcontext(Response()))
    monkeypatch.setattr(runtime, "_check_service_status", lambda service_target: seen.update(service_target))

    runtime._reload_mihomo(target, tmp_path / "candidate.yaml")

    assert seen["services"] == ["mihomo"]
    assert seen["status_commands"] == {"mihomo": ["mihomo-status"]}


def _make_envelope(source: Path, target: dict, *, primary=False, provider_name="Demo"):
    files = {"smartdns/proxy-domains.txt": b"example.net\n"}
    if primary:
        files.update({
            "mihomo/rule-providers.yaml": f"rule-providers:\n  {provider_name}:\n    type: file\n    behavior: classical\n    format: yaml\n    path: ./providers/{provider_name}.yaml\n".encode(),
            "mihomo/rules.yaml": f"rules:\n  - RULE-SET,{provider_name},AI\n".encode(),
            f"mihomo/providers/{provider_name}.yaml": b"payload:\n  - DOMAIN-SUFFIX,example.net\n",
        })
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    provider_map = [{"name": provider_name, "policy_group": "AI", "categories": ["demo"], "kind": "policy-run", "member_indices": [0], "path": f"mihomo/providers/{provider_name}.yaml"}] if primary else []
    exceptions = sorted(DIRECT, key=lambda row: (row["match_type"], row["domain"]))
    envelope = {
        "schema_version": 4, "target_id": "test-target", "role": target["role"], "release_id": hashlib.sha256(b"candidate").hexdigest(),
        "manifest_sha256": hashlib.sha256(b"manifest").hexdigest(), "services": target["services"],
        "files": {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
        "mihomo_rule_order": [provider_name] if primary else [], "provider_map": provider_map,
        "provider_policy_map": {provider_name: "AI"} if primary else {}, "regional_groups": list(REGIONAL_GROUPS) if primary else [],
        "direct_exceptions": exceptions, "direct_exceptions_sha256": _json_hash(exceptions),
    }
    return envelope


def test_target_runtime_stages_full_smartdns_config_and_rolls_back(tmp_path):
    runtime, target = _config(tmp_path)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target)
    payload = bundle_bytes(source, envelope)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, payload)
    summary_path = target["managed_root"]
    releases_root = Path(summary_path) / "releases"
    releases_stat = releases_root.stat()
    expected_gid = grp.getgrnam(target["managed_group"]).gr_gid
    assert releases_stat.st_gid == expected_gid
    assert releases_stat.st_mode & 0o777 == 0o750
    # Reusing an already staged release repairs only the controlled parent metadata
    # needed for service traversal, without widening access to other users.
    releases_root.chmod(0o700)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, payload)
    releases_stat = releases_root.stat()
    assert releases_stat.st_gid == expected_gid
    assert releases_stat.st_mode & 0o777 == 0o750
    integration = json.loads((Path(summary_path) / "releases" / envelope["release_id"] / "integration/integration.json").read_text())
    candidate = (Path(summary_path) / "releases" / envelope["release_id"] / "integration/smartdns.conf").read_text()
    assert integration["direct_exceptions_sha256"] == envelope["direct_exceptions_sha256"]
    assert f"/releases/{envelope['release_id']}/files/smartdns/proxy-domains.txt" in candidate
    assert "nameserver /-.mtalk.google.com/-" in candidate
    assert "nameserver /googletraveladservices.com/-" in candidate
    assert "address /-.mtalk.google.com/-" in candidate
    assert "address /googletraveladservices.com/-" in candidate
    assert "address /domain-set:proxy-domains/#" in candidate
    evidence = runtime.validate("test-target", envelope["release_id"])["evidence"]
    assert evidence["candidate_config_sha256"]["smartdns"] == integration["candidate_config_sha256"]["smartdns"]
    runtime.activate("test-target", envelope["release_id"])
    after_restart = runtime.validate("test-target", envelope["release_id"])["evidence"]
    assert after_restart["config_sha256"] == evidence["config_sha256"]
    assert after_restart["strategy_validation_sha256"] == evidence["strategy_validation_sha256"]
    assert runtime.dispatch("status", RID, "none", {"target_id": "test-target"})["current_release_id"] == envelope["release_id"]
    runtime.rollback("test-target", OLD_ID)
    assert runtime.dispatch("status", RID, "none", {"target_id": "test-target"})["current_release_id"] == OLD_ID
    assert Path(target["configs"]["smartdns"]).read_text().startswith("server-tls 192.0.2.1\n")


def test_smartdns_second_release_replaces_approved_exception_pairs_once(tmp_path):
    runtime, target = _config(tmp_path)

    def stage_candidate(name: str, release_id: str) -> str:
        source = tmp_path / name
        envelope = _make_envelope(source, target)
        envelope["release_id"] = release_id
        runtime.stage("test-target", RID, release_id,
                      {"target_id": "test-target", "envelope": envelope},
                      bundle_bytes(source, envelope))
        return (Path(target["managed_root"]) / "releases" / release_id /
                "integration/smartdns.conf").read_text()

    first_id = hashlib.sha256(b"first-smartdns-release").hexdigest()
    stage_candidate("first-smartdns-files", first_id)
    runtime.activate("test-target", first_id)

    second_id = hashlib.sha256(b"second-smartdns-release").hexdigest()
    candidate = stage_candidate("second-smartdns-files", second_id)
    generated = []
    for row in DIRECT:
        pattern = f"-.{row['domain']}" if row["match_type"] == "EXACT" else row["domain"]
        generated.extend((f"nameserver /{pattern}/-", f"address /{pattern}/-"))
    assert all(candidate.splitlines().count(line) == 1 for line in generated)
    assert sum(line.startswith("nameserver /-.") or
               (line.startswith("nameserver /") and line.endswith("/-"))
               for line in candidate.splitlines()) == len(DIRECT)
    assert sum(line.startswith("address /-.") or
               (line.startswith("address /") and line.endswith("/-"))
               for line in candidate.splitlines()) == len(DIRECT)
    assert "nameserver /domain-set:proxy-domains/mihomo" in candidate
    assert "address /domain-set:proxy-domains/#" in candidate
    assert candidate.index("nameserver /-.mtalk.google.com/-") < candidate.index("domain-set -name proxy-domains")
    assert candidate.index("address /-.mtalk.google.com/-") < candidate.index("address /domain-set:proxy-domains/#")


def _staged_smartdns_runtime_with_124_allowlisted(tmp_path):
    runtime, target = _config(tmp_path)
    config_path = tmp_path / "agent.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["targets"]["test-target"]["validation_exit_codes"] = {"smartdns": [124]}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    runtime = TargetRuntime(config_path)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target)
    runtime.stage("test-target", RID, envelope["release_id"],
                  {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    return runtime, envelope["release_id"]


def test_smartdns_allowlisted_timeout_rejects_explicit_config_error(tmp_path, monkeypatch):
    runtime, release_id = _staged_smartdns_runtime_with_124_allowlisted(tmp_path)

    def fake_run(argv, **kwargs):
        if argv == ["/usr/bin/true"]:
            return subprocess.CompletedProcess(argv, 124, b"parse error: invalid domain", b"")
        return subprocess.CompletedProcess(argv, 0, b"active", b"")

    monkeypatch.setattr("dns_rule_builder.target_runtime.subprocess.run", fake_run)
    with pytest.raises(TargetRuntimeError, match="configuration error"):
        runtime.validate("test-target", release_id)


def test_smartdns_allowlisted_timeout_accepts_normal_startup_output(tmp_path, monkeypatch):
    runtime, release_id = _staged_smartdns_runtime_with_124_allowlisted(tmp_path)

    def fake_run(argv, **kwargs):
        if argv == ["/usr/bin/true"]:
            return subprocess.CompletedProcess(argv, 124, b"network unreachable; startup continues", b"")
        return subprocess.CompletedProcess(argv, 0, b"active", b"")

    monkeypatch.setattr("dns_rule_builder.target_runtime.subprocess.run", fake_run)
    assert runtime.validate("test-target", release_id)["evidence"]["result"] == "ok"


def test_target_runtime_mihomo_preserves_base_and_routes_direct_before_dynamic(tmp_path):
    runtime, target = _config(tmp_path, primary=True)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    candidate = yaml.safe_load((Path(target["managed_root"]) / "releases" / envelope["release_id"] / "integration/mihomo.conf").read_text())
    rules = candidate["rules"]
    assert rules[0] == "DOMAIN,alt1-mtalk.google.com,DIRECT"
    smartdns_candidate = (Path(target["managed_root"]) / "releases" / envelope["release_id"] / "integration/smartdns.conf").read_text()
    assert "address /-.alt1-mtalk.google.com/-" in smartdns_candidate
    assert "address /googletraveladservices.com/-" in smartdns_candidate
    assert "DOMAIN-SUFFIX,googletraveladservices.com,DIRECT" in rules
    assert rules.index("RULE-SET,Direct,DIRECT") < rules.index("RULE-SET,Demo,AI")
    fake_filter = candidate["dns"]["fake-ip-filter"]
    direct_filter = [f"{'DOMAIN' if row['match_type'] == 'EXACT' else 'DOMAIN-SUFFIX'},{row['domain']},real-ip" for row in DIRECT]
    assert fake_filter[:len(DIRECT)] == direct_filter
    assert fake_filter[len(DIRECT)] == "RULE-SET,Demo,fake-ip"
    assert fake_filter[-1] == "MATCH,real-ip"
    assert candidate["dns"]["nameserver"] == ["tls://192.0.2.53"]
    assert rules[-1] == "MATCH,兜底后备策略"
    assert "IP-CIDR,192.0.2.0/24,DIRECT" in rules and "DOMAIN,idmsa.apple.com,DIRECT" in rules
    assert candidate["proxies"] == yaml.safe_load(Path(target["configs"]["mihomo"]).read_text())["proxies"]
    assert candidate["proxy-groups"][[x["name"] for x in candidate["proxy-groups"]].index("AI")]["proxies"][0] == "node-hk"
    runtime.test_controller.shutdown()
    runtime.test_controller.server_close()
    for name in ("LAN", "Direct", "ChinaDownloadCDN", "iCloudChina", "ApplePushNotificationService", "AppleSoftwareUpdates", "ESET_China"):
        assert candidate["rule-providers"][name]["path"].endswith(f"/files/mihomo/providers/{name}.yaml")
        assert (Path(target["managed_root"]) / "releases" / envelope["release_id"] / "files/mihomo/providers" / f"{name}.yaml").is_file()
    evidence = runtime.validate("test-target", envelope["release_id"])["evidence"]
    assert evidence["regional_groups_sha256"]
    assert evidence["direct_exceptions_sha256"] == envelope["direct_exceptions_sha256"]


def test_mihomo_render_preserves_reject_and_exact_provider_node_preferences(tmp_path, monkeypatch):
    runtime, target = _config(tmp_path, primary=True)
    live_path = Path(target["configs"]["mihomo"])
    base_config = yaml.safe_load(live_path.read_bytes())
    base_config["proxy-providers"] = {"Demo": {"type": "file", "path": "/providers/demo.yaml"}}
    live_path.write_text(yaml.safe_dump(base_config, allow_unicode=True, sort_keys=False))
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target, primary=True)
    payload = bundle_bytes(source, envelope)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, payload)
    target = runtime._target("test-target")
    node = {"kind": "provider", "provider": "Demo", "name": "node[hk].01"}
    members = [{"kind": "special", "name": "REJECT"}, node]
    runtime._write_strategy_preferences(target, {"schema_version": 1, "revision": 3,
        "groups": {"AI": {"members": members, "selected": node}}})
    monkeypatch.setattr(runtime, "_strategy_provider_members", lambda _target: {"Demo": {"node[hk].01", "other"}})

    base = yaml.safe_load(Path(target["configs"]["mihomo"]).read_bytes())
    base["proxy-providers"] = {"Demo": {"type": "file", "path": "/providers/demo.yaml"}}
    rendered, _evidence = runtime._mihomo_config(target, envelope["release_id"], yaml.safe_dump(base).encode(),
        Path(target["managed_root"]) / "releases" / envelope["release_id"], envelope)
    policy = next(row for row in yaml.safe_load(rendered)["proxy-groups"] if row["name"] == "AI")

    assert policy["proxies"] == ["REJECT"]
    assert policy["use"] == ["Demo"]
    assert policy["filter"] == "^(?:node\\[hk\\]\\.01)$"
    assert "include-all-proxies" not in policy


def test_mihomo_render_rejects_deleted_saved_provider_node(tmp_path, monkeypatch):
    runtime, target = _config(tmp_path, primary=True)
    live_path = Path(target["configs"]["mihomo"])
    base_config = yaml.safe_load(live_path.read_bytes())
    base_config["proxy-providers"] = {"Demo": {"type": "file", "path": "/providers/demo.yaml"}}
    live_path.write_text(yaml.safe_dump(base_config, allow_unicode=True, sort_keys=False))
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    target = runtime._target("test-target")
    node = {"kind": "provider", "provider": "Demo", "name": "deleted-node"}
    runtime._write_strategy_preferences(target, {"schema_version": 1, "revision": 4,
        "groups": {"AI": {"members": [node], "selected": node}}})
    monkeypatch.setattr(runtime, "_strategy_provider_members", lambda _target: {"Demo": {"other"}})

    base = yaml.safe_load(Path(target["configs"]["mihomo"]).read_bytes())
    base["proxy-providers"] = {"Demo": {"type": "file", "path": "/providers/demo.yaml"}}
    with pytest.raises(TargetRuntimeError, match="provider node.*unavailable or ambiguous"):
        runtime._mihomo_config(target, envelope["release_id"], yaml.safe_dump(base).encode(),
            Path(target["managed_root"]) / "releases" / envelope["release_id"], envelope)


def test_status_accepts_verified_active_strategy_overlay_hash(tmp_path):
    runtime, target = _config(tmp_path, primary=True)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    runtime.activate("test-target", envelope["release_id"])

    live_path = Path(target["configs"]["mihomo"])
    before = live_path.read_bytes()
    config = yaml.safe_load(before)
    ai = next(row for row in config["proxy-groups"] if row["name"] == "AI")
    ai["proxies"] = ["node-hk", "REJECT"]
    after = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()
    live_path.write_bytes(after)
    runtime._write_strategy_preferences(runtime._target("test-target"), {"schema_version": 1, "revision": 1, "groups": {}})
    runtime._refresh_active_marker(runtime._target("test-target"), live_path, expected_before_sha256=hashlib.sha256(before).hexdigest())

    result = runtime.dispatch("status", RID, "none", {"target_id": "test-target"})

    assert result["current_release_id"] == envelope["release_id"]


@pytest.mark.parametrize("node_present", [True, False])
def test_rollback_restores_special_provider_preferences_or_refuses_deleted_node(tmp_path, monkeypatch, node_present):
    runtime, raw_target = _config(tmp_path, primary=True)
    live_path = Path(raw_target["configs"]["mihomo"])
    initial = yaml.safe_load(live_path.read_text())
    initial["proxy-providers"] = {"Nodes": {"type": "file", "path": "/providers/nodes.yaml"}}
    next(row for row in initial["proxy-groups"] if row["name"] == "AI")["include-all-proxies"] = True
    live_path.write_text(yaml.safe_dump(initial, allow_unicode=True, sort_keys=False))
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, raw_target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    runtime.activate("test-target", envelope["release_id"])
    target = runtime._target("test-target")
    node = {"kind": "provider", "provider": "Nodes", "name": "node-one"}
    preferences = {"schema_version": 1, "revision": 2,
        "groups": {"AI": {"members": [{"kind": "special", "name": "REJECT"}, node], "selected": node}}}
    runtime._write_strategy_preferences(target, preferences)
    runtime._refresh_active_marker(target, live_path)

    class Response:
        def read(self, _limit):
            proxies = [{"name": "node-one"}] if node_present else [{"name": "other"}]
            return json.dumps({"providers": {"Nodes": {"proxies": proxies}}}).encode()

    monkeypatch.setattr(runtime, "_mihomo_request", lambda *_args, **_kwargs: nullcontext(Response()))
    monkeypatch.setattr(runtime, "_all_mihomo_selections", lambda *_args: {"AI": "node-hk"})
    monkeypatch.setattr(runtime, "_set_mihomo_selections", lambda *_args: None)
    monkeypatch.setattr(runtime, "_restart", lambda *_args: None)
    monkeypatch.setattr(runtime, "_check_service_status", lambda *_args: None)

    before_hash = hashlib.sha256(live_path.read_bytes()).hexdigest()
    if not node_present:
        with pytest.raises(TargetRuntimeError, match="saved strategy for AI cannot be restored"):
            runtime.rollback("test-target", OLD_ID)
        assert hashlib.sha256(live_path.read_bytes()).hexdigest() == before_hash
        assert not (Path(target["managed_root"]) / "rollback-intent.json").exists()
    else:
        runtime.rollback("test-target", OLD_ID)
        restored = yaml.safe_load(live_path.read_bytes())
        policy = next(row for row in restored["proxy-groups"] if row["name"] == "AI")
        assert policy["proxies"] == ["REJECT"]
        assert policy["use"] == ["Nodes"]
        assert policy["filter"] == "^(?:node\\-one)$"
        assert "include-all-proxies" not in policy


@pytest.mark.parametrize("config_is_after", [True, False])
def test_strategy_apply_recovery_is_idempotent_across_marker_write_window(tmp_path, monkeypatch, config_is_after):
    runtime, raw_target = _config(tmp_path, primary=True)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, raw_target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    runtime.activate("test-target", envelope["release_id"])
    target = runtime._target("test-target")
    path = Path(target["integration"]["mihomo_config"])
    before = path.read_bytes()
    config = yaml.safe_load(before)
    next(row for row in config["proxy-groups"] if row["name"] == "AI")["proxies"] = ["node-hk", "REJECT"]
    after = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()
    previous_preferences = {"schema_version": 1, "revision": 0, "groups": {}}
    new_preferences = {"schema_version": 1, "revision": 1, "groups": {}}
    path.write_bytes(after)
    runtime._write_strategy_preferences(target, new_preferences)
    runtime._refresh_active_marker(target, path, expected_before_sha256=hashlib.sha256(before).hexdigest())
    if not config_is_after:
        path.write_bytes(before)
    journal = {"schema_version": 1, "operation": "apply", "path": str(path),
        "before": base64.b64encode(before).decode(), "after": base64.b64encode(after).decode(),
        "mode": path.stat().st_mode & 0o777, "uid": path.stat().st_uid, "gid": path.stat().st_gid,
        "previous_preferences": previous_preferences, "preferences": new_preferences,
        "previous_selections": {"AI": "node-hk"}, "selections": {"AI": "REJECT"}}
    runtime._write_strategy_journal(target, journal)
    monkeypatch.setattr(runtime, "_reload_mihomo", lambda *_args: None)
    monkeypatch.setattr(runtime, "_set_mihomo_selections", lambda *_args: None)

    runtime._recover_strategy_intent(target)

    expected = after if config_is_after else before
    assert path.read_bytes() == expected
    assert not (Path(target["managed_root"]) / "strategy-intent.json").exists()
    assert runtime._current(target) == envelope["release_id"]
    saved = runtime._load_strategy_preferences(target)
    assert saved["revision"] == (1 if config_is_after else 0)


def test_rollback_rejects_preference_cycle_before_writing_any_service(tmp_path, monkeypatch):
    runtime, raw_target = _config(tmp_path, primary=True)
    live_path = Path(raw_target["configs"]["mihomo"])
    initial = yaml.safe_load(live_path.read_text())
    next(row for row in initial["proxy-groups"] if row["name"] == "Google")["proxies"] = ["AI", "REJECT"]
    live_path.write_text(yaml.safe_dump(initial, allow_unicode=True, sort_keys=False))
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, raw_target, primary=True)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    runtime.activate("test-target", envelope["release_id"])
    target = runtime._target("test-target")
    group = {"kind": "group", "name": "Google"}
    runtime._write_strategy_preferences(target, {"schema_version": 1, "revision": 1,
        "groups": {"AI": {"members": [group], "selected": group}}})
    runtime._refresh_active_marker(target, live_path)
    monkeypatch.setattr(runtime, "_all_mihomo_selections", lambda *_args: {"AI": "node-hk"})
    monkeypatch.setattr(runtime, "_mihomo_request", lambda *_args, **_kwargs: nullcontext(type("Response", (), {
        "read": lambda _self, _limit: b'{"providers":{}}'} )()))

    before = {name: Path(path).read_bytes() for name, path in target["configs"].items()}
    with pytest.raises(TargetRuntimeError, match="strategy group references contain a cycle"):
        runtime.rollback("test-target", OLD_ID)

    assert {name: Path(path).read_bytes() for name, path in target["configs"].items()} == before
    assert not (Path(target["managed_root"]) / "rollback-intent.json").exists()


def test_target_runtime_mihomo_second_release_replaces_managed_fake_ip_shards(tmp_path):
    runtime, target = _config(tmp_path, primary=True)
    live_path = Path(target["configs"]["mihomo"])
    initial = yaml.safe_load(live_path.read_text())
    initial["rule-providers"]["AuxDns"] = {
        "type": "file", "behavior": "classical", "format": "yaml", "path": "/user-managed/AuxDns.yaml"
    }
    initial["dns"]["fake-ip-filter"].insert(1, "RULE-SET,AuxDns,fake-ip")
    live_path.write_text(yaml.safe_dump(initial, allow_unicode=True, sort_keys=False))
    source1 = tmp_path / "release-one"
    first = _make_envelope(source1, target, primary=True, provider_name="FirstShard")
    runtime.stage("test-target", RID, first["release_id"], {"target_id": "test-target", "envelope": first}, bundle_bytes(source1, first))
    first_candidate = yaml.safe_load((Path(target["managed_root"]) / "releases" / first["release_id"] / "integration/mihomo.conf").read_text())
    assert first_candidate["rule-providers"]["AuxDns"]["path"] == "/user-managed/AuxDns.yaml"
    assert "RULE-SET,AuxDns,fake-ip" in first_candidate["dns"]["fake-ip-filter"]
    runtime.activate("test-target", first["release_id"])

    live = yaml.safe_load(live_path.read_text())
    live["dns"]["fake-ip-filter"].insert(0, "DOMAIN-SUFFIX,manual.example,fake-ip")
    # Keep this unrelated DNS rule in the current live baseline; the active marker
    # binds exact config hashes, so refresh that marker only within the test fixture.
    live_path.write_text(yaml.safe_dump(live, allow_unicode=True, sort_keys=False))
    marker_path = Path(target["managed_root"]) / "active-release.json"
    marker = json.loads(marker_path.read_text())
    marker["candidate_config_sha256"]["mihomo"] = hashlib.sha256(live_path.read_bytes()).hexdigest()
    marker_path.write_text(json.dumps(marker, sort_keys=True))

    source2 = tmp_path / "release-two"
    second = _make_envelope(source2, target, primary=True, provider_name="SecondShard")
    # A changed data snapshot receives a distinct immutable release identity.
    second["release_id"] = hashlib.sha256(b"second-candidate").hexdigest()
    runtime.stage("test-target", RID, second["release_id"], {"target_id": "test-target", "envelope": second}, bundle_bytes(source2, second))
    candidate_path = Path(target["managed_root"]) / "releases" / second["release_id"] / "integration/mihomo.conf"
    candidate = yaml.safe_load(candidate_path.read_text())
    filters = candidate["dns"]["fake-ip-filter"]
    direct_filter = [f"{'DOMAIN' if row['match_type'] == 'EXACT' else 'DOMAIN-SUFFIX'},{row['domain']},real-ip" for row in DIRECT]
    assert filters[:len(DIRECT)] == direct_filter
    assert "DOMAIN-SUFFIX,manual.example,fake-ip" in filters
    assert "RULE-SET,FirstShard,fake-ip" not in filters
    assert filters[-1] == "MATCH,real-ip"
    assert "RULE-SET,AuxDns,fake-ip" in filters
    assert filters.index("RULE-SET,SecondShard,fake-ip") < filters.index("RULE-SET,AuxDns,fake-ip")
    assert candidate["rule-providers"]["AuxDns"]["path"] == "/user-managed/AuxDns.yaml"
    assert candidate["dns"]["nameserver"] == ["tls://192.0.2.53"]
    assert "FirstShard" not in candidate["dns"]["fake-ip-filter"]
    direct_overlay = [rule for rule in candidate["rules"] if rule.startswith(("DOMAIN,", "DOMAIN-SUFFIX,")) and rule.endswith(",DIRECT")
                      and rule.split(",", 2)[1] in {row["domain"] for row in DIRECT}]
    assert len(direct_overlay) == len(DIRECT)


@pytest.mark.parametrize("filters", [
    ["RULE-SET,proxy-domains,fake-ip"],
    ["RULE-SET,proxy-domains,real-ip", "MATCH,real-ip"],
    ["RULE-SET,proxy-domains,fake-ip", "MATCH,real-ip", "DOMAIN,extra.example,real-ip"],
    ["RULE-SET,proxy-domains,fake-ip", "MATCH,real-ip", "MATCH,real-ip"],
])
def test_target_runtime_rejects_unexpected_fake_ip_filter_grammar(tmp_path, filters):
    runtime, _raw_target = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target, primary=True)
    base = yaml.safe_load(Path(target["configs"]["mihomo"]).read_text())
    base["dns"]["fake-ip-filter"] = filters
    with pytest.raises(TargetRuntimeError):
        runtime._mihomo_config(target, envelope["release_id"], yaml.safe_dump(base).encode(),
                               tmp_path / "scratch-release", envelope)



def test_runtime_refuses_interrupted_activation_as_current(tmp_path):
    runtime, target = _config(tmp_path)
    root = Path(target["managed_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "activation-intent.json").write_text(json.dumps({"release_id": "b" * 64}))
    with pytest.raises(TargetRuntimeError, match="interrupted activation"):
        runtime.dispatch("status", RID, "none", {"target_id": "test-target"})


def test_selector_restore_retries_api_startup_with_bounded_clock(tmp_path, monkeypatch):
    runtime, _ = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    clock = {"now": 0.0, "reads": 0, "puts": 0}
    def sleep(seconds):
        clock["now"] += seconds
    monkeypatch.setattr(runtime, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_sleep", sleep)
    def rows(_target):
        clock["reads"] += 1
        if clock["reads"] == 1:
            raise urllib.error.URLError("not listening yet")
        return {"AI": {"all": ["node-hk"], "now": "node-hk"}}
    class Accepted:
        status = 204
    def request(*_args, **kwargs):
        if kwargs.get("method") == "PUT":
            clock["puts"] += 1
        return nullcontext(Accepted())
    monkeypatch.setattr(runtime, "_mihomo_proxy_rows", rows)
    monkeypatch.setattr(runtime, "_mihomo_request", request)

    runtime._set_mihomo_selections(target, {"AI": "node-hk"})

    assert clock["reads"] >= 3
    assert clock["puts"] == 1
    assert clock["now"] < runtime._MIHOMO_READY_TIMEOUT


@pytest.mark.parametrize("status", [401, 403])
def test_selector_restore_does_not_retry_authentication_errors(tmp_path, monkeypatch, status):
    runtime, _ = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    clock = {"now": 0.0, "sleeps": 0}
    monkeypatch.setattr(runtime, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: clock.update(now=clock["now"] + seconds, sleeps=clock["sleeps"] + 1))
    monkeypatch.setattr(runtime, "_mihomo_proxy_rows", lambda _target: (_ for _ in ()).throw(
        urllib.error.HTTPError("http://localhost", status, "denied", {}, None)))

    with pytest.raises(TargetRuntimeError, match="authorization failed"):
        runtime._set_mihomo_selections(target, {"AI": "node-hk"})
    assert clock["sleeps"] == 0


def test_selector_restore_retries_transient_put_failure(tmp_path, monkeypatch):
    runtime, _ = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    clock = {"now": 0.0, "puts": 0}
    monkeypatch.setattr(runtime, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: clock.update(now=clock["now"] + seconds))
    monkeypatch.setattr(runtime, "_mihomo_proxy_rows", lambda _target: {"AI": {"all": ["node-hk"], "now": "node-hk"}})
    class Accepted:
        status = 204
    def request(*_args, **kwargs):
        if kwargs.get("method") == "PUT":
            clock["puts"] += 1
            if clock["puts"] == 1:
                raise urllib.error.URLError("controller restarting")
        return nullcontext(Accepted())
    monkeypatch.setattr(runtime, "_mihomo_request", request)

    runtime._set_mihomo_selections(target, {"AI": "node-hk"})

    assert clock["puts"] == 2
    assert clock["now"] == runtime._MIHOMO_RETRY_INTERVAL


def test_selector_restore_missing_member_fails_after_bound_without_put(tmp_path, monkeypatch):
    runtime, _ = _config(tmp_path, primary=True)
    target = runtime._target("test-target")
    clock = {"now": 0.0, "puts": 0}
    monkeypatch.setattr(runtime, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: clock.update(now=clock["now"] + seconds))
    monkeypatch.setattr(runtime, "_mihomo_proxy_rows", lambda _target: {"AI": {"all": ["other"], "now": "other"}})
    class Accepted:
        status = 204
    def request(*_args, **kwargs):
        if kwargs.get("method") == "PUT":
            clock["puts"] += 1
        return nullcontext(Accepted())
    monkeypatch.setattr(runtime, "_mihomo_request", request)

    with pytest.raises(TargetRuntimeError, match="remained unavailable"):
        runtime._set_mihomo_selections(target, {"AI": "missing"})
    assert clock["now"] == runtime._MIHOMO_READY_TIMEOUT
    assert clock["puts"] == 0


def test_recovery_status_requires_matching_transaction_evidence(tmp_path):
    runtime, raw_target = _config(tmp_path)
    source = tmp_path / "recovery-files"
    envelope = _make_envelope(source, raw_target)
    runtime.stage("test-target", RID, envelope["release_id"],
                  {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    target = runtime._target("test-target")
    root = Path(target["managed_root"])
    integration = runtime._integration(target, envelope["release_id"])
    (root / "releases" / OLD_ID).mkdir(parents=True)
    (root / "current").symlink_to(f"releases/{OLD_ID}")
    (root / "active-release.json").write_text(json.dumps({
        "release_id": OLD_ID, "candidate_config_sha256": integration["baseline_config_sha256"],
        "guard_sha256": integration["baseline_guard_sha256"],
    }))
    intent = {"release_id": envelope["release_id"], "previous_release_id": OLD_ID,
              "phase": "selector_restore_started"}
    (root / "activation-intent.json").write_text(json.dumps(intent))

    result = runtime.dispatch("recovery-status", RID, envelope["release_id"], {
        "target_id": "test-target", "previous_release_id": OLD_ID,
    })
    assert result == {"ok": True, "target_id": "test-target", "release_id": envelope["release_id"],
                      "previous_release_id": OLD_ID, "recovery_safe": True}
    assert json.loads((root / "activation-intent.json").read_text()) == intent
    before = {name: Path(path).read_bytes() for name, path in target["configs"].items()}
    with pytest.raises(TargetRuntimeError, match="IDs do not match"):
        runtime.rollback("test-target", "b" * 64)
    assert {name: Path(path).read_bytes() for name, path in target["configs"].items()} == before
    with pytest.raises(TargetRuntimeError, match="IDs do not match"):
        runtime.recovery_status("test-target", envelope["release_id"], None)

    (root / "current").unlink()
    (root / "current").symlink_to(f"releases/{'b' * 64}")
    with pytest.raises(TargetRuntimeError, match="pointer is outside transaction"):
        runtime.recovery_status("test-target", envelope["release_id"], OLD_ID)


@pytest.mark.parametrize("marker_value", [None, "[]", "{}", '{"release_id":"invalid"}'])
def test_runtime_fails_closed_when_managed_pointer_lacks_valid_marker(tmp_path, marker_value):
    runtime, target = _config(tmp_path)
    root = Path(target["managed_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "releases").mkdir()
    (root / "current").symlink_to(f"releases/{OLD_ID}")
    if marker_value is not None:
        (root / "active-release.json").write_text(marker_value)
    with pytest.raises(TargetRuntimeError, match="marker|structure|release ID"):
        runtime.dispatch("status", RID, "none", {"target_id": "test-target"})


def test_runtime_rejects_symlink_and_unlisted_archive_member(tmp_path):
    runtime, target = _config(tmp_path)
    source = tmp_path / "release-files"
    envelope = _make_envelope(source, target)
    bad = io.BytesIO()
    with tarfile.open(fileobj=bad, mode="w:gz") as tar:
        data = json.dumps(envelope).encode()
        info = tarfile.TarInfo("envelope.json"); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        data = b"example.net\n"
        info = tarfile.TarInfo("files/../outside"); info.size = len(data); tar.addfile(info, io.BytesIO(data))
    with pytest.raises(TargetRuntimeError, match="unsafe or unexpected"):
        runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bad.getvalue())


def test_real_smartdns_only_manifest_envelope_is_accepted_by_runtime(tmp_path):
    from test_target_v4 import _verified_release
    from dns_rule_builder.target_v4 import load_target_specs, target_manifest

    project_root = Path(__file__).resolve().parents[1]
    release = _verified_release(tmp_path / "builder")
    _, specs = load_target_specs(project_root / "config/targets-v4.yaml")
    spec = next(item for item in specs if item.role == "backup")
    envelope = target_manifest(release, spec, allow_preparation=True)
    runtime, _target = _config(tmp_path / "target", target_id=spec.target_id)
    source = tmp_path / "release-files"
    for relative in envelope["files"]:
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((release / relative).read_bytes())
    result = runtime.stage(spec.target_id, RID, envelope["release_id"], {"target_id": spec.target_id, "envelope": envelope}, bundle_bytes(source, envelope))
    assert result["release_id"] == envelope["release_id"]
    assert runtime.validate(spec.target_id, envelope["release_id"])["evidence"]["result"] == "ok"


def test_vm104_isolated_validation_namespace_restores_only_smartdns_bind_address():
    project_root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((project_root / "config/target-agent-vm104.example.yaml").read_text())
    commands = config["targets"]["vm104-main"]["validate_commands"]
    for argv in commands.values():
        assert argv[:3] == ["unshare", "--net", "--"]
        assert "10.255.50.2/32" in argv[5]
        assert "'link','set','lo','up'" in argv[5]
        code_index = argv.index("-c")
        code, child_argv = argv[code_index + 1], argv[code_index + 2:]
        assert child_argv[0] != "--"
        capture = "import os,subprocess,sys,json; subprocess.run=lambda *a,**k: None; os.execvp=lambda file,args: print(json.dumps([file,args])); " + code
        result = subprocess.run([sys.executable, "-c", capture, *child_argv], check=True, capture_output=True, text=True)
        exec_target, exec_argv = json.loads(result.stdout)
        assert exec_target == child_argv[0]
        assert exec_argv == child_argv
    assert commands["smartdns"][commands["smartdns"].index("-c") + 2] == "env"
    assert commands["mihomo"][commands["mihomo"].index("-c") + 2] == "runuser"
    smartdns = " ".join(commands["smartdns"])
    assert "LD_LIBRARY_PATH=/usr/local/lib/smartdns" in smartdns
    assert "-p -" in smartdns


def test_target_validation_binds_guard_files_and_accepts_allowlisted_absolute_legacy_pointer(tmp_path):
    runtime, target = _config(tmp_path)
    guard = tmp_path / "compose.yaml"
    guard.write_text("services: {smartdns: {image: pinned}}\n")
    target["guard_files"] = {"compose_config": str(guard)}
    legacy = Path(target["legacy_link"])
    legacy.unlink()
    legacy.symlink_to(str(legacy.parent / "versions" / OLD_ID))
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(yaml.safe_dump({"schema_version": 1, "default_target_id": "test-target", "targets": {"test-target": target}}, sort_keys=False))
    runtime = TargetRuntime(config_path)
    assert runtime.dispatch("status", RID, "none", {"target_id": "test-target"})["current_release_id"] == OLD_ID
    source = tmp_path / "guard-release-files"
    envelope = _make_envelope(source, target)
    runtime.stage("test-target", RID, envelope["release_id"], {"target_id": "test-target", "envelope": envelope}, bundle_bytes(source, envelope))
    guard.write_text("services: {smartdns: {image: changed}}\n")
    with pytest.raises(TargetRuntimeError, match="local target contract changed"):
        runtime.validate("test-target", envelope["release_id"])
