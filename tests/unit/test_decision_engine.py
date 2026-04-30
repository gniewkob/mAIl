"""Tests for decision engine consistency."""

import json
import pytest

from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.constants import ActionTaken, WorkflowStatus
from mail_ai_agent.decision_engine import decide_from_llm, decide_from_rule
from mail_ai_agent.schemas import LLMClassification, RuleDecision


class TestDecideFromRule:
    """Test that decide_from_rule returns consistent FinalDecision."""

    def test_decide_from_rule_sets_all_fields(self):
        """decide_from_rule should set all fields like decide_from_llm does."""
        rule = RuleDecision(
            category="billing",
            target_folder="INBOX.Billing",
            action="skip_ai",
            requires_flag=False,
            reason="billing keyword matched",
        )
        
        decision = decide_from_rule(rule)
        
        # Fields that were previously missing
        assert decision.priority == "medium"
        assert decision.confidence == 1.0
        assert decision.requires_reply is False
        assert decision.summary == "Rule matched: billing keyword matched"
        assert decision.reasoning_short == "billing keyword matched"
        
        # Original fields
        assert decision.category == "billing"
        assert decision.target_folder == "INBOX.Billing"
        assert decision.final_status == WorkflowStatus.PROCESSED
        assert decision.action_taken == ActionTaken.SKIP_AI

    def test_decide_from_rule_with_flag(self):
        """decide_from_rule with requires_flag=True sets Flagged flag."""
        rule = RuleDecision(
            category="complaint",
            target_folder="INBOX.Complaints",
            action="route",
            requires_flag=True,
            reason="complaint pattern matched",
        )
        
        decision = decide_from_rule(rule)
        
        assert "\\Flagged" in decision.flags
        assert decision.action_taken == ActionTaken.ROUTE_REPLY

    def test_decide_from_rule_action_mapping(self):
        """Test action mapping for different rule actions."""
        test_cases = [
            ("skip_ai", ActionTaken.SKIP_AI),
            ("route", ActionTaken.ROUTE_REPLY),
            ("needs_llm", ActionTaken.ROUTE_FROM_LLM),
        ]
        
        for action, expected_action_taken in test_cases:
            rule = RuleDecision(
                category="test",
                target_folder="INBOX.Test",
                action=action,  # type: ignore[arg-type]
                requires_flag=False,
                reason="test",
            )
            
            decision = decide_from_rule(rule)
            assert decision.action_taken == expected_action_taken


def _mailbox() -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="mbox",
        imap_host="imap.example.com",
        imap_user="user@example.com",
        imap_pass="secret",
    )


def test_decide_from_llm_routes_other_when_above_other_threshold() -> None:
    settings = Settings(IMAP_HOST="imap.example.com", IMAP_USER="user@example.com", IMAP_PASS="secret")
    classification = LLMClassification(
        category="other",
        priority="low",
        requires_reply=False,
        confidence=0.6,
        summary="Wiadomość ogólna.",
        entities={},
        draft_reply=None,
        reasoning_short="Brak silnych sygnałów dla innych kategorii.",
    )

    decision = decide_from_llm(classification, settings, _mailbox())

    assert decision.final_status == WorkflowStatus.PROCESSED
    assert decision.target_folder == "INBOX.Other"
    assert decision.action_taken == ActionTaken.ROUTE_FROM_LLM


def test_decide_from_llm_keeps_non_other_below_main_threshold_uncertain() -> None:
    settings = Settings(IMAP_HOST="imap.example.com", IMAP_USER="user@example.com", IMAP_PASS="secret")
    classification = LLMClassification(
        category="newsletter",
        priority="low",
        requires_reply=False,
        confidence=0.6,
        summary="Newsletter sklepu.",
        entities={},
        draft_reply=None,
        reasoning_short="Treść promocyjna, ale pewność zbyt niska dla automatycznego ruchu.",
    )

    decision = decide_from_llm(classification, settings, _mailbox())

    assert decision.final_status == WorkflowStatus.UNCERTAIN
    assert decision.target_folder == "INBOX.AI-Uncertain"
    assert decision.action_taken == ActionTaken.ROUTE_UNCERTAIN


def test_decide_from_llm_uses_mailbox_threshold_override(tmp_path) -> None:
    thresholds_path = tmp_path / "mailbox_thresholds.auto.json"
    thresholds_path.write_text(
        json.dumps(
            {
                "by_mailbox": {
                    "mbox": {
                        "move_confidence_threshold": 0.55,
                        "other_move_confidence_threshold": 0.45,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        MAILBOX_THRESHOLDS_PATH=str(thresholds_path),
    )
    classification = LLMClassification(
        category="newsletter",
        priority="low",
        requires_reply=False,
        confidence=0.6,
        summary="Newsletter sklepu.",
        entities={},
        draft_reply=None,
        reasoning_short="override threshold",
    )

    decision = decide_from_llm(classification, settings, _mailbox())

    assert decision.final_status == WorkflowStatus.PROCESSED
