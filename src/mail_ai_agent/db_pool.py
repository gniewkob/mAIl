"""SQLite connection pool for efficient database access.

This module provides a simple connection pool for SQLite to avoid
creating a new connection for every operation.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from .constants import DEFAULT_SQLITE_BUSY_TIMEOUT_MS
from .utils import _chmod_owner_only


class ConnectionPool:
    """Thread-safe SQLite connection pool.
    
    Usage:
        pool = ConnectionPool(db_path, max_connections=5)
        
        with pool.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")
    """
    
    def __init__(
        self,
        db_path: Path,
        max_connections: int = 5,
        timeout: float = 30.0,
    ) -> None:
        """Initialize connection pool.
        
        Args:
            db_path: Path to SQLite database
            max_connections: Maximum number of connections in pool
            timeout: Connection timeout in seconds
        """
        self.db_path = db_path
        self.max_connections = max_connections
        self.timeout = timeout
        
        # Ensure parent directory exists with proper permissions
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.db_path.parent)
        
        # Thread-local storage for connections
        self._local = threading.local()
        
        # Pool of available connections (not thread-local)
        self._pool: list[sqlite3.Connection] = []
        self._pool_lock = threading.Lock()
        self._pool_size = 0
        
        # Track temporary connections (created when pool exhausted)
        # Stored as id(conn) -> True to handle connections without __dict__
        self._temporary_connections: set[int] = set()
        
        # Initialize database if needed
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize database with WAL mode and proper settings."""
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={DEFAULT_SQLITE_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
        
        _chmod_owner_only(self.db_path)
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.timeout,
            check_same_thread=False,  # We handle thread safety ourselves
        )
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get connection from pool or create new one."""
        # Check thread-local first
        if hasattr(self._local, 'connection') and self._local.connection:
            return self._local.connection
        
        # Try to get from pool
        with self._pool_lock:
            if self._pool:
                conn = self._pool.pop()
                self._local.connection = conn
                return conn
            
            # Create new connection if under limit
            if self._pool_size < self.max_connections:
                self._pool_size += 1
                conn = self._create_connection()
                self._local.connection = conn
                return conn
        
        # Pool exhausted - create temporary connection
        # This may exceed max_connections but ensures operation continues
        # Mark as temporary so _return_connection knows not to adjust _pool_size
        conn = self._create_connection()
        with self._pool_lock:
            self._temporary_connections.add(id(conn))
        return conn
    
    def _return_connection(self, conn: sqlite3.Connection) -> None:
        """Return connection to pool."""
        # Check if this is a temporary connection (created when pool was exhausted)
        with self._pool_lock:
            is_temporary = id(conn) in self._temporary_connections
            if is_temporary:
                self._temporary_connections.discard(id(conn))
        
        # Clear thread-local
        if hasattr(self._local, 'connection') and self._local.connection is conn:
            self._local.connection = None
        
        # Temporary connections are always closed, never returned to pool
        if is_temporary:
            try:
                conn.close()
            except Exception:
                pass
            return
        
        # Validate connection before returning to pool
        try:
            conn.execute("SELECT 1")
        except sqlite3.Error:
            # Connection is bad, close it
            try:
                conn.close()
            except Exception:
                pass
            with self._pool_lock:
                self._pool_size = max(0, self._pool_size - 1)
            return
        
        # Return to pool
        with self._pool_lock:
            if len(self._pool) < self.max_connections:
                self._pool.append(conn)
            else:
                # Pool full, close connection
                conn.close()
                self._pool_size -= 1
    
    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get connection from pool as context manager.
        
        Usage:
            with pool.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM table")
        """
        conn = self._get_connection()
        try:
            yield conn
        finally:
            self._return_connection(conn)
    
    def close_all(self) -> None:
        """Close all connections in pool."""
        with self._pool_lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()
            self._pool_size = 0
        
        # Clear thread-local
        if hasattr(self._local, 'connection'):
            if self._local.connection:
                try:
                    self._local.connection.close()
                except Exception:
                    pass
            self._local.connection = None
    
    def __enter__(self) -> ConnectionPool:
        return self
    
    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_all()


# Global pool registry to share pools across repositories
_pool_registry: dict[Path, ConnectionPool] = {}
_registry_lock = threading.Lock()


def get_pool(db_path: Path, max_connections: int = 5) -> ConnectionPool:
    """Get or create connection pool for database.
    
    Args:
        db_path: Path to SQLite database
        max_connections: Maximum connections per pool
        
    Returns:
        ConnectionPool instance
    """
    with _registry_lock:
        if db_path not in _pool_registry:
            _pool_registry[db_path] = ConnectionPool(db_path, max_connections)
        return _pool_registry[db_path]


def close_all_pools() -> None:
    """Close all connection pools. Useful for cleanup in tests."""
    global _pool_registry
    with _registry_lock:
        for pool in _pool_registry.values():
            pool.close_all()
        _pool_registry.clear()
