from __future__ import annotations
import sys

import argparse
import json
import os
import shlex
from pathlib import Path


def _normalize_secret_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.upper()).strip("_") or "DEFAULT"


def _load_manifest(path: Path) -> tuple[dict | list, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload, payload.get("mailboxes", [])
    if isinstance(payload, list):
        return payload, payload
    raise ValueError("Mailbox manifest must be a list or an object with a 'mailboxes' key.")


def _write_manifest(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate plaintext IMAP secrets in a mailbox manifest to secret references")
    parser.add_argument("--input", required=True, help="Path to source mailbox manifest")
    parser.add_argument("--output", required=True, help="Path to migrated mailbox manifest")
    parser.add_argument("--mode", choices=["env", "keychain"], default="env", help="Secret reference mode to generate")
    parser.add_argument("--sidecar-output", default=None, help="Optional file for env exports or keychain import commands")
    parser.add_argument("--service", default="mail-ai", help="Keychain service name when --mode=keychain")
    parser.add_argument(
        "--allow-stdout-secrets",
        action="store_true",
        default=False,
        help="Allow printing secrets to stdout (use --sidecar-output instead)",
    )
    args = parser.parse_args()

    payload, mailboxes = _load_manifest(Path(args.input))
    if not mailboxes:
        raise SystemExit("Manifest has no mailboxes to migrate.")

    sidecar_lines: list[str] = []
    for mailbox in mailboxes:
        if "imap_pass_ref" in mailbox:
            continue
        if "imap_pass" not in mailbox:
            continue

        secret = str(mailbox["imap_pass"])
        mailbox_id = str(mailbox.get("mailbox_id") or mailbox.get("imap_user") or "default")
        if args.mode == "env":
            env_name = f"MAILBOX_SECRET_{_normalize_secret_name(mailbox_id)}"
            mailbox["imap_pass_ref"] = f"env:{env_name}"
            sidecar_lines.append(f"export {env_name}={shlex.quote(secret)}")
        else:
            account = str(mailbox.get("imap_user") or mailbox_id)
            mailbox["imap_pass_ref"] = f"keychain:{args.service}/{account}"
            sidecar_lines.append(
                "security add-generic-password -U "
                f"-s {shlex.quote(args.service)} -a {shlex.quote(account)} -w {shlex.quote(secret)}"
            )
        del mailbox["imap_pass"]

    _write_manifest(Path(args.output), payload)

    if args.sidecar_output:
        sidecar_path = Path(args.sidecar_output)
        sidecar_path.write_text(
            "\n".join(sidecar_lines) + ("\n" if sidecar_lines else ""), encoding="utf-8"
        )
        try:
            os.chmod(sidecar_path, 0o600)
        except OSError:
            pass
    elif sidecar_lines:
        if not args.allow_stdout_secrets:
            raise SystemExit(
                "[ERROR] Secrets would be printed to stdout. "
                "Use --sidecar-output <file> (recommended) or pass --allow-stdout-secrets to opt in."
            )
        print(
            "[WARN] Printing secrets to stdout. Use --sidecar-output <file> instead.",
            file=sys.stderr,
        )
        print("\n".join(sidecar_lines))


if __name__ == "__main__":
    main()
