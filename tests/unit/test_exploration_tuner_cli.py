from __future__ import annotations

from mail_ai_agent.exploration_tuner_cli import recommend_rate


def test_recommend_rate_decreases_on_overload() -> None:
    rate, action = recommend_rate(
        current_rate=0.02,
        uncertain_by_mailbox={"m1": 20},
        failed_by_mailbox={"m1": 0},
        max_uncertain_per_mailbox=15,
        max_failed_per_mailbox=3,
        outcome_bad_rate_7d=0.0,
        corrected_7d=100,
    )
    assert action == "decrease"
    assert rate < 0.02


def test_recommend_rate_increases_on_low_risk_low_signal() -> None:
    rate, action = recommend_rate(
        current_rate=0.01,
        uncertain_by_mailbox={"m1": 1},
        failed_by_mailbox={"m1": 0},
        max_uncertain_per_mailbox=15,
        max_failed_per_mailbox=3,
        outcome_bad_rate_7d=0.0,
        corrected_7d=2,
    )
    assert action == "increase"
    assert rate > 0.01


def test_recommend_rate_holds_on_neutral() -> None:
    rate, action = recommend_rate(
        current_rate=0.02,
        uncertain_by_mailbox={"m1": 5},
        failed_by_mailbox={"m1": 0},
        max_uncertain_per_mailbox=15,
        max_failed_per_mailbox=3,
        outcome_bad_rate_7d=0.05,
        corrected_7d=20,
    )
    assert action == "hold"
    assert rate == 0.02


def test_recommend_rate_holds_without_mailbox_signal() -> None:
    rate, action = recommend_rate(
        current_rate=0.01,
        uncertain_by_mailbox={},
        failed_by_mailbox={},
        max_uncertain_per_mailbox=15,
        max_failed_per_mailbox=3,
        outcome_bad_rate_7d=0.0,
        corrected_7d=0,
    )
    assert action == "hold"
    assert rate == 0.01
