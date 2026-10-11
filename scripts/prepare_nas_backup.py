#!/usr/bin/env python3
"""Create a private, integrity-described pre-install snapshot of scoped NAS state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import stat
import tempfile
import time


REQUIRED_SOURCES = (
    "/vol1/1000/DockerData/_compose/stacks/smartdns-backup",
    "/vol1/1000/DockerData/smartdns-backup/rules",
    "/vol1/1000/DockerData/homegateway-review",
    "/vol1/1000/DockerData/homegateway-rules-controller",
    "/vol1/1000/DockerData/_compose/config/network/caddy/Caddyfile",
)
SQLITE_HEADER = b"SQLite format 3\x00"
OPTIONAL_EXACT = {
    "/etc/dns-rule-target-v4.yaml",
    "/usr/local/libexec/smartdns-backup-target",
    "/usr/local/lib/dns-rule-target-v4",
    "/usr/local/sbin/dns-rule-target-v4",
    "/etc/sudoers.d/dns-rule-target-v4",
    "/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/README.md",
    "/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/compose.yaml",
    "/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/install.sh",
    "/vol1/1000/DockerData/_compose/config/dns-rule-builder/targets-v4.yaml",
    "/etc/dns-rule-builder-preparation-receipt.json",
}


def _copy_path(source: pathlib.Path, destination: pathlib.Path) -> None:
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source))
        st = source.lstat()
        os.lchown(destination, st.st_uid, st.st_gid)
        os.utime(destination, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
    elif source.is_dir():
        st = source.stat()
        destination.mkdir(parents=True, mode=stat.S_IMODE(st.st_mode))
        for child in source.iterdir():
            _copy_path(child, destination / child.name)
        os.chown(destination, st.st_uid, st.st_gid)
        os.chmod(destination, stat.S_IMODE(st.st_mode))
        os.utime(destination, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        st = source.stat()
        os.chown(destination, st.st_uid, st.st_gid)
        os.chmod(destination, stat.S_IMODE(st.st_mode))
        os.utime(destination, ns=(st.st_atime_ns, st.st_mtime_ns))
    else:
        raise ValueError(f"unsupported backup source type: {source}")


def _make_sqlite_copies(nas_root: pathlib.Path, files_root: pathlib.Path) -> None:
    for copied in sorted(files_root.rglob("*")):
        if copied.is_symlink() or not copied.is_file():
            continue
        with copied.open("rb") as stream:
            if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                continue
        relative = copied.relative_to(files_root)
        source = nas_root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"SQLite source is missing or unsafe: {relative}")
        with source.open("rb") as stream:
            if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise ValueError(f"SQLite source header changed during backup: {relative}")

        source_stat = source.stat()
        fd, temp_name = tempfile.mkstemp(prefix=".sqlite-backup-", dir=copied.parent)
        os.close(fd)
        try:
            uri = source.as_uri() + "?mode=ro"
            read_only = sqlite3.connect(uri, uri=True, timeout=30)
            target = sqlite3.connect(temp_name)
            try:
                read_only.backup(target)
                integrity = target.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise ValueError(f"SQLite integrity_check failed: {relative}")
            finally:
                target.close()
                read_only.close()
            os.replace(temp_name, copied)
            os.chown(copied, source_stat.st_uid, source_stat.st_gid)
            os.chmod(copied, stat.S_IMODE(source_stat.st_mode))
            os.utime(copied, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

        # These sidecars belong only to the byte-for-byte cp -a copy. The source
        # remains untouched; the sqlite3.backup result is self-contained.
        for suffix in ("-wal", "-shm"):
            sidecar = pathlib.Path(str(copied) + suffix)
            if sidecar.is_symlink() or sidecar.is_file():
                sidecar.unlink()
            elif sidecar.exists():
                raise ValueError(f"unexpected SQLite sidecar type: {relative}{suffix}")


def _receipt(files_root: pathlib.Path, sources: list[str]) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    counts = {"regular_files": 0, "symlinks": 0, "directories": 0, "bytes": 0}
    for path in sorted(files_root.rglob("*")):
        relative = path.relative_to(files_root).as_posix()
        if path.is_symlink():
            st = path.lstat()
            entries.append({"path": relative, "type": "symlink", "target": os.readlink(path), "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)})
            counts["symlinks"] += 1
        elif path.is_dir():
            st = path.stat()
            entries.append({"path": relative, "type": "directory", "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)})
            counts["directories"] += 1
        elif path.is_file():
            st = path.stat()
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
            entries.append({"path": relative, "type": "file", "size": size, "sha256": digest.hexdigest(), "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)})
            counts["regular_files"] += 1
            counts["bytes"] += size
        else:
            raise ValueError(f"unsupported file type in backup: {relative}")
    return {
        "schema_version": 1,
        "created_utc_epoch": int(time.time()),
        "sources": sources,
        "counts": counts,
        "entries": entries,
    }


def create_backup(nas_root: pathlib.Path, backup_dir: pathlib.Path, optional_sources: list[str]) -> pathlib.Path:
    nas_root = nas_root.resolve(strict=True)
    if backup_dir.is_symlink() or not backup_dir.is_dir() or any(backup_dir.iterdir()):
        raise ValueError("backup directory must be an existing empty directory")
    all_sources = list(REQUIRED_SOURCES)
    for source in optional_sources:
        valid_authorized_keys = source.startswith("/") and source.endswith("/.ssh/authorized_keys") and ".." not in pathlib.PurePosixPath(source).parts
        if source not in OPTIONAL_EXACT and not valid_authorized_keys:
            raise ValueError(f"optional backup source is outside the allowlist: {source}")
        optional_path = nas_root / source.lstrip("/")
        if source.startswith("/") and (optional_path.exists() or optional_path.is_symlink()):
            all_sources.append(source)

    # Validate every mandatory source before creating a partial snapshot.
    for source in REQUIRED_SOURCES:
        path = nas_root / source.lstrip("/")
        if path.is_symlink() or not path.exists() or not os.access(path, os.R_OK):
            raise ValueError(f"required backup source missing, unreadable, or unsafe: {source}")
        if source.endswith("/Caddyfile"):
            if not path.is_file():
                raise ValueError(f"required backup file is not regular: {source}")
        elif not path.is_dir():
            raise ValueError(f"required backup source is not a directory: {source}")

    files_root = backup_dir / "files"
    files_root.mkdir(parents=True, mode=0o700)
    for source in all_sources:
        path = nas_root / source.lstrip("/")
        if path.is_symlink():
            # Optional installed files may be symlinks; preserve their literal target.
            pass
        elif not path.exists():
            continue
        destination = files_root / source.lstrip("/")
        _copy_path(path, destination)

    _make_sqlite_copies(nas_root, files_root)
    receipt = _receipt(files_root, all_sources)
    receipt_path = backup_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return receipt_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nas-root", type=pathlib.Path, default=pathlib.Path("/"))
    parser.add_argument("--backup-dir", type=pathlib.Path, required=True)
    parser.add_argument("--optional-source", action="append", default=[])
    args = parser.parse_args()
    try:
        receipt = create_backup(args.nas_root, args.backup_dir, args.optional_source)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
