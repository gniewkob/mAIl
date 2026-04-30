"""Application-wide constants and enums."""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    """Email processing workflow states."""

    NEW = "new"
    PROCESSING = "processing"
    PROCESSED = "processed"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    CLEANUP_PENDING = "cleanup_pending"
    SKIPPED = "skipped"


class ActionTaken(StrEnum):
    """Actions performed during email processing."""

    # Routing actions
    ROUTE_REPLY = "route_reply"
    ROUTE_UNCERTAIN = "route_uncertain"
    ROUTE_FROM_LLM = "route_from_llm"
    ROUTE_ARCHIVE = "route_archive"
    ROUTE_NEEDS_REPLY = "route_needs_reply"
    SKIP_AI = "skip_ai"

    # Move-prefixed routing actions (production)
    MOVE_ROUTE_REPLY = "move_route_reply"
    MOVE_ROUTE_UNCERTAIN = "move_route_uncertain"
    MOVE_ROUTE_FROM_LLM = "move_route_from_llm"
    MOVE_ROUTE_ARCHIVE = "move_route_archive"
    MOVE_ROUTE_NEEDS_REPLY = "move_route_needs_reply"
    MOVE_ROUTE_UNCERTAIN_LLM_FAILURE = "move_route_uncertain_llm_failure"
    MOVE_SKIP_AI = "move_skip_ai"

    # Skip actions
    SKIP_ALREADY_DONE = "skip_already_done"
    SKIP_DUPLICATE = "skip_duplicate"
    SKIP_LEASE_CONFLICT = "skip_lease_conflict"
    SKIP_LOCKED = "skip_locked"
    SKIP_CONFLICT = "skip_conflict"

    # Cleanup actions
    CLEANUP_COMPLETED = "cleanup_completed"
    CLEANUP_FAILED = "cleanup_failed"
    CLEANUP_SOURCE_DELETED = "cleanup_source_deleted"
    CLEANUP_UIDVALIDITY_MISMATCH = "cleanup_uidvalidity_mismatch"
    CLEANUP_SOURCE = "cleanup_source"
    CLEANUP_SOURCE_ALREADY_DONE = "cleanup_source_already_done"
    CLEANUP_SOURCE_ALREADY_DONE_MISSING = "cleanup_source_already_done_missing"
    CLEANUP_SOURCE_ALREADY_DONE_FAILED = "cleanup_source_already_done_failed"
    CLEANUP_SOURCE_CONFLICT_DUPLICATE = "cleanup_source_conflict_duplicate"
    MOVE_COPY_SUCCEEDED_CLEANUP_PENDING = "move_copy_succeeded_cleanup_pending"

    # Parse failure actions
    MOVE_ROUTE_UNCERTAIN_PARSE_FAILURE = "move_route_uncertain_parse_failure"

    # Error actions
    FAILED = "failed"
    FAILED_PARSE = "failed_parse"
    FAILED_CLASSIFY = "failed_classify"
    FAILED_ROUTE = "failed_route"

    # Dry run
    SIMULATE_ROUTE_REPLY = "simulate_route_reply"
    SIMULATE_ROUTE_UNCERTAIN = "simulate_route_uncertain"
    SIMULATE_ROUTE_FROM_LLM = "simulate_route_from_llm"
    SIMULATE_ROUTE_ARCHIVE = "simulate_route_archive"
    SIMULATE_ROUTE_NEEDS_REPLY = "simulate_route_needs_reply"
    SIMULATE_ROUTE_UNCERTAIN_LLM_FAILURE = "simulate_route_uncertain_llm_failure"
    SIMULATE_FAILED = "simulate_failed"

    # Test/legacy actions (for backward compatibility)
    TEST = "test"
    MOVE_QUESTION = "move_question"

    # Admin actions
    ADMIN_REQUEUE_UNCERTAIN = "admin_requeue_uncertain"
    ADMIN_DELETE_MESSAGE = "admin_delete_message"

    # System actions
    WORKER_LOCK_DENIED = "worker_lock_denied"
    IMAP_AUTH_FAILED = "imap_auth_failed"
    MAILBOX_FAILED = "mailbox_failed"


class LeaseOutcome(StrEnum):
    """Possible outcomes of lease acquisition."""

    ACQUIRED = "acquired"
    CONFLICT = "conflict"
    UNCERTAIN = "uncertain"


class IdentityConflictType(StrEnum):
    """Types of identity conflicts detected."""

    DIFFERENT_CONTENT = "different_content_same_id"
    SAME_CONTENT_DIFFERENT_ID = "same_content_different_id"


class ErrorType(StrEnum):
    """Classification of error types."""

    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    LLM_ERROR = "llm_error"
    IMAP_ERROR = "imap_error"
    PARSE_ERROR = "parse_error"
    CLASSIFICATION_ERROR = "classification_error"
    ROUTING_ERROR = "routing_error"


# ============================================================================
# Timeouts (in seconds)
# ============================================================================

DEFAULT_SQLITE_TIMEOUT = 30.0
DEFAULT_ASYNC_LLM_TIMEOUT = 60.0
DEFAULT_WEBHOOK_TIMEOUT = 10.0
DEFAULT_CLEANUP_LOCK_TIMEOUT = 300  # 5 minutes

# ============================================================================
# Database
# ============================================================================

DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000  # 5 seconds
DEFAULT_MAX_CONNECTIONS = 5

# ============================================================================
# IMAP
# ============================================================================

IMAP_FETCH_WARNING_THRESHOLD = 500

# ============================================================================
# Email Processing
# ============================================================================

EMAIL_BODY_FINGERPRINT_LIMIT = 1000  # Characters

# ============================================================================
# Circuit Breaker Defaults
# ============================================================================

DEFAULT_CIRCUIT_BREAKER_TIMEOUT = 300  # 5 minutes
DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3

# ============================================================================
# Legacy / Backward Compatibility
# ============================================================================

MOVE_CLEANUP_PENDING_ACTION = ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING
