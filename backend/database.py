# backend/database.py
import os
import time
import sqlite3
import threading
from typing import Dict, Any, List, Optional

_data_dir = os.path.join(os.path.expanduser("~"), ".quantime")
os.makedirs(_data_dir, exist_ok=True)
DB_FILE = os.path.abspath(os.path.join(_data_dir, "quantime.db"))

# Migration helper: copy existing database to new location if it exists
_local_db = os.path.abspath(os.path.join(os.path.dirname(__file__), "quantime.db"))
if not os.path.exists(DB_FILE) and os.path.exists(_local_db):
    import shutil
    try:
        shutil.copy(_local_db, DB_FILE)
    except Exception:
        pass

class FirestoreThrottlingException(Exception):
    """Raised when Firestore write rates exceed limits to preserve the free Spark tier."""
    pass

class CircuitBreaker:
    """
    Thread-safe Token Bucket Circuit Breaker for Firestore writes.
    Max 5 writes per rolling 10 seconds.
    Capacity = 5. Fill Rate = 0.5 tokens per second.
    """
    def __init__(self, capacity: float = 5.0, fill_rate: float = 0.5):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.time()
        self.lock = threading.Lock()

    def consume(self) -> None:
        """
        Consumes a token. If no tokens are available, raises FirestoreThrottlingException.
        """
        with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            
            # Refill tokens based on time elapsed
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            
            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                raise FirestoreThrottlingException(
                    f"Firestore write rate-limiting triggered! (Available tokens: {self.tokens:.2f}/5.0). "
                    "Aborting transaction to protect Firebase Spark tier daily limits."
                )

# Global Circuit Breaker instance
circuit_breaker = CircuitBreaker()

def get_db_connection(db_path: str = DB_FILE) -> sqlite3.Connection:
    """
    Establishes a connection to the SQLite database.
    Enforces Write-Ahead Logging (WAL) and 5000ms busy timeout for multi-threaded concurrency.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Concurrency optimizations
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db(db_path: str = DB_FILE) -> None:
    """
    Initializes SQLite schema tables.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # 1. Tasks Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        energy_level TEXT CHECK(energy_level IN ('crimson', 'teal', 'none')) DEFAULT 'none',
        constraint_type TEXT CHECK(constraint_type IN ('hard', 'soft')) DEFAULT 'soft',
        status TEXT CHECK(status IN ('pending', 'completed')) DEFAULT 'pending',
        source_event_id TEXT,
        recurrence_group_id TEXT,
        recurrence_rule TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """)
    
    # Migrations for existing databases
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN recurrence_group_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN recurrence_rule TEXT")
    except sqlite3.OperationalError:
        pass
    
    # 2. Task Dependencies Table (to model Directed Acyclic Graph)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id TEXT NOT NULL,
        depends_on_task_id TEXT NOT NULL,
        PRIMARY KEY (task_id, depends_on_task_id),
        FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
        FOREIGN KEY (depends_on_task_id) REFERENCES tasks (id) ON DELETE CASCADE
    )
    """)
    
    # 3. User Profiles Table (configuration)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_profiles (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)
    
    # 4. State Snapshots Table (recovery/history logs)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS state_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        state_data TEXT NOT NULL,
        timestamp REAL NOT NULL
    )
    """)
    
    # 5. Task Cache Table (for LLM context assembly optimization)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        expires_at REAL NOT NULL
    )
    """)
    
    # 6. Chats Table for local chat persistence
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        sender TEXT NOT NULL CHECK(sender IN ('user', 'agent')),
        text TEXT,
        thoughts TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'failed')) DEFAULT 'pending',
        timestamp REAL NOT NULL
    )
    """)

    # 7. Proposed Schedules Staging Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proposed_schedules (
        transaction_id TEXT NOT NULL,
        option_id TEXT NOT NULL,
        description TEXT NOT NULL,
        proposed_changes TEXT NOT NULL,
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (transaction_id, option_id)
    )
    """)

    # 8. Circadian Profiles Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS circadian_profiles (
        key TEXT PRIMARY KEY,
        start_hour INTEGER NOT NULL,
        end_hour INTEGER NOT NULL,
        efficiency_type TEXT CHECK(efficiency_type IN ('peak', 'downtime')) NOT NULL
    )
    """)
    
    # 9. Push Subscriptions Staging Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id TEXT PRIMARY KEY,
        subscription_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )
    """)

    # 10. Sent Notifications Audit Log Table (Tracking per-device subscription deliveries)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sent_notifications (
        subscription_hash TEXT NOT NULL DEFAULT 'global',
        task_id TEXT NOT NULL,
        start_time TEXT NOT NULL,
        alert_type TEXT NOT NULL,
        fired_at REAL NOT NULL,
        PRIMARY KEY (subscription_hash, task_id, start_time, alert_type)
    )
    """)

    # 10b. Processed Emails Audit Log Table (To track email notifications processed by AI)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS processed_emails (
        email_id TEXT PRIMARY KEY,
        processed_at REAL NOT NULL,
        is_important INTEGER NOT NULL
    )
    """)
    
    # Seed default circadian peak hours if empty
    cursor.execute("SELECT COUNT(*) FROM circadian_profiles")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO circadian_profiles (key, start_hour, end_hour, efficiency_type) VALUES ('morning_peak', 9, 12, 'peak')")
        cursor.execute("INSERT INTO circadian_profiles (key, start_hour, end_hour, efficiency_type) VALUES ('evening_peak', 15, 18, 'peak')")
        cursor.execute("INSERT INTO circadian_profiles (key, start_hour, end_hour, efficiency_type) VALUES ('afternoon_slump', 13, 15, 'downtime')")
        
    # 11. Seed Default Profiles (if empty)
    cursor.execute("SELECT COUNT(*) FROM user_profiles WHERE key = 'user_id'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('user_id', 'user')")
        cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('user_name', 'User')")
        
    # Seed default reminder preferences if missing
    cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('notifications_enabled', 'true')")
    cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('notification_lead_minutes', '15')")
    cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('notification_on_start', 'true')")
    cursor.execute("INSERT OR IGNORE INTO user_profiles (key, value) VALUES ('notification_dnd_focus', 'true')")
    
    # Seed a persistent unique tunnel subdomain if missing
    cursor.execute("SELECT COUNT(*) FROM user_profiles WHERE key = 'tunnel_subdomain'")
    if cursor.fetchone()[0] == 0:
        import uuid
        unique_sub = f"quantime-{uuid.uuid4().hex[:8]}"
        cursor.execute("INSERT INTO user_profiles (key, value) VALUES ('tunnel_subdomain', ?)", (unique_sub,))
        
    # Seed a persistent API key for Localtunnel authentication if missing
    cursor.execute("SELECT COUNT(*) FROM user_profiles WHERE key = 'api_key'")
    if cursor.fetchone()[0] == 0:
        import secrets
        secure_key = secrets.token_hex(32)
        cursor.execute("INSERT INTO user_profiles (key, value) VALUES ('api_key', ?)", (secure_key,))
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
