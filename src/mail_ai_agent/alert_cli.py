from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import request

from .healthcheck_cli import build_health_payload


def build_alert_message(payload: dict[str, object], *, service_name: str) -> str:
    state = payload.get("state", {})
    issues = payload.get("issues", [])
    headline = "OK" if payload.get("ok") else "ALERT"
    lines = [
        f"[{headline}] {service_name}",
        (
            f"processed={state.get('processed', 0)} "
            f"uncertain={state.get('uncertain', 0)} "
            f"failed={state.get('failed', 0)} "
            f"cleanup_pending={state.get('cleanup_pending', 0)}"
        ),
    ]
    if issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in issues)
    return "\n".join(lines)


def send_webhook(*, webhook_url: str, message: str, payload: dict[str, object]) -> None:
    body = json.dumps({"text": message, "payload": payload}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        if response.status >= 400:
            raise RuntimeError(f"Webhook returned status {response.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Webhook alert wrapper for AI Mail Triage healthcheck")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--stdout-log", default=None, help="Optional stdout log path")
    parser.add_argument("--stderr-log", default=None, help="Optional stderr log path")
    parser.add_argument("--recent-audit-limit", type=int, default=50, help="How many recent audit records to inspect")
    parser.add_argument("--recent-audit-max-age-minutes", type=int, default=15, help="Only inspect audit records newer than this many minutes")
    parser.add_argument("--max-uncertain", type=int, default=0, help="Maximum tolerated uncertain rows in state")
    parser.add_argument("--service-name", default="mail-ai-prod", help="Service label used in the alert message")
    parser.add_argument("--webhook-url", default=None, help="Optional webhook URL")
    parser.add_argument("--send-on-ok", action="store_true", help="Also send webhook when healthcheck is green")
    args = parser.parse_args()

    payload = build_health_payload(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        stdout_log=Path(args.stdout_log) if args.stdout_log else None,
        stderr_log=Path(args.stderr_log) if args.stderr_log else None,
        recent_audit_limit=args.recent_audit_limit,
        recent_audit_max_age_minutes=args.recent_audit_max_age_minutes,
        max_uncertain=args.max_uncertain,
    )
    message = build_alert_message(payload, service_name=args.service_name)
    print(json.dumps({"message": message, "payload": payload}, ensure_ascii=False, indent=2))

    should_send = bool(args.webhook_url and (args.send_on_ok or not payload["ok"]))
    if should_send:
        send_webhook(webhook_url=args.webhook_url, message=message, payload=payload)

    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
