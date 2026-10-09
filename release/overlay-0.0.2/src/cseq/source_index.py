from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import os
import time
from pathlib import Path
from typing import Any


class SourceFingerprintIndex:
    """Persistent metadata/hash index for project source files.

    The index is only an acceleration cache. A content hash is reused only when
    the full stat identity used here is unchanged; otherwise bytes are hashed
    again. Dependency snapshots are stored separately and validated against the
    current file hashes before reuse.
    """

    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files(
              path TEXT PRIMARY KEY,
              size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              ctime_ns INTEGER NOT NULL,
              dev TEXT NOT NULL,
              ino TEXT NOT NULL,
              sha256 TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS dependencies(
              tu_path TEXT NOT NULL,
              context_hash TEXT NOT NULL,
              source_hash TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              records_json TEXT NOT NULL,
              PRIMARY KEY(tu_path, context_hash)
            ) WITHOUT ROWID;
            """
        )
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        old = int(row[0]) if row and str(row[0]).isdigit() else None
        if old != self.SCHEMA_VERSION:
            # v2 stores filesystem device/inode identities as exact decimal text.
            # Windows can expose unsigned file IDs larger than SQLite's signed
            # 64-bit INTEGER range; this is an acceleration cache, so discard the
            # old v1 table rather than narrowing or truncating those identifiers.
            self.connection.execute("DROP TABLE IF EXISTS files")
            self.connection.execute(
                """CREATE TABLE files(
                     path TEXT PRIMARY KEY,
                     size INTEGER NOT NULL,
                     mtime_ns INTEGER NOT NULL,
                     ctime_ns INTEGER NOT NULL,
                     dev TEXT NOT NULL,
                     ino TEXT NOT NULL,
                     sha256 TEXT NOT NULL
                   ) WITHOUT ROWID"""
            )
            self.connection.execute("DELETE FROM dependencies")
        self.connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
            (str(self.SCHEMA_VERSION),),
        )
        self.connection.commit()

        self.hash_hits = 0
        self.hash_misses = 0
        self.dependency_hits = 0
        self.dependency_misses = 0

    @staticmethod
    def _stat_identity(path: Path) -> tuple[int, int, int, str, str]:
        st = path.stat()
        return (
            int(st.st_size),
            int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
            int(getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))),
            str(int(getattr(st, "st_dev", 0))),
            str(int(getattr(st, "st_ino", 0))),
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def content_hash(self, path: str | Path) -> str:
        p = Path(path).resolve()
        key = str(p)
        size, mtime_ns, ctime_ns, dev, ino = self._stat_identity(p)
        with self._lock:
            row = self.connection.execute(
                "SELECT size,mtime_ns,ctime_ns,dev,ino,sha256 FROM files WHERE path=?",
                (key,),
            ).fetchone()
        if row and tuple(row[:5]) == (size, mtime_ns, ctime_ns, dev, ino):
            # Windows filesystems can expose unchanged stat timestamps for a
            # same-size rewrite performed within one clock tick.  Revalidate
            # only very recent files; normal warm-cache scans remain metadata-
            # only while immediate edits cannot silently reuse stale content.
            recent_windows_file = (
                os.name == "nt"
                and 0 <= time.time_ns() - mtime_ns <= 250_000_000
            )
            if not recent_windows_file:
                with self._lock:
                    self.hash_hits += 1
                return str(row[5])
            digest = self._hash_file(p)
            if digest == str(row[5]):
                with self._lock:
                    self.hash_hits += 1
                return digest
            with self._lock:
                self.connection.execute(
                    """INSERT OR REPLACE INTO files(path,size,mtime_ns,ctime_ns,dev,ino,sha256)
                       VALUES(?,?,?,?,?,?,?)""",
                    (key, size, mtime_ns, ctime_ns, dev, ino, digest),
                )
                self.hash_misses += 1
            return digest
        digest = self._hash_file(p)
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO files(path,size,mtime_ns,ctime_ns,dev,ino,sha256)
                   VALUES(?,?,?,?,?,?,?)""",
                (key, size, mtime_ns, ctime_ns, dev, ino, digest),
            )
            self.hash_misses += 1
        return digest

    def remove_missing_files(self, current_paths: set[str], project_root: str | Path | None = None) -> int:
        root = Path(project_root).resolve() if project_root is not None else None
        with self._lock:
            rows = [r[0] for r in self.connection.execute("SELECT path FROM files")]
            stale: list[str] = []
            for path_text in rows:
                if path_text in current_paths:
                    continue
                if root is not None:
                    try:
                        Path(path_text).resolve().relative_to(root)
                    except ValueError:
                        # External include hashes are also cached here. A project
                        # scan must not evict them merely because they are not
                        # project-owned source files.
                        continue
                stale.append(path_text)
            for path_text in stale:
                self.connection.execute("DELETE FROM files WHERE path=?", (path_text,))
        return len(stale)

    def dependency_snapshot(
        self, tu_path: str, context_hash: str, source_hash: str
    ) -> tuple[str, list[dict[str, Any]]] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT source_hash,fingerprint,records_json
                   FROM dependencies WHERE tu_path=? AND context_hash=?""",
                (tu_path, context_hash),
            ).fetchone()
        if not row or row[0] != source_hash:
            with self._lock:
                self.dependency_misses += 1
            return None
        try:
            records = json.loads(row[2])
        except (TypeError, json.JSONDecodeError):
            with self._lock:
                self.dependency_misses += 1
            return None
        if not isinstance(records, list):
            with self._lock:
                self.dependency_misses += 1
            return None
        return str(row[1]), records

    def note_dependency_hit(self) -> None:
        with self._lock:
            self.dependency_hits += 1

    def note_dependency_miss(self) -> None:
        with self._lock:
            self.dependency_misses += 1

    def put_dependency_snapshot(
        self,
        tu_path: str,
        context_hash: str,
        source_hash: str,
        fingerprint: str,
        records: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO dependencies(
                     tu_path,context_hash,source_hash,fingerprint,records_json
                   ) VALUES(?,?,?,?,?)""",
                (
                    tu_path,
                    context_hash,
                    source_hash,
                    fingerprint,
                    json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )

    def commit(self) -> None:
        with self._lock:
            self.connection.commit()

    def clear(self) -> None:
        with self._lock:
            self.connection.execute("DELETE FROM files")
            self.connection.execute("DELETE FROM dependencies")
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()
