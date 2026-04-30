"""Tests for db_pool resource leak fix."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mail_ai_agent.db_pool import ConnectionPool


class TestConnectionPoolResourceLeak:
    """Test that temporary connections don't cause resource leaks."""

    def _is_temporary(self, pool, conn):
        """Helper to check if connection is temporary."""
        return id(conn) in pool._temporary_connections

    def test_temporary_connection_marked(self, tmp_path):
        """Connections created when pool exhausted are marked as temporary."""
        db_path = tmp_path / "test.db"
        pool = ConnectionPool(db_path, max_connections=1)
        
        # Use threading to exhaust the pool from different threads
        connections = []
        
        def get_connection():
            conn = pool._get_connection()
            connections.append(conn)
        
        # First thread gets the pool connection
        thread1 = threading.Thread(target=get_connection)
        thread1.start()
        thread1.join()
        
        # Second thread should get a temporary connection
        thread2 = threading.Thread(target=get_connection)
        thread2.start()
        thread2.join()
        
        # The second connection should be temporary
        assert len(connections) == 2
        assert self._is_temporary(pool, connections[1]) is True
        assert self._is_temporary(pool, connections[0]) is False
        
        # Clean up
        for conn in connections:
            pool._return_connection(conn)

    def test_pool_size_not_affected_by_temporary(self, tmp_path):
        """Temporary connections don't affect _pool_size tracking."""
        db_path = tmp_path / "test.db"
        pool = ConnectionPool(db_path, max_connections=1)
        
        initial_size = pool._pool_size
        
        # Create and return many connections in sequence
        for i in range(5):
            with pool.connection() as conn:
                pass  # Just use and return
        
        # Pool size should be reasonable (not negative, not huge)
        assert 0 <= pool._pool_size <= pool.max_connections

    def test_pool_size_never_negative(self, tmp_path):
        """Pool size never goes negative even with many temporary connections."""
        db_path = tmp_path / "test.db"
        pool = ConnectionPool(db_path, max_connections=1)
        
        # Create and close many connections
        for _ in range(20):
            with pool.connection() as conn:
                pass
        
        assert pool._pool_size >= 0
        
    def test_temporary_connection_closed_not_returned_to_pool(self, tmp_path):
        """Temporary connections are closed, not returned to pool."""
        db_path = tmp_path / "test.db"
        pool = ConnectionPool(db_path, max_connections=1)
        
        # First exhaust the pool from main thread
        with pool.connection() as main_conn:
            # Then get another connection from different thread
            temp_conn = None
            
            def get_temp_conn():
                nonlocal temp_conn
                temp_conn = pool._get_connection()
            
            thread = threading.Thread(target=get_temp_conn)
            thread.start()
            thread.join()
            
            # temp_conn should be temporary
            assert self._is_temporary(pool, temp_conn) is True
            
            # Return it
            pool._return_connection(temp_conn)
            
            # Should no longer be in temporary set
            assert self._is_temporary(pool, temp_conn) is False
            
            # Pool should still have only 1 connection max
            assert len(pool._pool) <= 1
