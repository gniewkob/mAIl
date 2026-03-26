from __future__ import annotations

from .config import MailboxConfig, Settings
from .folder_mapper import category_to_folder
from .schemas import FinalDecision, LLMClassification, RuleDecision, WorkflowStatus


def decide_from_rule(rule: RuleDecision) -> FinalDecision:
    return FinalDecision(
        category=rule.category,
        target_folder=rule.target_folder,
        flags=[],
        final_status=WorkflowStatus.PROCESSED,
        action_taken=rule.action,
    )


def decide_from_llm(classification: LLMClassification, settings: Settings, mailbox: MailboxConfig) -> FinalDecision:
    if classification.confidence < settings.move_confidence_threshold:
        return FinalDecision(
            category=classification.category,
            priority=classification.priority,
            confidence=classification.confidence,
            target_folder=mailbox.imap_uncertain_folder,
            flags=[],
            final_status=WorkflowStatus.UNCERTAIN,
            action_taken="route_uncertain",
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
        action_taken="route_from_llm",
        requires_reply=classification.requires_reply,
        summary=classification.summary,
        reasoning_short=classification.reasoning_short,
        draft_reply=draft_reply,
    )
