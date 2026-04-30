from __future__ import annotations

import json
from pathlib import Path

from .config import MailboxConfig, Settings
from .constants import ActionTaken
from .folder_mapper import category_to_folder
from .schemas import FinalDecision, LLMClassification, RuleDecision, WorkflowStatus

_THRESHOLDS_CACHE: dict[str, object] = {
    "path": None,
    "mtime": None,
    "payload": {},
}


def _resolve_thresholds(settings: Settings, mailbox: MailboxConfig, category: str) -> float:
    default_main = float(settings.move_confidence_threshold)
    default_other = float(settings.other_move_confidence_threshold)
    default_value = default_other if category == "other" else default_main
    path = settings.mailbox_thresholds_path
    if path is None:
        return default_value

    payload = _load_thresholds_payload(path)
    by_mailbox = payload.get("by_mailbox", {})
    if not isinstance(by_mailbox, dict):
        return default_value
    mailbox_payload = by_mailbox.get(mailbox.mailbox_id, {})
    if not isinstance(mailbox_payload, dict):
        return default_value

    if category == "other":
        value = mailbox_payload.get("other_move_confidence_threshold")
    else:
        value = mailbox_payload.get("move_confidence_threshold")
    if isinstance(value, (int, float)):
        return float(value)
    return default_value


def _load_thresholds_payload(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

    abs_path = str(path.resolve())
    mtime = int(stat.st_mtime_ns)
    if _THRESHOLDS_CACHE["path"] == abs_path and _THRESHOLDS_CACHE["mtime"] == mtime:
        cached = _THRESHOLDS_CACHE.get("payload", {})
        if isinstance(cached, dict):
            return cached
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _THRESHOLDS_CACHE["path"] = abs_path
    _THRESHOLDS_CACHE["mtime"] = mtime
    _THRESHOLDS_CACHE["payload"] = payload
    return payload


def decide_from_rule(rule: RuleDecision) -> FinalDecision:
    flags = ["\\Flagged"] if rule.requires_flag else []
    # Map internal rule action to ActionTaken enum (without move_ prefix)
    action_mapping = {
        "skip_ai": ActionTaken.SKIP_AI,
        "route": ActionTaken.ROUTE_REPLY,
        "needs_llm": ActionTaken.ROUTE_FROM_LLM,
    }
    return FinalDecision(
        category=rule.category,
        priority="medium",  # Rules don't compute priority
        confidence=1.0,  # Rule matches are 100% confident
        target_folder=rule.target_folder,
        flags=flags,
        final_status=WorkflowStatus.PROCESSED,
        action_taken=action_mapping.get(rule.action, ActionTaken.ROUTE_REPLY),
        requires_reply=False,  # Rules don't determine reply need
        summary=f"Rule matched: {rule.reason}",
        reasoning_short=rule.reason,
    )


def decide_from_llm(classification: LLMClassification, settings: Settings, mailbox: MailboxConfig) -> FinalDecision:
    required_confidence = _resolve_thresholds(settings, mailbox, classification.category)

    if classification.confidence < required_confidence:
        return FinalDecision(
            category=classification.category,
            priority=classification.priority,
            confidence=classification.confidence,
            target_folder=mailbox.imap_uncertain_folder,
            flags=[],
            final_status=WorkflowStatus.UNCERTAIN,
            action_taken=ActionTaken.ROUTE_UNCERTAIN,
            requires_reply=classification.requires_reply,
            summary=classification.summary,
            reasoning_short=classification.reasoning_short,
        )

    flags: list[str] = []
    if classification.category == "complaint" and classification.priority == "high":
        flags.append("\\Flagged")
    elif classification.confidence >= settings.flag_confidence_threshold and classification.priority == "high":
        flags.append("\\Flagged")

    draft_reply = None
    if (
        classification.requires_reply
        and classification.confidence >= settings.draft_confidence_threshold
        and classification.draft_reply
    ):
        draft_reply = classification.draft_reply

    return FinalDecision(
        category=classification.category,
        priority=classification.priority,
        confidence=classification.confidence,
        target_folder=category_to_folder(classification.category, mailbox),
        flags=flags,
        final_status=WorkflowStatus.PROCESSED,
        action_taken=ActionTaken.ROUTE_FROM_LLM,
        requires_reply=classification.requires_reply,
        summary=classification.summary,
        reasoning_short=classification.reasoning_short,
        draft_reply=draft_reply,
    )
