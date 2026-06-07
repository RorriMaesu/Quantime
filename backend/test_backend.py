import sys
import os
import unittest
import time
import tempfile
import sqlite3
import json

# Add workspace directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import get_db_connection, init_db, CircuitBreaker, FirestoreThrottlingException
from backend.memory_store import SemanticMemoryStore
from backend.ollama_agent import create_task_dependency, resolve_dependencies, generate_agent_stream

class TestQuantimeBackend(unittest.TestCase):
    def setUp(self):
        # Create a temp database path for isolated unit testing
        self.db_fd, self.db_path = tempfile.mkstemp()
        init_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
            
        # Clean up temporary WAL files if they exist
        for suffix in ['-wal', '-shm']:
            p = self.db_path + suffix
            if os.path.exists(p):
                os.remove(p)

    def test_sqlite_wal_mode(self):
        """Verify SQLite is running in Write-Ahead Logging (WAL) mode for concurrency."""
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(busy_timeout, 5000)
        print("[OK] SQLite WAL mode and busy timeout verified successfully.")

    def test_circuit_breaker_throttling(self):
        """Verify the CircuitBreaker raises an error when writes exceed 5 per rolling 10 seconds."""
        cb = CircuitBreaker(capacity=5.0, fill_rate=0.5) # 5 max capacity, 1 refilled per 2 secs
        
        # Consume 5 times should succeed
        for _ in range(5):
            cb.consume()
            
        # 6th write attempt must immediately fail with FirestoreThrottlingException
        with self.assertRaises(FirestoreThrottlingException):
            cb.consume()
        print("[OK] Firestore Circuit Breaker token-bucket throttling verified successfully.")

    def test_dag_cycle_detection(self):
        """Verify DAG cycle detection blocks circular dependency loops."""
        # Insert 3 mock tasks in database
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        now = time.time()
        
        cursor.execute("""
            INSERT INTO tasks (id, title, start_time, end_time, created_at, updated_at)
            VALUES 
                ('A', 'Study Math', '2026-06-06T10:00:00Z', '2026-06-06T11:00:00Z', ?, ?),
                ('B', 'Read Physics', '2026-06-06T12:00:00Z', '2026-06-06T13:00:00Z', ?, ?),
                ('C', 'Lab Assignment', '2026-06-06T14:00:00Z', '2026-06-06T15:00:00Z', ?, ?)
        """, (now, now, now, now, now, now))
        conn.commit()
        conn.close()
        
        # Override get_db_connection globally for the test duration
        import backend.ollama_agent
        original_get_db = backend.ollama_agent.get_db_connection
        backend.ollama_agent.get_db_connection = lambda: get_db_connection(self.db_path)
        
        try:
            # Add valid dependencies: A depends on B, B depends on C (Order: C -> B -> A)
            create_task_dependency("A", "B")
            create_task_dependency("B", "C")
            
            # Check DAG resolve
            resolve_res = resolve_dependencies("A")
            self.assertEqual(resolve_res["status"], "ok")
            
            # Attemping to create circular dependency (C depends on A) must fail
            with self.assertRaises(ValueError) as context:
                create_task_dependency("C", "A")
            self.assertIn("CIRCULAR DEPENDENCY DEADLOCK", str(context.exception))
            print("[OK] Directed Acyclic Graph (DAG) cycle-detection algorithm verified successfully.")
            
        finally:
            # Restore db link
            backend.ollama_agent.get_db_connection = original_get_db

    def test_semantic_memory(self):
        """Verify semantic memory insertion and local-fallback search properties."""
        mem = SemanticMemoryStore(persist_dir="temp_chroma_db")
        
        # Insert test context
        doc_id = "test_doc_123"
        content = "Andrew J. Green has a university exam scheduled on next Tuesday morning."
        metadata = {"type": "schedule_alert", "user": "Andrew"}
        mem.add_interaction(doc_id, content, metadata)
        
        # Query matching memory
        results = mem.search_similar("When is Andrew's exam?", limit=1)
        self.assertTrue(len(results) > 0)
        self.assertIn("exam", results[0]["content"].lower())
        
        # Cleanup temporary chroma persistence if it created any files
        if os.path.exists("temp_chroma_db"):
            import shutil
            import gc
            del mem
            gc.collect()
            try:
                shutil.rmtree("temp_chroma_db")
            except PermissionError:
                # Windows file locking can prevent deletion until process exits
                pass
            
        print("[OK] ChromaDB/SQLite Semantic memory retrieval verified successfully.")

    def test_agent_stream_mock_decoding(self):
        """Verify the agent generator splits thought outputs and final response text."""
        import io
        import urllib.request
        
        # Simulate Ollama API output tokens containing thinking blocks
        mock_response_data = [
            json.dumps({"message": {"content": "<|channel>thought\nAnalyzing DAG schema..."}}),
            json.dumps({"message": {"content": "\nConflict validation checks complete.\n<|channel>"}}),
            json.dumps({"message": {"content": "text\nSchedule modified."}})
        ]
        
        # Encode response stream
        mock_stream = io.BytesIO("\n".join(mock_response_data).encode("utf-8"))
        
        class MockHttpResponse:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
            def __iter__(self):
                return self.stream.__iter__()
            def read(self):
                return self.stream.read()
                
        original_urlopen = urllib.request.urlopen
        # Force local mock connection handler for test isolation
        urllib.request.urlopen = lambda req, *args, **kwargs: MockHttpResponse(mock_stream)
        
        try:
            stream = generate_agent_stream("Reschedule my lecture")
            chunks = list(stream)
            self.assertTrue(len(chunks) > 0)
            
            has_thought = any(channel == "thought" for channel, text in chunks)
            has_text = any(channel == "text" for channel, text in chunks)
            
            self.assertTrue(has_thought)
            self.assertTrue(has_text)
            print("[OK] Deep Reasoning speculative decoding parser verified successfully.")
        finally:
            urllib.request.urlopen = original_urlopen

if __name__ == "__main__":
    unittest.main()
