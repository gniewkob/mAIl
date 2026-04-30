"""Processing context and result types for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING

from ..constants import ActionTaken, WorkflowStatus

if TYPE_CHECKING:
    from ..config import MailboxConfig, Settings
    from ..schemas import CandidateMessage, FinalDecision, LeaseAcquireResult, ParsedEmail


@dataclass
class ProcessingContext:
    """Context object passed through pipeline stages.
    
    Carries all state needed for processing, allowing stages to be
    stateless and easily testable.
    """
    
    # Input
    candidate: "CandidateMessage"
    mailbox: "MailboxConfig"
    settings: "Settings"
    
    # Timing
    started_at: float = field(default_factory=perf_counter)
    stage_timings: dict[str, float] = field(default_factory=dict)
    
    # ParseStage outputs
    parsed: "ParsedEmail | None" = None
    fingerprint: str | None = None
    content_fingerprint: str | None = None
    parse_error: Exception | None = None
    
    # LeaseStage outputs
    lease: "LeaseAcquireResult | None" = None
    
    # ClassifyStage outputs
    decision: "FinalDecision | None" = None
    rule_hit: str | None = None
    llm_latency_ms: int | None = None
    classification_error: Exception | None = None
    
    # RouteStage outputs
    target_uid: str | None = None
    draft_path: str | None = None
    routing_error: Exception | None = None
    cleanup_pending: bool = False
    
    # Final result
    result: "ProcessingResult | None" = None
    
    def record_timing(self, stage_name: str) -> None:
        """Record timing for a completed stage."""
        self.stage_timings[stage_name] = perf_counter() - self.started_at
    
    @property
    def total_duration_ms(self) -> int:
        """Total processing duration in milliseconds."""
        return int((perf_counter() - self.started_at) * 1000)
    
    @property
    def is_parse_failed(self) -> bool:
        """True if parsing failed."""
        return self.parse_error is not None
    
    @property
    def is_lease_acquired(self) -> bool:
        """True if lease was successfully acquired."""
        return self.lease is not None and self.lease.outcome == "acquired"
    
    @property
    def is_classification_failed(self) -> bool:
        """True if classification failed."""
        return self.classification_error is not None
    
    @property
    def is_routing_failed(self) -> bool:
        """True if routing failed."""
        return self.routing_error is not None


@dataclass
class ProcessingResult:
    """Final result of message processing."""
    
    action_taken: ActionTaken | str
    final_status: WorkflowStatus
    category: str | None = None
    confidence: float | None = None
    target_folder: str | None = None
    draft_path: str | None = None
    latency_ms: int = 0
    error: str | None = None
    
    # Additional metadata for debugging
    stage_timings: dict[str, float] = field(default_factory=dict)
    rule_hit: str | None = None
    llm_latency_ms: int | None = None
