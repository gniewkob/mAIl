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


SINGLE_COPY_STRATEGIES = {"hybrid", "move"}


def _trusted_block(trusted_domains: tuple[str, ...], default_action: str) -> str:
    if not trusted_domains:
        return ""
    return f"""# 0) Trusted senders (e.g. booking system) always reach the worker, even if the server
#    spam filter scores them as spam (Booksy booking notifications score ~6.5 > 5.0).
if address :domain :is "from" {_sieve_list(trusted_domains)} {{
    {default_action}
    stop;
}}

"""


def _render_spam_gate(
    mailbox: MailboxConfig,
    policy: SievePolicy,
    spam_headers: str,
    default_action: str,
    trusted_domains: tuple[str, ...] = (),
) -> str:
    """Single-copy mailboxes: the server only diverts hard spam; mAIl classifies everything else.

    No `:copy` anywhere: a message lives in exactly one folder, so deleting it or moving it to Junk
    on any device is final. Keyword pre-filters (billing/system/newsletter) are deliberately NOT
    applied server-side: their substring matches ("ing", "log", "plus") are too loose to move mail
    out of INBOX unchecked; mAIl's rule engine + LLM does that with confidence thresholds.
    """
    return f"""require ["fileinto", "mailbox"];

# Unified Sieve policy for mAIl (generated) - single-copy mode ({mailbox.routing_strategy}).
# Server diverts only hard spam; mAIl classifies and files everything else (one copy per message).

{_trusted_block(trusted_domains, default_action)}# 1) Hard spam from server anti-spam headers -> Junk (moved, not copied)
if anyof (
    {spam_headers}
) {{
    fileinto "{mailbox.imap_spam_folder}";
    stop;
}}

# 2) Default: one shared worker entrypoint
{default_action}
"""


def trusted_domains_from(keep_senders: str) -> tuple[str, ...]:
    """'booksy, versum.pl' -> ('booksy.com', 'versum.pl'): bare names get '.com' only if no dot."""
    out = []
    for item in (keep_senders or "").split(","):
        item = item.strip().lower().lstrip("@")
        if item:
            out.append(item if "." in item else f"{item}.com")
    return tuple(out)


def _render(
    mailbox: MailboxConfig,
    policy: SievePolicy,
    strategy: str | None = None,
    trusted_domains: tuple[str, ...] = (),
) -> str:
    spam_headers = " or ".join(
        f'header :contains "{header}" {_sieve_list(policy.spam_levels)}' for header in policy.spam_headers
    )
    source_folder = mailbox.imap_source_folder.strip()
    if source_folder.upper() == "INBOX":
        default_action = "keep;"
    else:
        default_action = f'fileinto "{source_folder}";'
    if (strategy or mailbox.routing_strategy) in SINGLE_COPY_STRATEGIES:
        return _render_spam_gate(mailbox, policy, spam_headers, default_action, trusted_domains)
    return f"""require ["fileinto", "mailbox", "copy", "variables", "envelope", "imap4flags"];

# Unified Sieve policy for mAIl (generated).
# Purpose: keep prefilter deterministic and minimal, then hand off to AI worker.

# 1) Hard spam from server anti-spam headers
if anyof (
    {spam_headers}
) {{
    fileinto :copy "{mailbox.imap_spam_folder}";
    stop;
}}

# 2) Deterministic billing candidates (high precision only)
if anyof (
    header :contains "subject" {_sieve_list(policy.billing_subject_keywords)},
    address :contains ["from", "reply-to"] {_sieve_list(policy.billing_sender_keywords)}
) {{
    addflag "\\\\Flagged";
    fileinto :copy "{mailbox.imap_billing_folder}";
    stop;
}}

# 3) Deterministic system mail (high precision only)
if anyof (
    address :contains "from" {_sieve_list(policy.system_sender_keywords)},
    header :contains "subject" {_sieve_list(policy.system_subject_keywords)}
) {{
    fileinto :copy "{mailbox.imap_system_folder}";
    stop;
}}

# 4) Deterministic bulk/newsletter candidates
if anyof (
    exists "List-ID",
    header :contains "Precedence" {_sieve_list(policy.newsletter_precedence_keywords)},
    header :contains "subject" {_sieve_list(policy.newsletter_subject_keywords)}
) {{
    fileinto :copy "{mailbox.imap_newsletter_folder}";
    stop;
}}

# 5) Default: one shared worker entrypoint
{default_action}
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
        script = _render(
            mailbox, policy, mailbox.routing_strategy or settings.routing_strategy,
            trusted_domains_from(settings.automated_sender_keep),
        )
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
