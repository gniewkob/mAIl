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


def _secure_open(path: Path, mode: str, encoding: str = "utf-8", private: bool = True) -> os.fdopen:
    """
    Open a file with restricted permissions (0o600) to prevent race conditions.
    """
    flags = 0
    if "w" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    elif "a" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_APPEND
    elif "r" in mode:
        flags |= os.O_RDONLY

    if "b" in mode:
        # Binary mode not directly supported by this simple wrapper's flags but can be added if needed
        pass

    file_mode = 0o600 if private else 0o644

    fd = os.open(path, flags, file_mode)
    return os.fdopen(fd, mode, encoding=encoding if "b" not in mode else None)


def _secure_write_text(path: Path, content: str, encoding: str = "utf-8", private: bool = True) -> None:
    """
    Write text to a file with restricted permissions (0o600) to prevent race conditions.
    """
    file_mode = 0o600 if private else 0o644
    # os.O_TRUNC ensures we overwrite existing content if file already exists
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
    with os.fdopen(fd, "w", encoding=encoding) as f:
        f.write(content)
