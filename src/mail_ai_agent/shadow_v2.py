"""Shadow mode for classifier v2: computed next to the production decision, logged, never routes.

Enabled with CLASSIFIER_V2_SHADOW=true. Every failure is swallowed: shadow must never change
or slow down production beyond one extra model call per LLM-bound message.
Log: CLASSIFIER_V2_SHADOW_LOG (JSONL, 0600). Review with ops/shadow_report.py.
"""
from __future__ import annotations

import json
import logging
import os
import time
from email.utils import parseaddr
from pathlib import Path

from .automated_sender import HUMAN_CATEGORIES
from .classifier_v2 import ActionGateway, classify_v2
from .config import MailboxConfig, Settings
from .schemas import ParsedEmail
from .sent_index import SentIndex

LOGGER = logging.getLogger(__name__)


class ShadowV2:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.gateway = ActionGateway(settings)
        self._index = SentIndex()
        self._index_mtime: float | None = None

    def _current_index(self) -> SentIndex:
        path = Path(self.settings.sent_index_path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._index
        if mtime != self._index_mtime:
            self._index, self._index_mtime = SentIndex.load(path), mtime
        return self._index

    def record(self, parsed: ParsedEmail, mailbox: MailboxConfig, v1_category: str, fingerprint: str) -> None:
        started = time.perf_counter()
        row: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mailbox": mailbox.mailbox_id,
            "fingerprint": fingerprint[:16],
            "sender_domain": parseaddr(parsed.sender or "")[1].rsplit("@", 1)[-1].lower(),
            "subject": (parsed.subject or "")[:120],
            "v1": v1_category,
        }
        try:
            d = classify_v2(parsed, mailbox, self.settings, self.gateway, self._current_index())
            row.update(v2=d.category, source=d.source, action=d.action, confidence=d.confidence,
                       reason=(d.reason or "")[:200],
                       inbox_v1=v1_category in HUMAN_CATEGORIES, inbox_v2=d.category in HUMAN_CATEGORIES)
        except Exception as exc:  # never propagate into the worker
            row.update(error=f"{type(exc).__name__}: {exc}"[:200])
        row["latency_ms"] = int((time.perf_counter() - started) * 1000)
        try:
            path = Path(self.settings.classifier_v2_shadow_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            LOGGER.warning("shadow v2 log write failed", exc_info=True)
