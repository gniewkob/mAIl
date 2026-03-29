from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .schemas import FinalDecision, ParsedEmail


class DraftStore:
    def __init__(self, draft_dir: Path) -> None:
        self.draft_dir = draft_dir
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.draft_dir)

    def save(self, parsed_email: ParsedEmail, decision: FinalDecision, fingerprint: str) -> Path:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed_email.subject or "no-subject").strip("-").lower()
        target = self.draft_dir / f"{slug[:40] or 'draft'}-{fingerprint[:8]}.json"
        payload = {
            "subject": parsed_email.subject,
            "sender": parsed_email.sender,
            "draft_reply": decision.draft_reply,
            "summary": decision.summary,
            "category": decision.category,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _chmod_owner_only(target)
        return target


def _chmod_owner_only(path: Path) -> None:
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass
