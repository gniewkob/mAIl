from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowStatus(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    PROCESSED = "processed"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    SKIPPED = "skipped"


class AttachmentMeta(BaseModel):
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None


class ParsedEmail(BaseModel):
    message_id: str | None = None
    sender: str
    reply_to: str | None = None
    to: str | None = None
    date: datetime | None = None
    subject: str
    plain_text_body: str = ""
    html_body: str | None = None
    normalized_body: str = ""
    attachment_metadata: list[AttachmentMeta] = Field(default_factory=list)

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachment_metadata)


class LLMEntities(BaseModel):
    customer_name: str | None = None
    phone: str | None = None
    requested_date: str | None = None
    service: str | None = None


class LLMClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: Literal["appointment", "question", "complaint", "spam_or_offer", "other"]
    priority: Literal["high", "medium", "low"]
    requires_reply: bool
    confidence: float
    summary: str
    entities: LLMEntities = Field(default_factory=LLMEntities)
    draft_reply: str | None = None
    reasoning_short: str

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @field_validator("summary", "reasoning_short")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be empty")
        return value


class RuleDecision(BaseModel):
    category: str
    target_folder: str
    action: Literal["skip_ai", "route", "needs_llm"]
    requires_flag: bool = False
    reason: str


class FinalDecision(BaseModel):
    category: str
    priority: str | None = None
    confidence: float | None = None
    target_folder: str
    flags: list[str] = Field(default_factory=list)
    final_status: WorkflowStatus
    action_taken: str
    requires_reply: bool = False
    summary: str | None = None
    reasoning_short: str | None = None
    draft_reply: str | None = None


class EmailRecord(BaseModel):
    id: int
    mailbox_id: str
    message_id: str | None = None
    fingerprint: str
    imap_uid: str | None = None
    uidvalidity: str | None = None
    source_folder: str | None = None
    target_folder: str | None = None
    target_uid: str | None = None
    sender: str
    subject: str
    internaldate: str | None = None
    status: WorkflowStatus
    category: str | None = None
    confidence: float | None = None
    action_taken: str | None = None
    draft_path: str | None = None
    error_message: str | None = None
    processing_started_at: str | None = None
    lock_expires_at: str | None = None
    lock_owner: str | None = None
    attempt_count: int = 0
    last_error_at: str | None = None
    last_error_type: str | None = None
    rule_hit: str | None = None
    model_name: str | None = None
    model_latency_ms: int | None = None
    created_at: str
    updated_at: str


class LeaseAcquireResult(BaseModel):
    outcome: Literal["acquired", "locked", "already_done", "conflict"]
    record: EmailRecord | None = None
    reason: str


class WorkerLockResult(BaseModel):
    acquired: bool
    lock_owner: str | None = None
    reason: str


class CandidateMessage(BaseModel):
    uid: str
    internaldate: str | None = None
    message_id: str | None = None
    raw_bytes: bytes


class MailboxProcessingReport(BaseModel):
    mailbox_id: str
    mailbox_user: str
    candidates_seen: int = 0
    acquired: int = 0
    processed: int = 0
    uncertain: int = 0
    failed: int = 0
    skipped: int = 0
    conflicts: int = 0


class ProcessingReport(BaseModel):
    worker_id: str
    dry_run: bool
    candidates_seen: int = 0
    acquired: int = 0
    processed: int = 0
    uncertain: int = 0
    failed: int = 0
    skipped: int = 0
    conflicts: int = 0
    worker_lock_denied: bool = False
    mailboxes_processed: int = 0
    mailbox_reports: list[MailboxProcessingReport] = Field(default_factory=list)
