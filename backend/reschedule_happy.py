import sqlite3
import os
import sys

# Connect to DB
db_path = os.path.join(os.path.dirname(__file__), 'quantime.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Update date in SQLite
cursor.execute("""
    UPDATE tasks 
    SET start_time = '2026-06-07T10:08:00-07:00', end_time = '2026-06-07T10:17:00-07:00' 
    WHERE title = 'happy time session'
""")
conn.commit()
print(f"Updated {cursor.rowcount} rows in SQLite.")
conn.close()

# Update Firestore if active
try:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from backend.app import db_firestore, MOCK_USER_ID
    if db_firestore is not None:
        task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document("task_1780851614")
        task_ref.update({
            "start_time": "2026-06-07T10:08:00-07:00",
            "end_time": "2026-06-07T10:17:00-07:00"
        })
        print("Updated Firestore successfully.")
except Exception as e:
    print(f"Firestore update bypassed/failed: {e}")
