from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _hash_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _chmod_owner_only(path: Path) -> None:
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass
