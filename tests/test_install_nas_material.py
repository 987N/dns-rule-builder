from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/install-nas-production.sh"
IMAGE = "ghcr.io/987n/dns-rule-builder@sha256:" + "a" * 64
HELPER_PATH = REPO / "scripts/prepare_nas_backup.py"
COMPOSE_INSTALL = REPO.parents[1] / "DockerWorkspace/compose/stacks/dns-rule-builder/install.sh"
SPEC = importlib.util.spec_from_file_location("prepare_nas_backup", HELPER_PATH)
assert SPEC and SPEC.loader
backup_helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_helper)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[dict[str, str], Path]:
    pack = tmp_path / "pack"
    files = {
        "image-digest.txt": IMAGE + "\n",
        "target-agent-nas.yaml": "schema_version: 1\ndefault_target_id: nas-backup\ntargets:\n  nas-backup: {}\n",
        "builder/scripts/install-target-agent.sh": "#!/bin/sh\nexit 0\n",
        "builder/scripts/prepare_nas_backup.py": (REPO / "scripts/prepare_nas_backup.py").read_text(encoding="utf-8"),
        "builder/src/dns_rule_builder/__init__.py": "\n",
        "builder/src/dns_rule_builder/target_agent.py": "\n",
        "builder/src/dns_rule_builder/target_runtime.py": "\n",
        "compose/stacks/dns-rule-builder/README.md": "stack readme\n",
        "compose/stacks/dns-rule-builder/compose.yaml": "services: {}\n",
        "compose/stacks/dns-rule-builder/install.sh": "#!/bin/sh\nexit 0\n",
        "compose/config/dns-rule-builder/targets-v4.yaml": "schema_version: 4\ndeployment_enabled: false\ntargets: {}\n",
    }
    for rel, content in files.items():
        _write(pack / rel, content)
    lines = [f"{hashlib.sha256((pack / rel).read_bytes()).hexdigest()}  {rel}" for rel in sorted(files)]
    manifest = "\n".join(lines) + "\n"
    _write(pack / "SHA256SUMS", manifest)
    lock = hashlib.sha256((pack / "SHA256SUMS").read_bytes()).hexdigest()

    root = tmp_path / "nas"
    compose = root / "vol1/1000/DockerData/_compose"
    secret_dir = compose / "secrets/local/dns-rule-builder"
    home = root / "home/987N"
    for name in ("github_token", "admin_password", "session_secret", "known_hosts"):
        _write(secret_dir / name, f"fixture-{name}\n")
        (secret_dir / name).chmod(0o600)
    key_path = secret_dir / "nas_target_ed25519"
    vm_key_path = secret_dir / "vm104_target_ed25519"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)], check=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(vm_key_path)], check=True)
    pub = subprocess.check_output(["ssh-keygen", "-y", "-f", str(key_path)], text=True).split()
    _write(secret_dir / "known_hosts", f"10.77.0.4 {pub[0]} {pub[1]}\n10.255.50.2 {pub[0]} {pub[1]}\n")
    (secret_dir / "known_hosts").chmod(0o600)
    _write(compose / "secrets/local/homegateway-review/web_password", "existing-admin-password\n")
    _write(root / "vol1/1000/DockerData/_compose/stacks/smartdns-backup/compose.yaml", "smartdns existing stack\n")
    _write(root / "vol1/1000/DockerData/smartdns-backup/rules/versions/release/rules.txt", "existing rules\n")
    (root / "vol1/1000/DockerData/smartdns-backup/rules/current").symlink_to("versions/release")
    _write(root / "vol1/1000/DockerData/homegateway-review/review.json", "old review state\n")
    _write(root / "vol1/1000/DockerData/homegateway-rules-controller/controller.json", "old controller state\n")
    _write(compose / "config/network/caddy/Caddyfile", "rules.lclwf.date { reverse_proxy old }\n")
    home.mkdir(parents=True)
    sshdir = home / ".ssh"
    _write(sshdir / "authorized_keys", "ssh-ed25519 " + pub[1] + " old-key\n")

    fakebin = tmp_path / "bin"
    _write(fakebin / "getent", f"#!/bin/sh\nprintf '%s\\n' '987N:x:{os.getuid()}:{os.getgid()}::{home}:/bin/sh'\n")
    (fakebin / "getent").chmod(0o755)
    env = {
        **os.environ,
        "DNS_RULE_BUILDER_PACK_ROOT": str(pack),
        "DNS_RULE_BUILDER_PACK_SHA256": lock,
        "DNS_RULE_BUILDER_IMAGE": IMAGE,
        "DNS_RULE_BUILDER_NAS_ROOT": str(root),
        "PATH": str(fakebin) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    return env, root


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(SCRIPT)], env=env, text=True, capture_output=True)


