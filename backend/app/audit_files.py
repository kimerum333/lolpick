"""Append-only UTF-8 JSONL audit segments. No deletion or networking."""
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class AuditFiles:
    def __init__(self, directory, segment_bytes=50 * 1024 * 1024):
        self.directory = Path(directory)
        self.segment_bytes = segment_bytes
        self.current, self.day = None, None

    def append(self, event, serialize):
        # Caller holds the host-wide SQLite writer lock, including during fsync.
        from app.audit_journal import private_directory
        private_directory(self.directory)
        if self.current is not None and self.current.is_symlink():
            raise OSError('Audit symlinks are forbidden.')
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self.day != day or self.current is None or self.current.stat().st_size >= self.segment_bytes:
            self.day = day
            self.current = self.directory / f'audit-{day}-{os.getpid()}-{uuid4().hex}.jsonl'
            fd = os.open(self.current, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(fd)
            if os.name == 'posix':
                directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        event['archive_file'] = 'archive/' + self.current.name
        payload = (serialize(event) + '\n').encode('utf-8')
        if len(payload) > 32768:
            raise ValueError('Audit file record exceeds its bound.')
        fd = None
        try:
            fd = os.open(self.current, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('Audit target is not a regular file.')
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError('Audit file write did not complete.')
                view = view[written:]
            os.fsync(fd)
        except Exception:
            # An incomplete tail is never joined to a later JSON record.
            self.current = None
            raise
        finally:
            if fd is not None:
                os.close(fd)
        return serialize(event)
