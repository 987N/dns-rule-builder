"""Strict, bounded SSH transport for the independent schema-v4 target agent."""

from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
import uuid
from pathlib import Path
from typing import Any

from .target_v4 import TargetSpec, TargetV4Error

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_REPLY_BYTES = 64 * 1024
MAX_STRATEGY_REPLY_BYTES = 1024 * 1024
AGENT = "dns-rule-target-v4"


def _json_line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def bundle_bytes(source_dir: Path, envelope: dict[str, Any]) -> bytes:
    """Build a bounded tar containing only envelope-listed, verified files."""
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("envelope.json")
        payload = _json_line(envelope)
        info.size, info.mode, info.mtime = len(payload), 0o600, 0
        tar.addfile(info, io.BytesIO(payload))
        for relative, expected in sorted(envelope["files"].items()):
            path = source_dir / relative
            if path.is_symlink() or not path.is_file():
                raise TargetV4Error(f"target bundle file is not regular: {relative}")
            data = path.read_bytes()
            import hashlib
            if hashlib.sha256(data).hexdigest() != expected:
                raise TargetV4Error(f"target bundle hash mismatch: {relative}")
            info = tarfile.TarInfo(f"files/{relative}")
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            tar.addfile(info, io.BytesIO(data))
    result = archive.getvalue()
    if len(result) > MAX_BUNDLE_BYTES:
        raise TargetV4Error("target bundle exceeds transport size limit")
    return result


class SSHTransport:
    """TargetHandler implementation. No shell, user-supplied command, or SSH agent."""

    def __init__(self, spec: TargetSpec, *, executable: str = "ssh"):
        if not all((spec.ssh_host, spec.ssh_user, spec.ssh_identity_file, spec.ssh_known_hosts_file)):
            raise TargetV4Error(f"{spec.target_id}: SSH configuration is incomplete")
        self.spec = spec
        self.executable = executable

    def _call(self, operation: str, request_id: str, release_id: str, message: dict[str, Any], payload: bytes = b"") -> dict[str, Any]:
        if operation not in {"stage", "validate", "activate", "rollback", "status", "strategies", "strategy-select", "strategy-preview", "strategy-apply"}:
            raise TargetV4Error("unsupported target operation")
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except ValueError as exc:
            raise TargetV4Error("invalid request UUID") from exc
        if not (release_id == "none" or re.fullmatch(r"[0-9a-f]{64}", release_id)):
            raise TargetV4Error("invalid target operation identifier")
        remote = f"{AGENT} {operation} {request_id} {release_id}"
        argv = [self.executable, "-T", "-p", str(self.spec.ssh_port), "-i", self.spec.ssh_identity_file,
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={self.spec.ssh_known_hosts_file}", "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes", "-o", f"ConnectTimeout={self.spec.ssh_connect_timeout_seconds}",
                "-o", f"ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                f"{self.spec.ssh_user}@{self.spec.ssh_host}", remote]
        wire = _json_line(message) + payload
        if len(wire) > MAX_BUNDLE_BYTES + MAX_REPLY_BYTES:
            raise TargetV4Error("SSH request exceeds transport size limit")
        try:
            result = subprocess.run(argv, input=wire, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=self.spec.ssh_operation_timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TargetV4Error(f"{self.spec.target_id}: SSH {operation} failed ({type(exc).__name__})") from exc
        response_limit = MAX_STRATEGY_REPLY_BYTES if operation == "strategies" else MAX_REPLY_BYTES
        if len(result.stdout) > response_limit:
            raise TargetV4Error("target response exceeds size limit")
        if result.returncode != 0:
            raise TargetV4Error(f"{self.spec.target_id}: SSH {operation} failed (exit {result.returncode})")
        try:
            response = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TargetV4Error("target returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise TargetV4Error("target operation was rejected")
        return response

    def stage(self, request_id: str, envelope: dict[str, Any], source_dir: Path) -> None:
        payload = bundle_bytes(source_dir, envelope)
        self._call("stage", request_id, envelope["release_id"], {"target_id": self.spec.target_id, "envelope": envelope}, payload)

    def validate(self, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        response = self._call("validate", request_id, envelope["release_id"], {"target_id": self.spec.target_id, "envelope": envelope})
        evidence = response.get("evidence")
        if not isinstance(evidence, dict):
            raise TargetV4Error("target validation response has no evidence")
        return evidence

    def activate(self, request_id: str, envelope: dict[str, Any]) -> None:
        self._call("activate", request_id, envelope["release_id"], {"target_id": self.spec.target_id, "envelope": envelope})

    def rollback(self, request_id: str, previous_release: str | None) -> None:
        self._call("rollback", request_id, previous_release or "none", {"target_id": self.spec.target_id, "previous_release_id": previous_release})

    def current_release(self) -> str | None:
        response = self._call("status", str(uuid.uuid4()), "none", {"target_id": self.spec.target_id})
        current = response.get("current_release_id")
        if current is not None and not re.fullmatch(r"[0-9a-f]{64}", current):
            raise TargetV4Error("target status returned an invalid release ID")
        return current

    def strategies(self) -> dict[str, Any]:
        return self._call("strategies", str(uuid.uuid4()), "none", {"target_id": self.spec.target_id})

    def strategy_select(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any], preview_sha256: str) -> dict[str, Any]:
        return self._call("strategy-select", str(uuid.uuid4()), "none", {
            "target_id": self.spec.target_id, "group": group, "revision": revision,
            "members": members, "selected": selected, "preview_sha256": preview_sha256,
        })

    def strategy_preview(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any]) -> dict[str, Any]:
        return self._call("strategy-preview", str(uuid.uuid4()), "none", {
            "target_id": self.spec.target_id, "group": group, "revision": revision,
            "members": members, "selected": selected,
        })

    def strategy_apply(self, group: str, revision: int, members: list[dict[str, Any]], selected: dict[str, Any], preview_sha256: str) -> dict[str, Any]:
        return self._call("strategy-apply", str(uuid.uuid4()), "none", {
            "target_id": self.spec.target_id, "group": group, "revision": revision,
            "members": members, "selected": selected, "preview_sha256": preview_sha256,
        })
