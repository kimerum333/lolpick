"""Private durable outbox. Commit locally before processing; acknowledge after DB commit.

SQLite is an operational journal, NOT the application database. All processes on
one host share a WAL file. Use a persistent local disk, never an NFS mount.
"""
import json
import os
import sqlite3
import shutil
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from app.core.config import get_settings


class JournalUnavailable(RuntimeError):
    pass


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise JournalUnavailable("Invalid journal directory.")
    os.chmod(path, 0o700)
    return path


class Journal:
    def __init__(self, directory, max_events=200000, retention_days=30, max_bytes=256 * 1024 * 1024,
                 segment_bytes=50 * 1024 * 1024):
        self.path = private_directory(directory) / "audit.sqlite3"
        self.max_events, self.retention_days, self.max_bytes = max_events, retention_days, max_bytes
        from app.audit_files import AuditFiles
        self.files = AuditFiles(self.path.parent / 'archive', segment_bytes)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        os.close(fd)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    created REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS pending ON events(delivered, retry_at, created);
                CREATE TABLE IF NOT EXISTS rate_limits (
                    bucket TEXT PRIMARY KEY, window INTEGER NOT NULL, hits INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            if 'archived' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute('ALTER TABLE events ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('rate_secret', ?)", (os.urandom(32).hex(),))

    @contextmanager
    def connection(self):
        # umask prevents WAL/SHM creation with broader permissions on POSIX.
        # SQLite derives sidecar modes from the 0600 main database.
        try:
            if self.path.parent.is_symlink() or any(Path(str(self.path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm")):
                raise JournalUnavailable("Journal symlinks are forbidden.")
            db = sqlite3.connect(self.path, timeout=1)
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    yield db
            finally:
                db.close()
        except (OSError, sqlite3.Error) as exc:
            raise JournalUnavailable("Journal operation failed.") from exc

    def append(self, event):
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload.encode()) > 32768:
            raise JournalUnavailable("Journal event exceeds its limit.")
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM events WHERE event_key=?", (event["event_key"],)).fetchone():
                return
            count = db.execute('SELECT count(*) FROM events').fetchone()[0]
            allocated = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            free_pages = db.execute("PRAGMA freelist_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            pressure = count >= self.max_events // 2 or allocated - free_pages >= self.max_bytes // 2
            pruned = db.execute("SELECT value FROM metadata WHERE key='last_pruned'").fetchone()
            if pressure or pruned is None or now - float(pruned[0]) >= 3600:
                # Prune complete pairs together; keep undecidable starts and all pending events.
                db.execute("""DELETE FROM events WHERE event_key IN (
                    SELECT s.event_key FROM events s JOIN events f
                      ON f.event_key=substr(s.event_key,1,length(s.event_key)-5)||'finish'
                     WHERE json_extract(s.payload,'$.phase')='started' AND s.delivered=1 AND f.delivered=1
                       AND s.archived=1 AND f.archived=1 AND f.created<?
                    UNION ALL
                    SELECT f.event_key FROM events s JOIN events f
                      ON f.event_key=substr(s.event_key,1,length(s.event_key)-5)||'finish'
                     WHERE json_extract(s.payload,'$.phase')='started' AND s.delivered=1 AND f.delivered=1
                       AND s.archived=1 AND f.archived=1 AND f.created<?)""",
                    ((now + 1 if pressure else now - self.retention_days * 86400),) * 2)
                db.execute("""DELETE FROM events WHERE delivered=1 AND archived=1 AND created<?
                              AND json_extract(payload,'$.phase') NOT IN ('started','finished')""",
                           (now + 1 if pressure else now - self.retention_days * 86400,))
                db.execute("INSERT INTO metadata VALUES ('last_pruned',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),))
            allocated = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            free_pages = db.execute("PRAGMA freelist_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            occupied = allocated - free_pages
            if occupied + len(payload.encode()) + 8192 >= self.max_bytes or shutil.disk_usage(self.path.parent).free < 2 * 1024 * 1024:
                raise JournalUnavailable("Journal disk capacity reached.")
            if event.get("phase") == "started" and occupied >= self.max_bytes // 2:
                raise JournalUnavailable("Journal disk start capacity reached.")
            if db.execute("SELECT count(*) FROM events").fetchone()[0] >= self.max_events:
                raise JournalUnavailable("Journal capacity reached.")
            # Reserve room for all admitted requests' terminal entries.
            if event.get("phase") == "started" and db.execute("SELECT count(*) FROM events").fetchone()[0] >= self.max_events // 2:
                raise JournalUnavailable("Journal start capacity reached.")
            # File write and fsync must complete before admitting business work.
            # A crash can repeat a file event key; DB delivery deduplicates it.
            try:
                payload = self.files.append(event, lambda e: json.dumps(
                    e, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
            except (OSError, ValueError) as exc:
                raise JournalUnavailable('Audit file persistence failed.') from exc
            db.execute("INSERT INTO events(event_key,payload,created,archived) VALUES (?,?,?,1)",
                       (event["event_key"], payload, now))

    def archive_pending(self, limit=20):
        """Archive legacy journal details before pruning or DB delivery."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT event_key,payload FROM events WHERE archived=0 ORDER BY created LIMIT ?',
                              (limit,)).fetchall()
            for key, payload in rows:
                try:
                    archived = self.files.append(json.loads(payload), lambda e: json.dumps(
                        e, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
                except (OSError, ValueError) as exc:
                    raise JournalUnavailable('Legacy audit archival failed.') from exc
                db.execute('UPDATE events SET payload=?,archived=1 WHERE event_key=?', (archived, key))
        return len(rows)

    def pending(self, limit=20):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM events WHERE delivered=0 AND archived=1 AND retry_at<=? ORDER BY created,event_key LIMIT ?", (time.time(), limit))]

    def acknowledge(self, key):
        with self.connection() as db:
            db.execute("UPDATE events SET delivered=1 WHERE event_key=?", (key,))

    def defer(self, key):
        with self.connection() as db:
            row = db.execute("SELECT attempts FROM events WHERE event_key=?", (key,)).fetchone()
            if row:
                attempts = min(row[0] + 1, 30)
                db.execute("UPDATE events SET attempts=?,retry_at=? WHERE event_key=?",
                           (attempts, time.time() + min(300, 2 ** min(attempts, 8)), key))

    def replay(self, writer, limit=20):
        self.archive_pending(limit)
        sent = 0
        for event in self.pending(limit):
            try:
                accepted = writer(event) is True
            except Exception:
                accepted = False
            if not accepted:
                self.defer(event["event_key"])
                break  # DB outage: one bounded attempt per cycle, avoid amplifying it.
            self.acknowledge(event["event_key"])
            sent += 1
        return sent

    def summary(self):
        with self.connection() as db:
            return dict(total=db.execute("SELECT count(*) FROM events").fetchone()[0],
                        pending=db.execute("SELECT count(*) FROM events WHERE delivered=0").fetchone()[0],
                        completion_unknown=db.execute("""SELECT count(*) FROM events s
                            WHERE json_extract(s.payload,'$.phase')='started'
                            AND NOT EXISTS (SELECT 1 FROM events f WHERE f.event_key=substr(s.event_key,1,length(s.event_key)-5)||'finish')""").fetchone()[0])

    def storage_status(self):
        files = list(self.files.directory.glob('audit-*.jsonl')) if self.files.directory.exists() else []
        with self.connection() as db:
            legacy = db.execute('SELECT count(*) FROM events WHERE archived=0').fetchone()[0]
        return {'archive_files':len(files),'archive_bytes':sum(p.stat().st_size for p in files),
                'legacy_unarchived':legacy,'disk_free_bytes':shutil.disk_usage(self.path.parent).free,
                'outbox_event_limit':self.max_events,'files_auto_deleted':False}

    def replay_file(self, filename, writer, *, offset=0, limit=100):
        """Bounded operator recovery after loss/restore of the DB or outbox.

        Archive files are never consumed/deleted. Repeating a page is safe only
        with an idempotent DB writer such as persist_http_event.
        """
        import re
        if (not re.fullmatch(r'audit-\d{4}-\d{2}-\d{2}-\d+-[0-9a-f]{32}\.jsonl', filename)
                or type(offset) is not int or offset < 0 or not 1 <= limit <= 1000):
            raise ValueError('Invalid archive replay arguments.')
        path = self.files.directory / filename
        if path.is_symlink():
            raise JournalUnavailable('Archive symlinks are forbidden.')
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        sent, blocked, incomplete = 0, False, False
        with os.fdopen(fd,'rb') as stream:
            if offset:
                stream.seek(offset-1)
                if stream.read(1) != b'\n':
                    raise ValueError('Offset must be a JSONL record boundary.')
            stream.seek(offset)
            for _ in range(limit):
                began = stream.tell()
                line = stream.readline(32769)
                if not line:
                    break
                if not line.endswith(b'\n'):
                    incomplete = True
                    stream.seek(began)
                    break
                try:
                    event = json.loads(line)
                    accepted = writer(event) is True
                except Exception:
                    accepted = False
                if not accepted:
                    blocked = True
                    stream.seek(began)
                    break
                sent += 1
            next_offset = stream.tell()
        return {'delivered':sent,'next_offset':next_offset,'blocked':blocked,'incomplete_tail':incomplete}

    def export(self):
        with self.connection() as db:
            for row in db.execute("SELECT payload FROM events ORDER BY created,event_key"):
                yield row[0]

    def admit(self, client, category, limit):
        """Shared one-minute limits. Client is HMAC'd, never saved in clear text."""
        return self.admit_many([(client, category, limit)])

    def admit_many(self, entries):
        """Atomically check/update all identity limits; rejects consume no bucket."""
        import hashlib
        import hmac
        minute = int(time.time() // 60)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            secret = bytes.fromhex(db.execute("SELECT value FROM metadata WHERE key='rate_secret'").fetchone()[0])
            db.execute("DELETE FROM rate_limits WHERE window<?", (minute - 1,))
            updates = []
            for client, category, limit in entries:
                bucket = category + ':' + hmac.new(secret, client.encode(), hashlib.sha256).hexdigest()
                row = db.execute("SELECT window,hits FROM rate_limits WHERE bucket=?", (bucket,)).fetchone()
                hits = row[1] if row and row[0] == minute else 0
                if hits >= limit:
                    return False
                updates.append((bucket, minute, hits + 1, row is None))
            if db.execute("SELECT count(*) FROM rate_limits").fetchone()[0] + sum(x[3] for x in updates) > 10000:
                raise JournalUnavailable("Rate bucket capacity reached.")
            db.executemany("INSERT INTO rate_limits VALUES (?,?,?) ON CONFLICT(bucket) DO UPDATE SET window=excluded.window,hits=excluded.hits",
                           [x[:3] for x in updates])
            return True


@lru_cache
def _journal(directory, max_events, retention_days, max_bytes, segment_bytes):
    return Journal(directory, max_events, retention_days, max_bytes, segment_bytes)


def get_journal():
    settings = get_settings()
    return _journal(str(settings.log_directory), settings.audit_max_events, settings.audit_retention_days,
                    settings.audit_max_bytes, settings.audit_file_segment_bytes)
