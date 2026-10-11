from __future__ import annotations

import json
import subprocess
import uuid

from dns_rule_builder.ssh_transport import SSHTransport
from dns_rule_builder.target_v4 import TargetSpec


def _spec():
    return TargetSpec("nas-backup", "backup", ("smartdns",), "smartdns/proxy-domains.txt",
                      ssh_host="nas.example.test", ssh_user="987N",
                      ssh_identity_file="/run/secrets/nas", ssh_known_hosts_file="/run/secrets/known_hosts")


def test_ssh_transport_uses_strict_host_key_and_fixed_remote_command(monkeypatch):
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, b'{"ok":true,"current_release_id":null}\n', b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SSHTransport(_spec()).current_release() is None
    argv = observed["argv"]
    assert "StrictHostKeyChecking=yes" in argv
    assert "IdentitiesOnly=yes" in argv
    assert "ForwardAgent=no" in argv
    assert "UserKnownHostsFile=/run/secrets/known_hosts" in argv
    assert argv[-1] == "dns-rule-target-v4 status " + argv[-1].split()[-2] + " none"
    assert observed["kwargs"]["timeout"] == 420
    assert observed["kwargs"]["input"].endswith(b"\n")


def test_ssh_transport_rejects_invalid_operation_id_without_calling_ssh(monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("must not invoke ssh")

    monkeypatch.setattr(subprocess, "run", fail_run)
    transport = SSHTransport(_spec())
    try:
        transport._call("status", "bad", "none", {})
    except Exception as exc:
        assert "UUID" in str(exc)
    else:
        raise AssertionError("invalid operation identifier accepted")
