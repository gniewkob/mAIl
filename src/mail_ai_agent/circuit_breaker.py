from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreaker:
    """Circuit breaker pattern implementation for external service calls.
    
    - CLOSED: Normal operation, requests pass through
    - OPEN: After failure threshold, requests fail immediately  
    - HALF_OPEN: After timeout, single request allowed to test recovery
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        timeout_seconds: int = 300,
        expected_exception: type[Exception] = Exception,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.expected_exception = expected_exception
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.RLock()  # Reentrant lock to allow nested calls
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state
    
    def call(self, func: Callable[[], T]) -> T:
        """Execute function with circuit breaker protection."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                else:
                    remaining = self._seconds_until_reset()
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Try again in {remaining:.0f}s."
                    )
        
        try:
            result = func()
            self._on_success()
            return result
        except self.expected_exception as exc:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        # Called with lock held
        if self._last_failure_time is None:
            return True
        return (time.time() - self._last_failure_time) >= self.timeout_seconds
    
    def _seconds_until_reset(self) -> float:
        # Called with lock held
        if self._last_failure_time is None:
            return 0.0
        remaining = self.timeout_seconds - (time.time() - self._last_failure_time)
        return max(0.0, remaining)
    
    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Service recovered
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_failure_time = None
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # Recovery failed, go back to open
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                # Threshold reached, open circuit
                self._state = CircuitState.OPEN


class CircuitBreakerOpenError(RuntimeError):
    """Raised when circuit breaker is open and request is rejected."""
    pass
