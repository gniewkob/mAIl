from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .utils import _chmod_owner_only


@dataclass
class RotationResult:
    rotated: bool
    archive_path: Path | None = None
    original_size: int = 0


@dataclass
class DraftPruneResult:
    removed: int
    kept: int


@dataclass
class StateScrubResult:
    updated_rows: int


@dataclass
class DraftScrubResult:
    updated_files: int


def rotate_audit_log(path: Path, *, max_bytes: int, backup_count: int = 5) -> RotationResult:
    if not path.exists():
        return RotationResult(rotated=False, archive_path=None, original_size=0)

    size = path.stat().st_size
    if size < max_bytes:
        return RotationResult(rotated=False, archive_path=None, original_size=size)

    oldest = path.with_suffix(path.suffix + f".{backup_count}")
    if oldest.exists():
        oldest.unlink()

    for idx in range(backup_count - 1, 0, -1):
        source = path.with_suffix(path.suffix + f".{idx}")
        target = path.with_suffix(path.suffix + f".{idx + 1}")
        if source.exists():
            source.replace(target)

    archive = path.with_suffix(path.suffix + ".1")
    path.replace(archive)          # atomic rename — crash-safe
    _chmod_owner_only(archive)
    path.touch()                   # create fresh empty log
    _chmod_owner_only(path)
    return RotationResult(rotated=True, archive_path=archive, original_size=size)


def prune_drafts(draft_dir: Path, *, older_than_days: int) -> DraftPruneResult:
    draft_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    removed = 0
    kept = 0

    for item in draft_dir.iterdir():
        if not item.is_file():
            continue
        modified = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            item.unlink()
            removed += 1
        else:
            kept += 1

    return DraftPruneResult(removed=removed, kept=kept)


@dataclass
class StateScrubResult:
    updated_rows: int


def scrub_state_pii(db_path: Path) -> StateScrubResult:
    if not db_path.exists():
        return StateScrubResult(updated_rows=0)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # First pass: compute and persist sha256 hashes for rows that have PII but NULL hashes
        rows_needing_hash = conn.execute(
            """
            SELECT id, sender, subject
            FROM email_processing_state
            WHERE (sender_sha256 IS NULL AND sender IS NOT NULL AND sender != '' AND sender != '[redacted]')
               OR (subject_sha256 IS NULL AND subject IS NOT NULL AND subject != '' AND subject != '[redacted]')
            """
        ).fetchall()
        for row in rows_needing_hash:
            sender = row["sender"]
            subject = row["subject"]
            sender_hash = _hash_value(sender) if sender not in (None, "", "[redacted]") else None
            subject_hash = _hash_value(subject) if subject not in (None, "", "[redacted]") else None
            conn.execute(
                """
                UPDATE email_processing_state
                SET sender_sha256 = COALESCE(sender_sha256, ?),
                    subject_sha256 = COALESCE(subject_sha256, ?)
                WHERE id = ?
                """,
                (sender_hash, subject_hash, row["id"]),
            )
        # Second pass: batch redact PII in one SQL statement
        cursor = conn.execute(
            """
            UPDATE email_processing_state
            SET sender = '[redacted]', subject = '[redacted]'
            WHERE (sender IS NOT NULL AND sender != '' AND sender != '[redacted]')
               OR (subject IS NOT NULL AND subject != '' AND subject != '[redacted]')
            """
        )
        updated_rows = cursor.rowcount

    return StateScrubResult(updated_rows=updated_rows)


def maintain_sqlite(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {"status": "missing"}

    with sqlite3.connect(db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.execute("VACUUM")
    return {"status": "ok", "integrity_check": str(integrity)}


def scrub_draft_pii(draft_dir: Path) -> DraftScrubResult:
    draft_dir.mkdir(parents=True, exist_ok=True)
    updated_files = 0
    for item in draft_dir.iterdir():
        if not item.is_file() or item.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        sender = payload.get("sender")
        subject = payload.get("subject")
        changed = False
        if sender not in (None, "", "[redacted]"):
            payload["sender_sha256"] = payload.get("sender_sha256") or _hash_value(sender)
            payload["sender"] = "[redacted]"
            changed = True
        if subject not in (None, "", "[redacted]"):
            payload["subject_sha256"] = payload.get("subject_sha256") or _hash_value(subject)
            payload["subject"] = "[redacted]"
            changed = True
        if changed:
            item.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            _chmod_owner_only(item)
            updated_files += 1
    return DraftScrubResult(updated_files=updated_files)


def _hash_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


