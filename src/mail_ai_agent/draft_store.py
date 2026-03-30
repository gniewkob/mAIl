from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .schemas import FinalDecision, ParsedEmail
from .utils import _chmod_owner_only, _hash_value


class DraftStore:
    def __init__(self, draft_dir: Path) -> None:
        self.draft_dir = draft_dir
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.draft_dir)

    def save(
        self,
        parsed_email: ParsedEmail,
        decision: FinalDecision,
        fingerprint: str,
        *,
        redact_pii: bool = False,
    ) -> Path:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed_email.subject or "no-subject").strip("-").lower()
        target = self.draft_dir / f"{slug[:40] or 'draft'}-{fingerprint[:8]}.json"
        subject = parsed_email.subject
        sender = parsed_email.sender
        payload: dict[str, object] = {
            "subject": "[redacted]" if (redact_pii and subject) else subject,
            "sender": "[redacted]" if (redact_pii and sender) else sender,
            "draft_reply": decision.draft_reply,
            "summary": decision.summary,
            "category": decision.category,
        }
        if redact_pii:
            if subject:
                payload["subject_sha256"] = _hash_value(subject)
            if sender:
                payload["sender_sha256"] = _hash_value(sender)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _chmod_owner_only(tmp)
        os.replace(tmp, target)
        return target


