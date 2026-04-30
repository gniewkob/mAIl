from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .rule_engine import NEWSLETTER_REGEX, OFFER_REGEX, SPAM_REGEX

_MIGRATION_NEWSLETTER_HINTS = (
    "okazj",
    "promocj",
    "wyprzeda",
    "powiadomienia@allegro.pl",
    "playpowiadomienia.pl",
)


def _migrate_category(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    category = str(record.get("category") or "").strip().lower()
    if category != "spam_or_offer":
        return record, False

    migrated = dict(record)
    target_folder = str(migrated.get("target_folder") or "").strip()
    if target_folder == "Junk":
        migrated["category"] = "spam"
    elif target_folder.endswith(".Newsletter") or target_folder == "Newsletter":
        migrated["category"] = "newsletter"
    elif target_folder.endswith(".Offer") or target_folder == "Offer":
        migrated["category"] = "offer"
    else:
        subject = str(migrated.get("subject") or "")
        sender = str(migrated.get("sender") or "")
        combined = f"{subject} {sender}".lower()
        if SPAM_REGEX.search(combined):
            migrated["category"] = "spam"
        elif (NEWSLETTER_REGEX.search(combined) or any(hint in combined for hint in _MIGRATION_NEWSLETTER_HINTS)) and not OFFER_REGEX.search(combined):
            migrated["category"] = "newsletter"
        else:
            migrated["category"] = "offer"
    return migrated, True


def run_migrate_audit_log(
    *,
    audit_log: Path,
    apply: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "audit_log": str(audit_log),
        "apply": apply,
        "backup_created": None,
        "records_seen": 0,
        "records_changed": 0,
        "parse_errors": 0,
    }
    if not audit_log.exists():
        payload["status"] = "missing"
        return payload

    original_lines = audit_log.read_text(encoding="utf-8").splitlines()
    rewritten_lines: list[str] = []

    for line in original_lines:
        if not line.strip():
            continue
        payload["records_seen"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            payload["parse_errors"] += 1
            rewritten_lines.append(line)
            continue
        if not isinstance(record, dict):
            rewritten_lines.append(line)
            continue
        migrated, changed = _migrate_category(record)
        if changed:
            payload["records_changed"] += 1
        rewritten_lines.append(json.dumps(migrated, ensure_ascii=False))

    payload["status"] = "ok"
    if not apply or payload["records_changed"] == 0:
        return payload

    backup_path = audit_log.with_suffix(audit_log.suffix + ".bak")
    if create_backup:
        backup_path.write_text(audit_log.read_text(encoding="utf-8"), encoding="utf-8")
        payload["backup_created"] = str(backup_path)

    new_content = "\n".join(rewritten_lines)
    if rewritten_lines:
        new_content += "\n"
    audit_log.write_text(new_content, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate historical audit log categories to the current model.")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--apply", action="store_true", help="Rewrite the audit log in place.")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a .bak backup before rewriting.",
    )
    args = parser.parse_args()

    payload = run_migrate_audit_log(
        audit_log=Path(args.audit_log),
        apply=args.apply,
        create_backup=not args.no_backup,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("status") == "missing":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
