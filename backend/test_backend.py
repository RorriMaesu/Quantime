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

    def test_agent_recursive_tool_calls(self):
        """Verify the agent recursive tool loop handles sequential tool execution."""
        import io
        import urllib.request
        
        # Turn 1: Respond with a tool call to 'get_current_schedule'
        turn1_response = [
            json.dumps({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_current_schedule",
                            "arguments": {"start_date": "2026-06-07T00:00:00Z", "end_date": "2026-06-07T23:59:59Z"}
                        }
                    }]
                }
            })
        ]
        
        # Turn 2: After tool execution, respond with text completion
        turn2_response = [
            json.dumps({
                "message": {
                    "role": "assistant",
                    "content": "Schedule is clear. Scheduled successfully."
                }
            })
        ]
        
        request_count = 0
        
        class MockHttpResponse:
            def __init__(self, data_list):
                self.stream = io.BytesIO("\n".join(data_list).encode("utf-8"))
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
            def __iter__(self):
                return self.stream.__iter__()
            def read(self):
                return self.stream.read()
                
        def mock_urlopen(req, *args, **kwargs):
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return MockHttpResponse(turn1_response)
            else:
                return MockHttpResponse(turn2_response)
                
        original_urlopen = urllib.request.urlopen
        urllib.request.urlopen = mock_urlopen
        
        try:
            stream = generate_agent_stream("schedule a block for today at 2")
            chunks = list(stream)
            
            self.assertEqual(request_count, 2)
            
            has_tool_trigger = any("[Agent Triggered Tool: get_current_schedule" in text for channel, text in chunks)
            has_execution_success = any("[Tool Execution Success]" in text for channel, text in chunks)
            has_final_text = any("Scheduled successfully" in text for channel, text in chunks)
            
            self.assertTrue(has_tool_trigger)
            self.assertTrue(has_execution_success)
            self.assertTrue(has_final_text)
            print("[OK] Agent recursive tool-calling loop verified successfully.")
            
        finally:
            urllib.request.urlopen = original_urlopen

    def test_chat_history_retention_and_decay(self):
        """Verify get_recent_chat_history maps roles correctly, respects decay window and exclusions."""
        from backend.app import get_recent_chat_history, update_chat_record
        import backend.app
        
        original_get_db = backend.app.get_db_connection
        backend.app.get_db_connection = lambda: get_db_connection(self.db_path)
        
        try:
            # 1. Insert chat message from 3 hours ago (should be excluded)
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            three_hours_ago = time.time() - 10800
            cursor.execute(
                "INSERT INTO chats (id, sender, text, thoughts, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                ("chat_old", "user", "I want pizza", "", "done", three_hours_ago)
            )
            
            # 2. Insert chat messages from 10 minutes ago (should be included)
            ten_mins_ago = time.time() - 600
            cursor.execute(
                "INSERT INTO chats (id, sender, text, thoughts, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                ("chat_user_1", "user", "schedule study time", "", "done", ten_mins_ago)
            )
            cursor.execute(
                "INSERT INTO chats (id, sender, text, thoughts, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                ("chat_agent_1", "agent", "how long?", "", "done", ten_mins_ago + 10)
            )
            
            # 3. Insert current active message that is currently pending/processing
            cursor.execute(
                "INSERT INTO chats (id, sender, text, thoughts, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                ("chat_active", "user", "1 hour", "", "pending", time.time())
            )
            conn.commit()
            conn.close()
            
            # Query history
            history = get_recent_chat_history(limit=5)
            
            # Verify "I want pizza" (old) is excluded, pending is excluded
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[0]["content"], "schedule study time")
            self.assertEqual(history[1]["role"], "assistant")
            self.assertEqual(history[1]["content"], "how long?")
            
            # Query with exclusion (excluding user chat 1)
            history_excl = get_recent_chat_history(limit=5, exclude_chat_id="chat_user_1")
            self.assertEqual(len(history_excl), 1)
            self.assertEqual(history_excl[0]["role"], "assistant")
            self.assertEqual(history_excl[0]["content"], "how long?")
            print("[OK] Chat history retention, session decay, and exclusions verified successfully.")
            
        finally:
            backend.app.get_db_connection = original_get_db

    def test_recurring_tasks_local_generation(self):
        """Verify recurring tasks are generated correctly for local offline fallback."""
        from backend.app import create_task, TaskSchema
        import backend.app
        
        original_get_db = backend.app.get_db_connection
        backend.app.get_db_connection = lambda: get_db_connection(self.db_path)
        
        try:
            task = TaskSchema(
                id="task_test_rec_123",
                title="Weekly Synced Meeting",
                description="Sync meeting description",
                start_time="2026-06-08T10:00:00Z",
                end_time="2026-06-08T11:00:00Z",
                energy_level="none",
                constraint_type="soft",
                status="pending",
                recurrence_pattern="weekly",
                recurrence_count=3
            )
            
            res = create_task(task)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["tasks_created"], 3)
            
            # Query db directly to verify 3 tasks were created
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, start_time, end_time, recurrence_group_id FROM tasks ORDER BY start_time ASC")
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            
            self.assertEqual(len(rows), 3)
            # Verify weekly interval
            self.assertEqual(rows[0]["start_time"], "2026-06-08T10:00:00Z")
            self.assertEqual(rows[1]["start_time"], "2026-06-15T10:00:00Z")
            self.assertEqual(rows[2]["start_time"], "2026-06-22T10:00:00Z")
            
            # Verify they all share the same recurrence group ID
            group_id = rows[0]["recurrence_group_id"]
            self.assertTrue(group_id.startswith("rec_"))
            self.assertEqual(rows[1]["recurrence_group_id"], group_id)
            self.assertEqual(rows[2]["recurrence_group_id"], group_id)
            
            # Test deleting series
            from backend.app import delete_task_endpoint
            del_res = delete_task_endpoint(rows[0]["id"], target="series")
            self.assertEqual(del_res["status"], "success")
            
            # Verify database is empty
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks")
            count = cursor.fetchone()[0]
            conn.close()
            self.assertEqual(count, 0)
            print("[OK] Local recurring task generation and series deletion verified successfully.")
            
        finally:
            backend.app.get_db_connection = original_get_db

    def test_api_security_middleware(self):
        """Verify API security middleware restricts remote hosts and bypasses local host requests."""
        import asyncio
        from fastapi.testclient import TestClient
        from backend.app import app
        import backend.app
        
        original_get_db = backend.app.get_db_connection
        backend.app.get_db_connection = lambda: get_db_connection(self.db_path)
        
        try:
            # Seed API key in temp DB
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('api_key', 'test_secret_key_123')")
            conn.commit()
            conn.close()
            
            client = TestClient(app)
            
            # 1. Local Request (TestClient defaults to localhost/testclient)
            # Should bypass auth and return status success
            resp = client.get("/api/setup/status")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json().get("api_key"), "test_secret_key_123")
            
            # 2. Remote Request (Simulated by mock request parameters)
            class MockClient:
                def __init__(self, host):
                    self.host = host
            class MockRequest:
                def __init__(self, path, host, headers=None):
                    self.url = self
                    self.path = path
                    self.client = MockClient(host)
                    self.headers = headers or {}
                    
            async def call_next(req):
                from fastapi.responses import Response
                return Response(status_code=200)
                
            from backend.app import security_middleware
            
            # Test local request via middleware directly
            req_local = MockRequest("/api/tasks", "127.0.0.1")
            res_local = asyncio.run(security_middleware(req_local, call_next))
            self.assertEqual(res_local.status_code, 200)
            
            # Test remote request without key
            req_remote_fail = MockRequest("/api/tasks", "192.168.1.50")
            res_remote_fail = asyncio.run(security_middleware(req_remote_fail, call_next))
            self.assertEqual(res_remote_fail.status_code, 401)
            
            # Test remote request with wrong key
            req_remote_wrong = MockRequest("/api/tasks", "192.168.1.50", headers={"X-API-Key": "wrong"})
            res_remote_wrong = asyncio.run(security_middleware(req_remote_wrong, call_next))
            self.assertEqual(res_remote_wrong.status_code, 401)
            
            # Test remote request with correct key
            req_remote_ok = MockRequest("/api/tasks", "192.168.1.50", headers={"X-API-Key": "test_secret_key_123"})
            res_remote_ok = asyncio.run(security_middleware(req_remote_ok, call_next))
            self.assertEqual(res_remote_ok.status_code, 200)
            print("[OK] Remote authorization checks and localhost security bypass verified successfully.")
            
        finally:
            backend.app.get_db_connection = original_get_db
 
if __name__ == "__main__":
    unittest.main()
