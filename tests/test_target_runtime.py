from __future__ import annotations

import grp
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
    state = {"requests": 0, "unauthorized": 0}
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
            groups = {name: {"now": f"node-{index}", "all": [f"node-{index}"]} for index, name in enumerate(REGIONAL_GROUPS)}
            groups[REGIONAL_GROUPS[0]] = {"now": "node-hk", "all": ["node-hk"]}
            groups["AI"] = {"now": "node-hk", "all": ["node-hk", *REGIONAL_GROUPS]}
            payload = json.dumps({"proxies": groups}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_PUT(self):
            if not self._authorized():
                return
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
            old_file = legacy.parent / "versions" / OLD_ID / "mihomo/providers" / f"{name}.yaml"
            old_file.write_text("payload: []\n")
            providers[name] = {"type": "file", "behavior": "classical", "format": "yaml", "path": str(legacy / "mihomo/providers" / f"{name}.yaml")}
        live_mihomo.write_text(yaml.safe_dump({
            "external-controller": f"{controller_host}:{controller.server_port}",
            "secret": "secret-fixture",
            "mixed-port": 7890,
            "proxies": [{"name": "node-hk", "type": "ss", "server": "example.test", "port": 443, "cipher": "x", "password": "secret-fixture"}],
            "proxy-groups": [*groups, {"name": "AI", "type": "select", "proxies": ["node-hk"]}, {"name": "Google", "type": "select", "proxies": ["香港自动策略", "REJECT"]}],
            "rule-providers": providers,
            "rules": [*(f"RULE-SET,{name},DIRECT" for name in direct_names), "IP-CIDR,192.0.2.0/24,DIRECT", "DOMAIN,idmsa.apple.com,DIRECT", "RULE-SET,OldAI,AI", "MATCH,兜底后备策略"],
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
    assert result["AI"] == REGIONAL_GROUPS[0]
    assert runtime.test_controller.request_state == {"requests": 1, "unauthorized": 0}


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
        assert runtime._query_mihomo_selections(target, {"AI"}, {"AI": {}})["AI"] == REGIONAL_GROUPS[0]
        assert server.request_state == {"requests": 1, "unauthorized": 0}
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


def _make_envelope(source: Path, target: dict, *, primary=False):
    files = {"smartdns/proxy-domains.txt": b"example.net\n"}
    if primary:
        files.update({
            "mihomo/rule-providers.yaml": b"rule-providers:\n  Demo:\n    type: file\n    behavior: classical\n    format: yaml\n    path: ./providers/Demo.yaml\n",
            "mihomo/rules.yaml": b"rules:\n  - RULE-SET,Demo,AI\n",
            "mihomo/providers/Demo.yaml": b"payload:\n  - DOMAIN-SUFFIX,example.net\n",
        })
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    provider_map = [{"name": "Demo", "policy_group": "AI", "categories": ["demo"], "kind": "policy-run", "member_indices": [0], "path": "mihomo/providers/Demo.yaml"}] if primary else []
    exceptions = sorted(DIRECT, key=lambda row: (row["match_type"], row["domain"]))
    envelope = {
        "schema_version": 4, "target_id": "test-target", "role": target["role"], "release_id": hashlib.sha256(b"candidate").hexdigest(),
        "manifest_sha256": hashlib.sha256(b"manifest").hexdigest(), "services": target["services"],
        "files": {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
        "mihomo_rule_order": ["Demo"] if primary else [], "provider_map": provider_map,
        "provider_policy_map": {"Demo": "AI"} if primary else {}, "regional_groups": list(REGIONAL_GROUPS) if primary else [],
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
    assert rules[-1] == "MATCH,兜底后备策略"
    assert "IP-CIDR,192.0.2.0/24,DIRECT" in rules and "DOMAIN,idmsa.apple.com,DIRECT" in rules
    assert candidate["proxies"] == yaml.safe_load(Path(target["configs"]["mihomo"]).read_text())["proxies"]
    assert candidate["proxy-groups"][[x["name"] for x in candidate["proxy-groups"]].index("AI")]["proxies"][0] == "香港自动策略"
    runtime.test_controller.shutdown()
    runtime.test_controller.server_close()
    for name in ("LAN", "Direct", "ChinaDownloadCDN", "iCloudChina", "ApplePushNotificationService", "AppleSoftwareUpdates", "ESET_China"):
        assert candidate["rule-providers"][name]["path"].endswith(f"/files/mihomo/providers/{name}.yaml")
        assert (Path(target["managed_root"]) / "releases" / envelope["release_id"] / "files/mihomo/providers" / f"{name}.yaml").is_file()
    evidence = runtime.validate("test-target", envelope["release_id"])["evidence"]
    assert evidence["regional_groups_sha256"]
    assert evidence["direct_exceptions_sha256"] == envelope["direct_exceptions_sha256"]


def test_runtime_refuses_interrupted_activation_as_current(tmp_path):
    runtime, target = _config(tmp_path)
    root = Path(target["managed_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "activation-intent.json").write_text(json.dumps({"release_id": "b" * 64}))
    with pytest.raises(TargetRuntimeError, match="interrupted activation"):
        runtime.dispatch("status", RID, "none", {"target_id": "test-target"})


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
