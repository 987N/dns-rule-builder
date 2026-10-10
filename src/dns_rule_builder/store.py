"""Local operational index; the GitHub YAML remains the published rule source."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases (
                    release_id TEXT PRIMARY KEY, data_commit TEXT NOT NULL, code_commit TEXT NOT NULL,
                    path TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key: str, default=None):
        with self._lock, self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        with self._lock, self.connect() as db:
            db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def create_task(self, kind: str, payload: dict) -> str:
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dns-rule-builder:{kind}:daily:{payload['schedule_date']}")) if payload.get("daily") and payload.get("schedule_date") else str(uuid.uuid4())
        now = utc_now()
        with self._lock, self.connect() as db:
            db.execute("INSERT OR IGNORE INTO tasks(id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)", (task_id, kind, "queued", json.dumps(payload, ensure_ascii=False), now, now))
        return task_id

    def task(self, task_id: str):
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task_dict(row) if row else None

    def has_running_task(self) -> bool:
        with self._lock, self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE status IN ('running','polling','downloading') LIMIT 1").fetchone() is not None

    def tasks(self, limit: int = 100):
        with self._lock, self.connect() as db:
            return [self._task_dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))]

    def next_queued_task(self):
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
            return self._task_dict(row) if row else None

    @staticmethod
    def _task_dict(row):
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def update_task(self, task_id: str, status: str, message: str = "", attempts: int | None = None):
        now = utc_now()
        with self._lock, self.connect() as db:
            if attempts is None:
                db.execute("UPDATE tasks SET status=?,message=?,updated_at=? WHERE id=?", (status, message[:1000], now, task_id))
            else:
                db.execute("UPDATE tasks SET status=?,message=?,attempts=?,updated_at=? WHERE id=?", (status, message[:1000], attempts, now, task_id))

    def update_task_payload(self, task_id: str, payload: dict):
        with self._lock, self.connect() as db:
            db.execute("UPDATE tasks SET payload=?,updated_at=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), utc_now(), task_id))

    def recover_interrupted(self):
        with self._lock, self.connect() as db:
            rows = db.execute("SELECT id,kind,payload FROM tasks WHERE status IN ('running','polling','downloading')").fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                resumable = row["kind"] == "build" and payload.get("phase") in {"dispatching", "polling", "downloading"}
                status = "queued" if resumable else "interrupted"
                message = "容器重启后恢复已有 GitHub 任务；不会重复触发" if resumable else "容器重启时任务未完成；请检查状态后重试"
                db.execute("UPDATE tasks SET status=?,message=?,updated_at=? WHERE id=?", (status, message, utc_now(), row["id"]))

    def add_release(self, release_id: str, data_commit: str, code_commit: str, path: Path, metadata: dict):
        with self._lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO releases(release_id,data_commit,code_commit,path,created_at,metadata) VALUES(?,?,?,?,?,?)", (release_id, data_commit, code_commit, str(path), utc_now(), json.dumps(metadata, ensure_ascii=False)))

    def releases(self, limit=100):
        with self._lock, self.connect() as db:
            rows = db.execute("SELECT * FROM releases ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]
