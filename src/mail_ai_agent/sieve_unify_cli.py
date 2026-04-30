from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .config import MailboxConfig, Settings


@dataclass(frozen=True)
class SievePolicy:
    spam_headers: tuple[str, ...]
    spam_levels: tuple[str, ...]
    billing_subject_keywords: tuple[str, ...]
    billing_sender_keywords: tuple[str, ...]
    system_sender_keywords: tuple[str, ...]
    system_subject_keywords: tuple[str, ...]
    newsletter_subject_keywords: tuple[str, ...]
    newsletter_precedence_keywords: tuple[str, ...]


DEFAULT_POLICY = SievePolicy(
    spam_headers=("X-Spam-Flag",),
    spam_levels=("YES", "*****"),
    billing_subject_keywords=(
        "faktura",
        "fv",
        "proforma",
        "rachunek",
        "oplata",
        "platnosc",
        "payment",
        "invoice",
    ),
    billing_sender_keywords=(
        "faktury",
        "ksiegowosc",
        "billing",
        "platnosci",
        "oplaty",
    ),
    system_sender_keywords=(
        "no-reply",
        "noreply",
        "system",
        "notification",
        "alert",
        "security",
        "postmaster",
        "mailer-daemon",
    ),
    system_subject_keywords=(
        "powiadomienie",
        "alert",
        "status",
        "security",
        "failure",
        "delivery status notification",
    ),
    newsletter_subject_keywords=(
        "newsletter",
        "promocja",
        "rabat",
        "wyprzedaz",
        "black friday",
        "cyber monday",
    ),
    newsletter_precedence_keywords=("bulk", "junk", "list"),
)


def _sieve_list(values: tuple[str, ...]) -> str:
    quoted = [f'"{value}"' for value in values]
    return "[{}]".format(", ".join(quoted))


def _render(mailbox: MailboxConfig, policy: SievePolicy) -> str:
    spam_headers = " or ".join(
        f'header :contains "{header}" {_sieve_list(policy.spam_levels)}' for header in policy.spam_headers
    )
    return f"""require ["fileinto", "mailbox", "copy", "variables", "envelope", "imap4flags"];

# Unified Sieve policy for mAIl (generated).
# Purpose: keep prefilter deterministic and minimal, then hand off to AI worker.

# 1) Hard spam from server anti-spam headers
if anyof (
    {spam_headers}
) {{
    fileinto "{mailbox.imap_spam_folder}";
    stop;
}}

# 2) Deterministic billing candidates (high precision only)
if anyof (
    header :contains "subject" {_sieve_list(policy.billing_subject_keywords)},
    address :contains ["from", "reply-to"] {_sieve_list(policy.billing_sender_keywords)}
) {{
    addflag "\\\\Flagged";
    fileinto "{mailbox.imap_billing_folder}";
    stop;
}}

# 3) Deterministic system mail (high precision only)
if anyof (
    address :contains "from" {_sieve_list(policy.system_sender_keywords)},
    header :contains "subject" {_sieve_list(policy.system_subject_keywords)}
) {{
    fileinto "{mailbox.imap_system_folder}";
    stop;
}}

# 4) Deterministic bulk/newsletter candidates
if anyof (
    exists "List-ID",
    header :contains "Precedence" {_sieve_list(policy.newsletter_precedence_keywords)},
    header :contains "subject" {_sieve_list(policy.newsletter_subject_keywords)}
) {{
    fileinto "{mailbox.imap_newsletter_folder}";
    stop;
}}

# 5) Default: one shared worker entrypoint
fileinto "{mailbox.imap_source_folder}";
"""


def _load_policy(path: Path | None) -> SievePolicy:
    if path is None:
        return DEFAULT_POLICY
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SievePolicy(
        spam_headers=tuple(payload.get("spam_headers", DEFAULT_POLICY.spam_headers)),
        spam_levels=tuple(payload.get("spam_levels", DEFAULT_POLICY.spam_levels)),
        billing_subject_keywords=tuple(payload.get("billing_subject_keywords", DEFAULT_POLICY.billing_subject_keywords)),
        billing_sender_keywords=tuple(payload.get("billing_sender_keywords", DEFAULT_POLICY.billing_sender_keywords)),
        system_sender_keywords=tuple(payload.get("system_sender_keywords", DEFAULT_POLICY.system_sender_keywords)),
        system_subject_keywords=tuple(payload.get("system_subject_keywords", DEFAULT_POLICY.system_subject_keywords)),
        newsletter_subject_keywords=tuple(
            payload.get("newsletter_subject_keywords", DEFAULT_POLICY.newsletter_subject_keywords)
        ),
        newsletter_precedence_keywords=tuple(
            payload.get("newsletter_precedence_keywords", DEFAULT_POLICY.newsletter_precedence_keywords)
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one unified Sieve policy for all configured mailboxes."
    )
    parser.add_argument("--env-file", default=None, help="Optional env file path.")
    parser.add_argument(
        "--policy-json",
        default=None,
        help="Optional JSON policy override file.",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/sieve-unified",
        help="Directory where generated .sieve files are written.",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    policy = _load_policy(Path(args.policy_json)) if args.policy_json else DEFAULT_POLICY
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, str]] = []
    for mailbox in settings.load_mailboxes():
        script = _render(mailbox, policy)
        output_path = output_dir / f"{mailbox.mailbox_id}.main.sieve"
        output_path.write_text(script, encoding="utf-8")
        summary.append(
            {
                "mailbox_id": mailbox.mailbox_id,
                "path": str(output_path),
                "source_folder": mailbox.imap_source_folder,
            }
        )

    print(json.dumps({"generated": summary, "count": len(summary)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
