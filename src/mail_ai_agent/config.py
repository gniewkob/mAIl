from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_mailbox_id(imap_user: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", imap_user.strip().lower()).strip("_")
    return normalized or "mailbox_default"


def _normalize_imap_search_criterion(value: str) -> str:
    normalized = " ".join(value.strip().upper().split())
    allowed = {
        "ALL",
        "UNSEEN",
        "UNANSWERED",
        "FLAGGED",
        "UNSEEN UNANSWERED",
        "UNSEEN FLAGGED",
    }
    if normalized not in allowed:
        raise ValueError(f"Unsupported IMAP_SEARCH_CRITERION: {value}")
    return normalized


class MailboxConfig(BaseModel):
    mailbox_id: str
    imap_host: str
    imap_port: int = 993
    imap_user: str
    imap_pass: SecretStr
    imap_max_retries: int = 3
    imap_retry_backoff_seconds: float = 0.5
    imap_search_criterion: str = "ALL"
    imap_fetch_limit: int = 100
    imap_allow_folder_expunge: bool = False

    imap_source_folder: str = "INBOX.AI-Review"
    imap_uncertain_folder: str = "INBOX.AI-Uncertain"
    imap_appointments_folder: str = "INBOX.Appointments"
    imap_questions_folder: str = "INBOX.Questions"
    imap_complaints_folder: str = "INBOX.Complaints"
    imap_other_folder: str = "INBOX.Other"
    imap_billing_folder: str = "INBOX.Billing"
    imap_system_folder: str = "INBOX.System"

    billing_payment_email: str | None = None

    @field_validator("imap_search_criterion")
    @classmethod
    def validate_imap_search_criterion(cls, value: str) -> str:
        return _normalize_imap_search_criterion(value)

    @classmethod
    def from_settings(cls, settings: Settings) -> MailboxConfig:
        if not settings.imap_user or not settings.imap_pass or not settings.imap_host:
            raise ValueError("Single-mailbox mode requires IMAP_HOST, IMAP_USER, and IMAP_PASS.")
        return cls(
            mailbox_id=settings.default_mailbox_id(),
            imap_host=settings.imap_host,
            imap_port=settings.imap_port,
            imap_user=settings.imap_user,
            imap_pass=settings.imap_pass,
            imap_max_retries=settings.imap_max_retries,
            imap_retry_backoff_seconds=settings.imap_retry_backoff_seconds,
            imap_search_criterion=settings.imap_search_criterion,
            imap_fetch_limit=settings.imap_fetch_limit,
            imap_allow_folder_expunge=settings.imap_allow_folder_expunge,
            imap_source_folder=settings.imap_source_folder,
            imap_uncertain_folder=settings.imap_uncertain_folder,
            imap_appointments_folder=settings.imap_appointments_folder,
            imap_questions_folder=settings.imap_questions_folder,
            imap_complaints_folder=settings.imap_complaints_folder,
            imap_other_folder=settings.imap_other_folder,
            imap_billing_folder=settings.imap_billing_folder,
            imap_system_folder=settings.imap_system_folder,
            billing_payment_email=settings.imap_billing_payment_email,
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    imap_host: str | None = Field(default=None, alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_user: str | None = Field(default=None, alias="IMAP_USER")
    imap_pass: SecretStr | None = Field(default=None, alias="IMAP_PASS")
    imap_max_retries: int = Field(default=3, alias="IMAP_MAX_RETRIES")
    imap_retry_backoff_seconds: float = Field(default=0.5, alias="IMAP_RETRY_BACKOFF_SECONDS")
    imap_search_criterion: str = Field(default="ALL", alias="IMAP_SEARCH_CRITERION")
    imap_fetch_limit: int = Field(default=100, alias="IMAP_FETCH_LIMIT")
    imap_allow_folder_expunge: bool = Field(default=False, alias="IMAP_ALLOW_FOLDER_EXPUNGE")

    imap_source_folder: str = Field(default="INBOX.AI-Review", alias="IMAP_SOURCE_FOLDER")
    imap_uncertain_folder: str = Field(default="INBOX.AI-Uncertain", alias="IMAP_UNCERTAIN_FOLDER")
    imap_appointments_folder: str = Field(default="INBOX.Appointments", alias="IMAP_APPOINTMENTS_FOLDER")
    imap_questions_folder: str = Field(default="INBOX.Questions", alias="IMAP_QUESTIONS_FOLDER")
    imap_complaints_folder: str = Field(default="INBOX.Complaints", alias="IMAP_COMPLAINTS_FOLDER")
    imap_other_folder: str = Field(default="INBOX.Other", alias="IMAP_OTHER_FOLDER")
    imap_billing_folder: str = Field(default="INBOX.Billing", alias="IMAP_BILLING_FOLDER")
    imap_system_folder: str = Field(default="INBOX.System", alias="IMAP_SYSTEM_FOLDER")
    imap_billing_payment_email: str | None = Field(default=None, alias="IMAP_BILLING_PAYMENT_EMAIL")

    mailboxes_config_path: Path | None = Field(default=None, alias="MAILBOXES_CONFIG_PATH")

    ollama_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="qwen2.5:7b-instruct", alias="OLLAMA_MODEL")
    ollama_timeout_seconds: int = Field(default=60, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_temperature: float = Field(default=0.1, alias="OLLAMA_TEMPERATURE")

    move_confidence_threshold: float = Field(default=0.75, alias="MOVE_CONFIDENCE_THRESHOLD")
    flag_confidence_threshold: float = Field(default=0.80, alias="FLAG_CONFIDENCE_THRESHOLD")
    draft_confidence_threshold: float = Field(default=0.85, alias="DRAFT_CONFIDENCE_THRESHOLD")

    max_body_chars: int = Field(default=12000, alias="MAX_BODY_CHARS")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    processing_lease_seconds: int = Field(default=900, alias="PROCESSING_LEASE_SECONDS")
    llm_failure_route_to_uncertain: bool = Field(default=True, alias="LLM_FAILURE_ROUTE_TO_UNCERTAIN")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    dry_run: bool = Field(default=True, alias="DRY_RUN")

    state_db_path: Path = Field(default=Path("data/state.sqlite"), alias="STATE_DB_PATH")
    audit_log_path: Path = Field(default=Path("logs/audit.jsonl"), alias="AUDIT_LOG_PATH")
    draft_dir: Path = Field(default=Path("drafts/pending"), alias="DRAFT_DIR")
    worker_id: str = Field(default="mail-ai-worker-1", alias="WORKER_ID")
    audit_redact_pii: bool = Field(default=True, alias="AUDIT_REDACT_PII")
    state_redact_pii: bool = Field(default=True, alias="STATE_REDACT_PII")
    audit_fsync: bool = Field(default=True, alias="AUDIT_FSYNC")

    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_pass: SecretStr | None = Field(default=None, alias="SMTP_PASS")
    smtp_from: str | None = Field(default=None, alias="SMTP_FROM")
    admin_notify_email: str | None = Field(default=None, alias="ADMIN_NOTIFY_EMAIL")

    @field_validator("imap_search_criterion")
    @classmethod
    def validate_imap_search_criterion(cls, value: str) -> str:
        return _normalize_imap_search_criterion(value)

    @model_validator(mode="after")
    def validate_pii_flag_consistency(self) -> "Settings":
        if self.state_redact_pii and not self.audit_redact_pii:
            raise ValueError(
                "AUDIT_REDACT_PII must be True when STATE_REDACT_PII is True."
            )
        return self

    def default_mailbox_id(self) -> str:
        return _default_mailbox_id(self.imap_user or "default")

    def load_mailboxes(self) -> list[MailboxConfig]:
        if self.mailboxes_config_path:
            return self._load_mailboxes_from_manifest(self.mailboxes_config_path)
        return [MailboxConfig.from_settings(self)]

    def _load_mailboxes_from_manifest(self, manifest_path: Path) -> list[MailboxConfig]:
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Mailbox manifest not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            raw_mailboxes = payload.get("mailboxes", [])
        elif isinstance(payload, list):
            raw_mailboxes = payload
        else:
            raise ValueError("Mailbox manifest must be a list or an object with a 'mailboxes' key.")
        mailboxes = [self._normalize_mailbox(raw_mailbox) for raw_mailbox in raw_mailboxes]
        if not mailboxes:
            raise ValueError("Mailbox manifest is empty.")
        return mailboxes

    def _normalize_mailbox(self, raw_mailbox: dict[str, Any]) -> MailboxConfig:
        if "imap_user" not in raw_mailbox:
            raise ValueError("Each mailbox entry must include imap_user.")
        if "imap_pass" not in raw_mailbox and "imap_pass_ref" not in raw_mailbox:
            raise ValueError("Each mailbox entry must include imap_pass or imap_pass_ref.")
        mailbox_user = str(raw_mailbox["imap_user"])

        def _get(key: str, default: Any) -> Any:
            # Returns the manifest value when present and non-None.
            # Setting a key to null in JSON falls through to the Settings default
            # — intentional: null means "inherit from global settings".
            value = raw_mailbox.get(key)
            return value if value is not None else default

        merged = {
            "mailbox_id": raw_mailbox.get("mailbox_id") or _default_mailbox_id(mailbox_user),
            "imap_host": _get("imap_host", self.imap_host),
            "imap_port": _get("imap_port", self.imap_port),
            "imap_user": mailbox_user,
            "imap_pass": _resolve_mailbox_secret(raw_mailbox, mailbox_user),
            "imap_max_retries": _get("imap_max_retries", self.imap_max_retries),
            "imap_retry_backoff_seconds": _get("imap_retry_backoff_seconds", self.imap_retry_backoff_seconds),
            "imap_search_criterion": _get("imap_search_criterion", self.imap_search_criterion),
            "imap_fetch_limit": _get("imap_fetch_limit", self.imap_fetch_limit),
            "imap_allow_folder_expunge": raw_mailbox.get("imap_allow_folder_expunge", self.imap_allow_folder_expunge),
            "imap_source_folder": _get("imap_source_folder", self.imap_source_folder),
            "imap_uncertain_folder": _get("imap_uncertain_folder", self.imap_uncertain_folder),
            "imap_appointments_folder": _get("imap_appointments_folder", self.imap_appointments_folder),
            "imap_questions_folder": _get("imap_questions_folder", self.imap_questions_folder),
            "imap_complaints_folder": _get("imap_complaints_folder", self.imap_complaints_folder),
            "imap_other_folder": _get("imap_other_folder", self.imap_other_folder),
            "imap_billing_folder": _get("imap_billing_folder", self.imap_billing_folder),
            "imap_system_folder": _get("imap_system_folder", self.imap_system_folder),
            "billing_payment_email": _get("billing_payment_email", self.imap_billing_payment_email),
        }
        if not merged["imap_host"]:
            raise ValueError(f"Mailbox '{merged['mailbox_id']}' has no IMAP host configured.")
        return MailboxConfig.model_validate(merged)


def _resolve_mailbox_secret(raw_mailbox: dict[str, Any], mailbox_user: str) -> str:
    if "imap_pass" in raw_mailbox:
        return str(raw_mailbox["imap_pass"])

    ref = str(raw_mailbox["imap_pass_ref"]).strip()
    if not ref:
        raise ValueError(f"Mailbox '{mailbox_user}' has empty imap_pass_ref.")
    if ref.startswith("env:"):
        env_name = ref.split(":", 1)[1].strip()
        if not env_name:
            raise ValueError(f"Mailbox '{mailbox_user}' has invalid env secret reference.")
        secret = os.getenv(env_name)
        if not secret:
            raise ValueError(f"Mailbox '{mailbox_user}' secret env var '{env_name}' is not set.")
        return secret
    if ref.startswith("keychain:"):
        service, account = _parse_keychain_ref(ref, mailbox_user)
        return _read_keychain_secret(service, account, mailbox_user)
    raise ValueError(
        f"Mailbox '{mailbox_user}' uses unsupported imap_pass_ref '{ref}'. "
        "Supported formats: env:VAR_NAME, keychain:service/account, keychain:service:account"
    )


def _parse_keychain_ref(ref: str, mailbox_user: str) -> tuple[str, str]:
    payload = ref.split(":", 1)[1].strip()
    if "/" in payload:
        service, account = payload.split("/", 1)
    elif ":" in payload:
        service, account = payload.split(":", 1)
    else:
        service, account = payload, mailbox_user
    service = service.strip()
    account = account.strip()
    if not service or not account:
        raise ValueError(f"Mailbox '{mailbox_user}' has invalid keychain secret reference '{ref}'.")
    return service, account


def _read_keychain_secret(service: str, account: str, mailbox_user: str) -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service, "-a", account],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            f"Mailbox '{mailbox_user}' requests keychain secret '{service}/{account}', but the 'security' CLI is unavailable."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or "unknown error"
        raise ValueError(
            f"Mailbox '{mailbox_user}' keychain secret '{service}/{account}' could not be read: {stderr}"
        ) from exc

    secret = result.stdout.strip()
    if not secret:
        raise ValueError(f"Mailbox '{mailbox_user}' keychain secret '{service}/{account}' is empty.")
    return secret
