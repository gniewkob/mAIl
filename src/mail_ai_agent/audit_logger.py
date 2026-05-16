"""Audit logger for email processing operations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import _chmod_owner_only, _secure_open


class AuditLogger:
    """Thread-safe audit logger with file locking.
    
    This logger ensures atomic writes to the audit log file using:
    1. Thread-level locking (for multi-threaded use)
    2. File-level locking with fcntl (for multi-process use)
    """
    
    REDACTED_FIELDS = {"message_id", "sender", "subject", "draft_path"}

    def __init__(self, path: Path, *, redact_pii: bool = True, fsync: bool = True) -> None:
        self.path = path
        self.redact_pii = redact_pii
        self._fsync = fsync
        self._lock = threading.Lock()  # Thread-level lock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.path.parent)

    def log(self, **payload: Any) -> None:
        """Log an audit record with thread/process-safe file locking."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self._sanitize_payload(payload),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with _secure_open(self.path, "a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.write(line)
                    handle.flush()
                    if self._fsync:
                        os.fsync(handle.fileno())
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        _chmod_owner_only(self.path)

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Remove or hash PII fields from payload."""
        if not self.redact_pii:
            return payload
        sanitized = dict(payload)
        for field in self.REDACTED_FIELDS:
            value = sanitized.pop(field, None)
            if value in (None, ""):
                continue
            sanitized[f"{field}_sha256"] = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        return sanitized
