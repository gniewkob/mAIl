"""Base protocols and types for pipeline stages."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import ProcessingContext


@runtime_checkable
class Stage(Protocol):
    """Protocol for a pipeline stage."""
    
    name: str
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Process the context and return updated context.
        
        Stages should be pure functions - they don't modify the input context
        in place but return a new (or updated) context.
        """
        ...


class StageError(Exception):
    """Error raised by a pipeline stage."""
    
    def __init__(self, stage_name: str, message: str, cause: Exception | None = None) -> None:
        self.stage_name = stage_name
        self.cause = cause
        super().__init__(f"[{stage_name}] {message}")
