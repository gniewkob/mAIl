from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class RotationResult:
    rotated: bool
    archive_path: Path | None = None
    original_size: int = 0


@dataclass
class DraftPruneResult:
    removed: int
    kept: int


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
    shutil.copy2(path, archive)
    path.write_text("", encoding="utf-8")
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


def maintain_sqlite(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {"status": "missing"}

    with sqlite3.connect(db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.execute("VACUUM")
    return {"status": "ok", "integrity_check": str(integrity)}
