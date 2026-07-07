"""Async LLM Gateway for concurrent LLM calls.

This module provides async/await support for LLM operations, allowing
concurrent processing of multiple messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .circuit_breaker import CircuitBreakerOpenError
from .constants import (
    ActionTaken,
    DEFAULT_ASYNC_LLM_TIMEOUT,
    WorkflowStatus,
)
from .schemas import FinalDecision, LLMClassification

if TYPE_CHECKING:
    from .config import MailboxConfig, Settings
    from .schemas import ParsedEmail

LOGGER = logging.getLogger(__name__)


class AsyncLLMGateway:
    """Async wrapper for LLM operations with semaphore-based concurrency control.
    
    This class wraps the synchronous LLM gateway and provides async operations
    with configurable concurrency limits.
    
    Example:
        gateway = AsyncLLMGateway(settings, sync_llm_gateway)
        
        # Single classification
        result = await gateway.classify(parsed_email, mailbox)
        
        # Batch classification
        results = await gateway.classify_batch(
            [(parsed1, mailbox1), (parsed2, mailbox2)],
            max_concurrent=3
        )
    """
    
    def __init__(
        self,
        settings: Settings,
        llm_gateway,
        max_concurrent: int = 5,
    ) -> None:
        """Initialize async LLM gateway.
        
        Args:
            settings: Application settings
            llm_gateway: Synchronous LLM gateway to wrap
            max_concurrent: Maximum concurrent LLM calls
        """
        self.settings = settings
        self.llm = llm_gateway
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
    
    async def classify(
        self,
        parsed: ParsedEmail,
        mailbox: MailboxConfig,
    ) -> LLMClassification:
        """Classify a single email asynchronously.
        
        Args:
            parsed: Parsed email
            mailbox: Mailbox configuration
            
        Returns:
            LLM classification result
            
        Raises:
            CircuitBreakerOpenError: If circuit breaker is open
            Exception: If LLM call fails
        """
        async with self.semaphore:
            # Run synchronous LLM call in thread pool with timeout
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None,  # Use default executor
                    self._sync_classify,
                    parsed,
                    mailbox,
                ),
                timeout=DEFAULT_ASYNC_LLM_TIMEOUT,
            )
    
    def _sync_classify(
        self,
        parsed: ParsedEmail,
        mailbox: MailboxConfig,
    ) -> LLMClassification:
        """Synchronous wrapper for classification."""
        return self.llm.classify(parsed, mailbox)
    
    async def classify_batch(
        self,
        items: list[tuple[ParsedEmail, MailboxConfig]],
        max_concurrent: int | None = None,
    ) -> list[LLMClassification | Exception]:
        """Classify multiple emails concurrently.
        
        Args:
            items: List of (parsed_email, mailbox) tuples
            max_concurrent: Override default concurrency limit
            
        Returns:
            List of results (LLMClassification or Exception for each item)
            
        Example:
            results = await gateway.classify_batch([
                (parsed1, mailbox1),
                (parsed2, mailbox2),
                (parsed3, mailbox3),
            ])
            
            for (parsed, mailbox), result in zip(items, results):
                if isinstance(result, Exception):
                    LOGGER.error("Classification failed: %s", result)
                else:
                    LOGGER.info("Category: %s", result.category)
        """
        if max_concurrent is not None and max_concurrent != self.max_concurrent:
            # Create temporary semaphore with different limit
            semaphore = asyncio.Semaphore(max_concurrent)
        else:
            semaphore = self.semaphore
        
        # Create tasks for all items
        tasks = [
            self._classify_with_semaphore(semaphore, parsed, mailbox)
            for parsed, mailbox in items
        ]
        
        # Wait for all to complete
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _classify_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        parsed: ParsedEmail,
        mailbox: MailboxConfig,
    ) -> LLMClassification:
        """Classify with specific semaphore."""
        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._sync_classify,
                parsed,
                mailbox,
            )
    
    async def classify_with_timeout(
        self,
        parsed: ParsedEmail,
        mailbox: MailboxConfig,
        timeout_seconds: float = 30.0,
    ) -> LLMClassification:
        """Classify with timeout.
        
        Args:
            parsed: Parsed email
            mailbox: Mailbox configuration
            timeout_seconds: Maximum time to wait for classification
            
        Returns:
            LLM classification result
            
        Raises:
            asyncio.TimeoutError: If classification takes longer than timeout
            CircuitBreakerOpenError: If circuit breaker is open
        """
        return await asyncio.wait_for(
            self.classify(parsed, mailbox),
            timeout=timeout_seconds,
        )


class AsyncPipelineStage:
    """Async-enabled pipeline stage for classification.
    
    This stage can be used in the regular pipeline to enable async
    LLM classification.
    """
    
    name = "async_classify"
    
    def __init__(
        self,
        settings: Settings,
        async_llm: AsyncLLMGateway,
    ) -> None:
        self.settings = settings
        self.async_llm = async_llm
    
    async def process(self, context):
        """Async classification (for use in async pipelines)."""
        from .rule_engine import evaluate_rules
        from .decision_engine import decide_from_rule, decide_from_llm
        from time import perf_counter
        
        # Skip if lease not acquired
        if not context.is_lease_acquired:
            return context
        
        if context.parsed is None:
            raise RuntimeError("Cannot classify: email not parsed")
        
        try:
            # Evaluate rules
            rule = evaluate_rules(context.parsed, context.mailbox)
            context.rule_hit = rule.reason if rule.action != "needs_llm" else None
            
            if rule.action != "needs_llm":
                # Use rule decision
                context.decision = decide_from_rule(rule)
            else:
                # Async LLM call
                llm_start = perf_counter()
                classification = await self.async_llm.classify(
                    context.parsed, context.mailbox
                )
                context.llm_latency_ms = int((perf_counter() - llm_start) * 1000)
                context.decision = decide_from_llm(
                    classification, self.settings, context.mailbox
                )
            
            context.record_timing(self.name)
            
        except CircuitBreakerOpenError:
            LOGGER.warning("Circuit breaker open, routing to uncertain")
            context.decision = FinalDecision(
                category="other",
                priority="medium",
                confidence=0.0,
                target_folder=context.mailbox.imap_uncertain_folder,
                flags=[],
                final_status=WorkflowStatus.UNCERTAIN,
                action_taken=ActionTaken.ROUTE_UNCERTAIN,
                requires_reply=False,
                summary="Circuit breaker open - LLM unavailable",
                reasoning_short="Circuit breaker open",
            )
            context.record_timing(self.name)
            
        except Exception as exc:
            LOGGER.exception("Classification failed")
            context.classification_error = exc
        
        return context


# Utility functions for batch processing
async def process_batch_async(
    processor,
    candidates: list,
    mailbox,
    max_concurrent: int = 5,
) -> list:
    """Process multiple candidates concurrently.
    
    This is a higher-level utility for batch processing with full
    async support.
    
    Args:
        processor: MessageProcessorV2 instance
        candidates: List of CandidateMessage objects
        mailbox: Mailbox configuration
        max_concurrent: Maximum concurrent processing
        
    Returns:
        List of ProcessingResult objects
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_one(candidate):
        async with semaphore:
            # This would need to be made async in processor
            # For now, we run sync processor in thread
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                processor.process_candidate_simulate,
                candidate,
                mailbox,
            )
    
    tasks = [process_one(c) for c in candidates]
    return await asyncio.gather(*tasks)
