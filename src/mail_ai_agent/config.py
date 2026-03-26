from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_mailbox_id(imap_user: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", imap_user.strip().lower()).strip("_")
    return normalized or "mailbox_default"


class MailboxConfig(BaseModel):
    mailbox_id: str
    imap_host: str
    imap_port: int = 993
    imap_user: str
    imap_pass: SecretStr

    imap_source_folder: str = "INBOX.AI-Review"
    imap_uncertain_folder: str = "INBOX.AI-Uncertain"
    imap_appointments_folder: str = "INBOX.Appointments"
    imap_questions_folder: str = "INBOX.Questions"
    imap_complaints_folder: str = "INBOX.Complaints"
    imap_other_folder: str = "INBOX.Other"
    imap_billing_folder: str = "INBOX.Billing"
    imap_system_folder: str = "INBOX.System"

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
            imap_source_folder=settings.imap_source_folder,
            imap_uncertain_folder=settings.imap_uncertain_folder,
            imap_appointments_folder=settings.imap_appointments_folder,
            imap_questions_folder=settings.imap_questions_folder,
            imap_complaints_folder=settings.imap_complaints_folder,
            imap_other_folder=settings.imap_other_folder,
            imap_billing_folder=settings.imap_billing_folder,
            imap_system_folder=settings.imap_system_folder,
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

    imap_source_folder: str = Field(default="INBOX.AI-Review", alias="IMAP_SOURCE_FOLDER")
    imap_uncertain_folder: str = Field(default="INBOX.AI-Uncertain", alias="IMAP_UNCERTAIN_FOLDER")
    imap_appointments_folder: str = Field(default="INBOX.Appointments", alias="IMAP_APPOINTMENTS_FOLDER")
    imap_questions_folder: str = Field(default="INBOX.Questions", alias="IMAP_QUESTIONS_FOLDER")
    imap_complaints_folder: str = Field(default="INBOX.Complaints", alias="IMAP_COMPLAINTS_FOLDER")
    imap_other_folder: str = Field(default="INBOX.Other", alias="IMAP_OTHER_FOLDER")
    imap_billing_folder: str = Field(default="INBOX.Billing", alias="IMAP_BILLING_FOLDER")
    imap_system_folder: str = Field(default="INBOX.System", alias="IMAP_SYSTEM_FOLDER")

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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    dry_run: bool = Field(default=True, alias="DRY_RUN")

    state_db_path: Path = Field(default=Path("data/state.sqlite"), alias="STATE_DB_PATH")
    audit_log_path: Path = Field(default=Path("logs/audit.jsonl"), alias="AUDIT_LOG_PATH")
    draft_dir: Path = Field(default=Path("drafts/pending"), alias="DRAFT_DIR")
    worker_id: str = Field(default="mail-ai-worker-1", alias="WORKER_ID")

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
        if "imap_user" not in raw_mailbox or "imap_pass" not in raw_mailbox:
            raise ValueError("Each mailbox entry must include imap_user and imap_pass.")
        mailbox_user = str(raw_mailbox["imap_user"])
        merged = {
            "mailbox_id": raw_mailbox.get("mailbox_id") or _default_mailbox_id(mailbox_user),
            "imap_host": raw_mailbox.get("imap_host") or self.imap_host,
            "imap_port": raw_mailbox.get("imap_port") or self.imap_port,
            "imap_user": mailbox_user,
            "imap_pass": raw_mailbox["imap_pass"],
            "imap_source_folder": raw_mailbox.get("imap_source_folder") or self.imap_source_folder,
            "imap_uncertain_folder": raw_mailbox.get("imap_uncertain_folder") or self.imap_uncertain_folder,
            "imap_appointments_folder": raw_mailbox.get("imap_appointments_folder") or self.imap_appointments_folder,
            "imap_questions_folder": raw_mailbox.get("imap_questions_folder") or self.imap_questions_folder,
            "imap_complaints_folder": raw_mailbox.get("imap_complaints_folder") or self.imap_complaints_folder,
            "imap_other_folder": raw_mailbox.get("imap_other_folder") or self.imap_other_folder,
            "imap_billing_folder": raw_mailbox.get("imap_billing_folder") or self.imap_billing_folder,
            "imap_system_folder": raw_mailbox.get("imap_system_folder") or self.imap_system_folder,
        }
        if not merged["imap_host"]:
            raise ValueError(f"Mailbox '{merged['mailbox_id']}' has no IMAP host configured.")
        return MailboxConfig.model_validate(merged)
