from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.sender_priors_cli import build_sender_priors


def test_build_sender_priors_builds_majority_sender_map(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "sender_priors.auto.json"
    audit.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-05-20T00:00:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_llm"}),
                json.dumps({"timestamp": "2026-05-20T00:01:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_llm"}),
                json.dumps({"timestamp": "2026-05-20T00:02:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_llm"}),
                json.dumps({"timestamp": "2026-05-20T00:03:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_llm"}),
                json.dumps({"timestamp": "2026-05-20T00:04:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_llm"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_sender_priors(audit_log=audit, output_path=out, window_days=3650, min_samples=5, min_share=0.9)
    sender = payload["by_mailbox"]["m1"]["senders"]["s1"]
    assert sender["category"] == "newsletter"
    assert sender["samples"] == 5
    assert sender["share"] == 1.0
    assert out.exists()


def test_build_sender_priors_ignores_route_from_prior_actions(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "sender_priors.auto.json"
    audit.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-05-20T00:00:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_prior"}),
                json.dumps({"timestamp": "2026-05-20T00:01:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_prior"}),
                json.dumps({"timestamp": "2026-05-20T00:02:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_prior"}),
                json.dumps({"timestamp": "2026-05-20T00:03:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_prior"}),
                json.dumps({"timestamp": "2026-05-20T00:04:00+00:00", "mailbox_id": "m1", "sender_sha256": "s1", "category": "newsletter", "action_taken": "tag_route_from_prior"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_sender_priors(audit_log=audit, output_path=out, window_days=3650, min_samples=5, min_share=0.9)
    assert "m1" not in payload["by_mailbox"]