def test_nas_install_script_dry_run_only_plans_and_preserves_existing_files(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    target = root / "vol1/1000/DockerData/_compose/config/dns-rule-builder/targets-v4.yaml"
    authorized = root / "home/987N/.ssh/authorized_keys"
    original_authorized = authorized.read_bytes()

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "Dry run only" in result.stdout
    assert "No files, metadata, targets, services" in result.stdout
    assert "existing-admin-password" not in result.stdout + result.stderr
    assert not target.exists()
    assert not (root / "vol1/1000/DockerData/dns-rule-builder").exists()
    assert authorized.read_bytes() == original_authorized
    assert not (root / "etc/dns-rule-target-v4.yaml").exists()


def test_missing_pat_fails_before_any_installation(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    (root / "vol1/1000/DockerData/_compose/secrets/local/dns-rule-builder/github_token").unlink()

    result = _run(env)

    assert result.returncode != 0
    assert "github_token" in result.stderr
    assert not (root / "vol1/1000/DockerData/dns-rule-builder").exists()
    assert not (root / "etc/dns-rule-target-v4.yaml").exists()


def test_short_builder_admin_password_fails_without_disclosing_value(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    secret_path = root / "vol1/1000/DockerData/_compose/secrets/local/dns-rule-builder/admin_password"
    secret_path.write_text("too-short-value\n", encoding="utf-8")
    secret_path.chmod(0o600)

    result = _run(env)

    assert result.returncode != 0
    assert "at least 16" in result.stderr
    assert "too-short-value" not in result.stdout + result.stderr
    assert not (root / "vol1/1000/DockerData/dns-rule-builder").exists()
    old_password = root / "vol1/1000/DockerData/_compose/secrets/local/homegateway-review/web_password"
    assert old_password.read_text(encoding="utf-8") == "existing-admin-password\n"


def test_builder_admin_password_does_not_depend_on_legacy_review_password(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    (root / "vol1/1000/DockerData/_compose/secrets/local/homegateway-review/web_password").unlink()

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "Dry run only" in result.stdout


def test_compose_install_checks_secret_file_and_mode_without_read_permission_check() -> None:
    source = COMPOSE_INSTALL.read_text(encoding="utf-8")

    assert '[ ! -f "$secret_path" ]' in source
    assert '[ -L "$secret_path" ]' in source
    assert 'stat -c \'%a\' "$secret_path"' in source
    assert '[ "$secret_mode" != 600 ]' in source
    assert '[ ! -r "$stack_dir/$relative_path" ]' not in source


def test_different_existing_target_config_is_never_overwritten(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    target = root / "vol1/1000/DockerData/_compose/config/dns-rule-builder/targets-v4.yaml"
    _write(target, "user data; do not replace\n")

    result = _run(env)

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert target.read_text(encoding="utf-8") == "user data; do not replace\n"


def test_changed_pack_lock_is_rejected(tmp_path: Path) -> None:
    env, _ = _fixture(tmp_path)
    env["DNS_RULE_BUILDER_PACK_SHA256"] = "b" * 64

    result = _run(env)

    assert result.returncode != 0
    assert "differs from supplied lock" in result.stderr


def test_symlinked_secret_path_is_rejected_in_dry_run(tmp_path: Path) -> None:
    env, root = _fixture(tmp_path)
    secret_dir = root / "vol1/1000/DockerData/_compose/secrets/local/dns-rule-builder"
    session = secret_dir / "session_secret"
    session.unlink()
    session.symlink_to(secret_dir / "github_token")

    result = _run(env)

    assert result.returncode != 0
    assert "symlink path component" in result.stderr
    assert not (root / "vol1/1000/DockerData/dns-rule-builder").exists()


def _required_backup_sources(root: Path) -> None:
    for source in backup_helper.REQUIRED_SOURCES:
        path = root / source.lstrip("/")
        if source.endswith("/Caddyfile"):
            _write(path, "caddy route\n")
        else:
            path.mkdir(parents=True, exist_ok=True)


def test_scoped_backup_helper_preserves_links_and_sqlite_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "nas-source"
    _required_backup_sources(root)
    rules = root / "vol1/1000/DockerData/smartdns-backup/rules"
    _write(rules / "versions/release/domains.txt", "rules\n")
    (rules / "current").symlink_to("versions/release")
    db = root / "vol1/1000/DockerData/homegateway-rules-controller/controller.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(db)
    source_db.execute("PRAGMA journal_mode=WAL")
    source_db.execute("CREATE TABLE state (value TEXT)")
    source_db.execute("INSERT INTO state VALUES ('committed-in-wal')")
    source_db.commit()
    db.chmod(0o640)
    assert Path(str(db) + "-wal").exists()

    backup_parent = tmp_path / "protected-backups"
    backup_parent.mkdir(mode=0o700)
    backup = backup_parent / "snapshot-1"
    backup.mkdir(mode=0o700)
    receipt_file = backup_helper.create_backup(root, backup, [])

    copied_rules = backup / "files/vol1/1000/DockerData/smartdns-backup/rules"
    assert (copied_rules / "current").is_symlink()
    assert os.readlink(copied_rules / "current") == "versions/release"
    assert (copied_rules / "versions/release/domains.txt").stat().st_mode & 0o777 == (rules / "versions/release/domains.txt").stat().st_mode & 0o777
    copied_db = backup / "files/vol1/1000/DockerData/homegateway-rules-controller/controller.sqlite3"
    assert not Path(str(copied_db) + "-wal").exists()
    assert not Path(str(copied_db) + "-shm").exists()
    assert copied_db.stat().st_uid == db.stat().st_uid
    assert copied_db.stat().st_gid == db.stat().st_gid
    assert copied_db.stat().st_mode & 0o777 == db.stat().st_mode & 0o777
    with sqlite3.connect(copied_db) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT value FROM state").fetchone() == ("committed-in-wal",)
    assert db.exists() and Path(str(db) + "-wal").exists()
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["counts"]["symlinks"] >= 1
    assert receipt["counts"]["regular_files"] >= 2
    assert "committed-in-wal" not in receipt_file.read_text(encoding="utf-8")
    assert any(item.get("path", "").endswith("/current") and item.get("target") == "versions/release" for item in receipt["entries"])
    assert all({"uid", "gid", "mode"}.issubset(item) for item in receipt["entries"])
    source_db.close()


def test_scoped_backup_helper_requires_all_production_sources_before_copy(tmp_path: Path) -> None:
    root = tmp_path / "nas-source"
    _required_backup_sources(root)
    missing = root / backup_helper.REQUIRED_SOURCES[3].lstrip("/")
    missing.rmdir()
    backup_parent = tmp_path / "protected-backups"
    backup_parent.mkdir(mode=0o700)
    backup = backup_parent / "snapshot-1"
    backup.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="required backup source"):
        backup_helper.create_backup(root, backup, [])

    assert not (backup / "files").exists()


def test_scoped_backup_helper_rejects_unlisted_optional_paths(tmp_path: Path) -> None:
    root = tmp_path / "nas-source"
    _required_backup_sources(root)
    backup = tmp_path / "protected-backup"
    backup.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="outside the allowlist"):
        backup_helper.create_backup(root, backup, ["/etc/shadow"])

    assert not (backup / "files").exists()
