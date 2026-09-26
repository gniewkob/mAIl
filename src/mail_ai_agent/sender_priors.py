from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CACHE: dict[str, Any] = {"path": None, "mtime": None, "payload": {}}


def load_sender_priors(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        stat = path.stat()
    except Exception:
        return {}
    abs_path = str(path.resolve())
    mtime = int(stat.st_mtime_ns)
    if _CACHE["path"] == abs_path and _CACHE["mtime"] == mtime:
        cached = _CACHE.get("payload", {})
        return cached if isinstance(cached, dict) else {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _CACHE["path"] = abs_path
    _CACHE["mtime"] = mtime
    _CACHE["payload"] = payload
    return payload


def resolve_sender_prior(
    *,
    payload: dict[str, Any],
    mailbox_id: str,
    sender_sha256: str,
    min_samples: int,
    min_share: float,
) -> dict[str, Any] | None:
    by_mailbox = payload.get("by_mailbox", {})
    if not isinstance(by_mailbox, dict):
        return None
    mailbox_payload = by_mailbox.get(mailbox_id, {})
    if not isinstance(mailbox_payload, dict):
        return None
    senders = mailbox_payload.get("senders", {})
    if not isinstance(senders, dict):
        return None
    candidate = senders.get(sender_sha256)
    if not isinstance(candidate, dict):
        return None
    samples = int(candidate.get("samples", 0) or 0)
    share = float(candidate.get("share", 0.0) or 0.0)
    category = str(candidate.get("category", "")).strip().lower()
    if not category:
        return None
    if samples < min_samples or share < min_share:
        return None
    return {"category": category, "samples": samples, "share": share}
