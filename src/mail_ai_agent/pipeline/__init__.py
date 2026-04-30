"""Processing pipeline for email triage.

The pipeline breaks down message processing into discrete, composable stages:
- ParseStage: Parse raw email bytes
- LeaseStage: Acquire processing lease
- ClassifyStage: Rule evaluation and LLM classification
- RouteStage: IMAP routing operations
- AuditStage: Audit logging
"""

from .context import ProcessingContext, ProcessingResult
from .pipeline import ProcessingPipeline
from .stages import (
    AuditStage,
    ClassifyStage,
    LeaseStage,
    ParseStage,
    RouteStage,
)

__all__ = [
    "ProcessingContext",
    "ProcessingResult",
    "ProcessingPipeline",
    "ParseStage",
    "LeaseStage",
    "ClassifyStage",
    "RouteStage",
    "AuditStage",
]
