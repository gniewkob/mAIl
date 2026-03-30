from __future__ import annotations

import argparse
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from .config import Settings
from .decision_engine import decide_from_llm, decide_from_rule
from .email_parser import parse_email
from .rule_engine import evaluate_rules
from .schemas import LLMClassification


def _mailbox_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="golden@example.com",
        IMAP_PASS=SecretStr("secret"),
        DRY_RUN=True,
    )


def _build_message(item: dict[str, Any]) -> bytes:
    message = EmailMessage()
    message["From"] = item["from"]
    message["Subject"] = item["subject"]
    message["Message-ID"] = item["message_id"]
    message.set_content(item["body"])
    return message.as_bytes()


def run_golden_set(dataset_path: Path) -> dict[str, Any]:
    settings = _mailbox_settings()
    mailbox = settings.load_mailboxes()[0]
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    for item in dataset:
        parsed = parse_email(_build_message(item), settings)
        rule = evaluate_rules(parsed, mailbox)
        if rule.action == "needs_llm":
            payload = item.get("llm_classification")
            if not payload:
                raise ValueError(f"Golden set item {item['message_id']} requires llm_classification fixture")
            decision = decide_from_llm(LLMClassification.model_validate(payload), settings, mailbox)
            route_source = "llm"
        else:
            decision = decide_from_rule(rule)
            route_source = "rule"

        ok = (
            decision.category == item["category_expected"]
            and decision.target_folder == item["target_folder_expected"]
            and decision.final_status.value == item["status_expected"]
        )
        if ok:
            passed += 1
        else:
            failed += 1
        rows.append(
            {
                "message_id": item["message_id"],
                "expected_category": item["category_expected"],
                "actual_category": decision.category,
                "expected_target_folder": item["target_folder_expected"],
                "actual_target_folder": decision.target_folder,
                "expected_status": item["status_expected"],
                "actual_status": decision.final_status.value,
                "route_source": route_source,
                "ok": ok,
            }
        )

    return {
        "summary": {
            "dataset": str(dataset_path),
            "total": len(rows),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden set regression runner for AI Mail Triage")
    parser.add_argument("dataset", help="Path to the golden set JSON file")
    args = parser.parse_args()

    payload = run_golden_set(Path(args.dataset))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["summary"]["failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
