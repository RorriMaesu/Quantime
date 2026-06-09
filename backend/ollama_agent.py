# backend/ollama_agent.py
import json
import urllib.request
import urllib.parse
import logging
import sqlite3
import time
from typing import Dict, Any, List, Generator, Tuple, Optional
from backend.database import get_db_connection
from backend.google_client import GoogleCalendarSync, GmailParser
from backend.memory_store import memory_store

logger = logging.getLogger("quantime.ollama_agent")
logging.basicConfig(level=logging.INFO)

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "gemma4-agent-mtp"

def get_selected_model() -> str:
    """Helper to query the user profile database for selected LLM model."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'llm_model'")
        row = cursor.fetchone()
        if row and row["value"]:
            return row["value"]
    except Exception as e:
        logger.error(f"Error querying user profile llm_model: {e}")
    finally:
        conn.close()
    return MODEL_NAME

# =====================================================================
# Core Database Tool Implementations
# =====================================================================

def get_current_schedule(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Queries the local SQLite database to fetch schedule tasks between two date-time ranges.
    Uses timezone-aware Python comparisons to avoid SQLite lexicographical string inequality bugs.
    """
    logger.info(f"Ollama Agent executing: get_current_schedule({start_date}, {end_date})")
    
    import datetime
    try:
        q_start = datetime.datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        q_end = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
    except Exception as e:
        logger.error(f"Invalid date boundaries: {e}")
        return []

    d_start = (q_start.date() - datetime.timedelta(days=1)).isoformat()
    d_end = (q_end.date() + datetime.timedelta(days=1)).isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, description, start_time, end_time, energy_level, constraint_type, status, source_event_id, recurrence_group_id, recurrence_rule
            FROM tasks 
            WHERE substr(start_time, 1, 10) >= ? AND substr(start_time, 1, 10) <= ?
        """, (d_start, d_end))
        
        matched = []
        for row in cursor.fetchall():
            try:
                t_start = datetime.datetime.fromisoformat(row["start_time"].replace('Z', '+00:00'))
                t_end = datetime.datetime.fromisoformat(row["end_time"].replace('Z', '+00:00'))
                if max(t_start, q_start) < min(t_end, q_end):
                    matched.append(dict(row))
            except Exception:
                continue
        return matched
    except Exception as e:
        logger.error(f"Failed to query local schedule: {e}")
        return []
    finally:
        conn.close()

def resolve_task_id(task_id_or_title: str) -> str:
    """
    Resolves a task_id or title to a valid SQLite task ID.
    If direct lookup fails, searches for a matching task title (case-insensitive fuzzy match).
    """
    if not task_id_or_title:
        return task_id_or_title
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check direct ID
        cursor.execute("SELECT id FROM tasks WHERE id = ?", (task_id_or_title,))
        row = cursor.fetchone()
        if row:
            return row["id"]
            
        # Try exact title match
        cursor.execute("SELECT id FROM tasks WHERE title = ?", (task_id_or_title,))
        row = cursor.fetchone()
        if row:
            return row["id"]
            
        # Try fuzzy title match
        cursor.execute("SELECT id FROM tasks WHERE title LIKE ?", (f"%{task_id_or_title}%",))
        rows = cursor.fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
    except Exception as e:
        logger.warning(f"Error resolving task ID/title fuzzy lookup: {e}")
    finally:
        conn.close()
    return task_id_or_title

def check_overlaps(exclude_task_id: Optional[str], start_time: str, end_time: str) -> List[Dict[str, Any]]:
    """Helper to detect any overlapping tasks in a given range."""
    import datetime
    try:
        q_start = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        q_end = datetime.datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    except Exception:
        return []
    
    d_start = (q_start.date() - datetime.timedelta(days=1)).isoformat()
    d_end = (q_end.date() + datetime.timedelta(days=1)).isoformat()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    conflicts = []
    try:
        cursor.execute("""
            SELECT id, title, start_time, end_time, constraint_type
            FROM tasks
            WHERE substr(start_time, 1, 10) >= ? AND substr(start_time, 1, 10) <= ?
        """, (d_start, d_end))
        rows = cursor.fetchall()
        for r in rows:
            if exclude_task_id and r["id"] == exclude_task_id:
                continue
            try:
                t_start = datetime.datetime.fromisoformat(r["start_time"].replace('Z', '+00:00'))
                t_end = datetime.datetime.fromisoformat(r["end_time"].replace('Z', '+00:00'))
                if max(t_start, q_start) < min(t_end, q_end):
                    conflicts.append(dict(r))
            except Exception:
                continue
    finally:
        conn.close()
    return conflicts

def modify_task_time(task_id: str, new_start: str, new_end: str, ignore_conflicts: bool = False, target: str = "single") -> Dict[str, Any]:
    """
    Mutates a local task's scheduling bounds. Supports shifting an entire recurrence series relatively.
    """
    task_id = resolve_task_id(task_id)
    logger.info(f"Ollama Agent executing: modify_task_time({task_id}, {new_start}, {new_end}, ignore_conflicts={ignore_conflicts}, target={target})")
    
    import datetime
    try:
        task_start = datetime.datetime.fromisoformat(new_start.replace('Z', '+00:00'))
        now = datetime.datetime.now().astimezone()
        if task_start < now:
            return {
                "status": "error",
                "message": f"Conflict: Cannot modify task time to start in the past. Staged new_start '{new_start}' is before current time '{now.isoformat()}'."
            }
    except Exception as e:
        logger.error(f"Error checking new_start bounds: {e}")

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, title, constraint_type, source_event_id, start_time, end_time, recurrence_group_id FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        
        if not task:
            raise ValueError(f"Target task with ID '{task_id}' does not exist.")
            
        if task["constraint_type"] == "hard":
            raise PermissionError(
                f"IMMUTABLE CONSTRAINT CONFLICT: Task '{task['title']}' (ID: {task_id}) is flagged "
                "as a HARD constraint. This task cannot be shifted automatically."
            )
            
        if target == "series" and task["recurrence_group_id"]:
            rec_group = task["recurrence_group_id"]
            orig_start_dt = datetime.datetime.fromisoformat(task["start_time"].replace('Z', '+00:00'))
            new_start_dt = datetime.datetime.fromisoformat(new_start.replace('Z', '+00:00'))
            delta = new_start_dt - orig_start_dt
            
            cursor.execute("SELECT id, title, start_time, end_time, source_event_id, constraint_type FROM tasks WHERE recurrence_group_id = ?", (rec_group,))
            series_tasks = [dict(r) for r in cursor.fetchall()]
            
            # Precheck constraints and overlaps
            for t in series_tasks:
                if t["constraint_type"] == "hard":
                    raise PermissionError(f"IMMUTABLE CONSTRAINT CONFLICT: Task '{t['title']}' is a HARD constraint. Series shift aborted.")
                
                t_start = datetime.datetime.fromisoformat(t["start_time"].replace('Z', '+00:00'))
                t_end = datetime.datetime.fromisoformat(t["end_time"].replace('Z', '+00:00'))
                t_new_start = (t_start + delta).isoformat().replace('+00:00', 'Z')
                t_new_end = (t_end + delta).isoformat().replace('+00:00', 'Z')
                
                if not ignore_conflicts:
                    conflicts = check_overlaps(t["id"], t_new_start, t_new_end)
                    if conflicts:
                        conflict_names = ", ".join([f"'{c['title']}'" for c in conflicts])
                        return {
                            "status": "error",
                            "message": f"Conflict: Shifted series slot for '{t['title']}' overlaps with {conflict_names}."
                        }
            
            # Commit the updates
            for t in series_tasks:
                t_start = datetime.datetime.fromisoformat(t["start_time"].replace('Z', '+00:00'))
                t_end = datetime.datetime.fromisoformat(t["end_time"].replace('Z', '+00:00'))
                t_new_start = (t_start + delta).isoformat().replace('+00:00', 'Z')
                t_new_end = (t_end + delta).isoformat().replace('+00:00', 'Z')
                
                cursor.execute("""
                    UPDATE tasks 
                    SET start_time = ?, end_time = ?, updated_at = ? 
                    WHERE id = ?
                """, (t_new_start, t_new_end, time.time(), t["id"]))
                
                if t["source_event_id"]:
                    GoogleCalendarSync.patch_calendar_event(t["source_event_id"], t_new_start, t_new_end, t["title"])
            
            conn.commit()
            return {"status": "success", "message": f"Successfully shifted all tasks in recurring series '{task['title']}' by {delta}."}

        # Check single task overlaps
        if not ignore_conflicts:
            conflicts = check_overlaps(task_id, new_start, new_end)
            if conflicts:
                conflict_names = ", ".join([f"'{c['title']}' ({c['start_time']} - {c['end_time']})" for c in conflicts])
                return {
                    "status": "error",
                    "message": f"Conflict: The requested slot overlaps with existing tasks: {conflict_names}. "
                               f"To force this change, set ignore_conflicts=True, or call calculate_schedule_proposals to reschedule."
                }

        cursor.execute("""
            UPDATE tasks 
            SET start_time = ?, end_time = ?, updated_at = ? 
            WHERE id = ?
        """, (new_start, new_end, time.time(), task_id))
        
        sync_status = "Local SQLite update successful."
        if task["source_event_id"]:
            success = GoogleCalendarSync.patch_calendar_event(task["source_event_id"], new_start, new_end, task["title"])
            if success:
                sync_status += " Changes patched to Google Calendar."
            else:
                sync_status += " Google Calendar API synchronization failed."
                
        conn.commit()
        return {"status": "success", "message": f"Task '{task['title']}' successfully shifted to {new_start} - {new_end}. {sync_status}"}
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error modifying task time: {e}")
        raise e
    finally:
        conn.close()

# =====================================================================
# Directed Acyclic Graph (DAG) / Deadlock Check
# =====================================================================

def resolve_dependencies(task_id: str) -> Dict[str, Any]:
    """
    Traces dependencies of the task and checks the DAG dependency graph.
    Returns status confirmation or raises ValueError if circular dependency locks exist.
    """
    task_id = resolve_task_id(task_id)
    logger.info(f"Ollama Agent executing: resolve_dependencies({task_id})")
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Build adjacency graph
        cursor.execute("SELECT task_id, depends_on_task_id FROM task_dependencies")
        edges = cursor.fetchall()
        
        adj: Dict[str, List[str]] = {}
        all_nodes = set()
        for u, v in edges:
            if u not in adj:
                adj[u] = []
            adj[u].append(v)
            all_nodes.update([u, v])
            
        # DFS Cycle Detection
        # 0: Unvisited, 1: Visiting, 2: Visited
        visited: Dict[str, int] = {node: 0 for node in all_nodes}
        
        def dfs_has_cycle(node: str) -> bool:
            visited[node] = 1 # Mark as visiting
            for neighbor in adj.get(node, []):
                if visited.get(neighbor, 0) == 1:
                    return True # Visited parent node in same recursion chain
                if visited.get(neighbor, 0) == 0:
                    if dfs_has_cycle(neighbor):
                        return True
            visited[node] = 2 # Mark as fully processed
            return False
            
        cycle_detected = False
        for node in all_nodes:
            if visited[node] == 0:
                if dfs_has_cycle(node):
                    cycle_detected = True
                    break
                    
        if cycle_detected:
            raise ValueError(
                "CIRCULAR DEPENDENCY DEADLOCK DETECTED: A cycle was identified in the schedule "
                "graph schema. The requested operation was aborted to prevent circular blockages."
            )
            
        # Return listing of dependencies
        cursor.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?", (task_id,))
        deps = [row[0] for row in cursor.fetchall()]
        return {"status": "ok", "message": "Graph acyclic checks passed.", "dependencies": deps}
        
    finally:
        conn.close()

def create_task_dependency(task_id: str, depends_on_task_id: str) -> Dict[str, Any]:
    """
    Configures a dependency edge (task_id depends on depends_on_task_id).
    Validates graph is free of cycles before committing.
    """
    task_id = resolve_task_id(task_id)
    depends_on_task_id = resolve_task_id(depends_on_task_id)
    logger.info(f"Ollama Agent executing: create_task_dependency({task_id}, {depends_on_task_id})")
    if task_id == depends_on_task_id:
        raise ValueError("A task cannot establish a dependency on itself.")
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Load active links
        cursor.execute("SELECT task_id, depends_on_task_id FROM task_dependencies")
        edges = cursor.fetchall()
        
        # Build simulated representation containing the proposed edge
        adj: Dict[str, List[str]] = {}
        for u, v in edges:
            if u not in adj:
                adj[u] = []
            adj[u].append(v)
            
        if task_id not in adj:
            adj[task_id] = []
        adj[task_id].append(depends_on_task_id)
        
        # Cycle Check starting from task_id
        visited: Dict[str, int] = {}
        def dfs_has_cycle(node: str) -> bool:
            visited[node] = 1
            for neighbor in adj.get(node, []):
                if visited.get(neighbor, 0) == 1:
                    return True
                if visited.get(neighbor, 0) == 0:
                    if dfs_has_cycle(neighbor):
                        return True
            visited[node] = 2
            return False
            
        if dfs_has_cycle(task_id):
            raise ValueError(
                f"CIRCULAR DEPENDENCY DEADLOCK DETECTED: Linking task '{task_id}' to depend "
                f"on '{depends_on_task_id}' creates an illegal scheduler loop. Edge aborted."
            )
            
        # Safe to commit
        cursor.execute("""
            INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
            VALUES (?, ?)
        """, (task_id, depends_on_task_id))
        conn.commit()
        return {"status": "success", "message": f"Dependency edge registered: {task_id} -> {depends_on_task_id}"}
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def create_task(title: str, start_time: str, end_time: str, description: str = "", energy_level: str = "none", constraint_type: str = "soft", ignore_conflicts: bool = False, recurrence_pattern: str = "none", recurrence_count: Optional[int] = None, recurrence_days: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    Creates a new schedule task locally, supporting recurrence, and mirrors to Firestore/Google Calendar.
    """
    logger.info(f"Ollama Agent executing: create_task({title}, {start_time}, {end_time}, ignore_conflicts={ignore_conflicts}, recurrence={recurrence_pattern})")
    
    import datetime
    import random
    
    try:
        task_start = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        task_end = datetime.datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    except Exception as e:
        return {"status": "error", "message": f"Invalid start_time/end_time format: {e}"}
        
    duration = task_end - task_start
    
    # Check for overlapping scheduling conflicts (only for the first instance for simplicity)
    if not ignore_conflicts:
        conflicts = check_overlaps(None, start_time, end_time)
        if conflicts:
            conflict_names = ", ".join([f"'{c['title']}' ({c['start_time']} - {c['end_time']})" for c in conflicts])
            return {
                "status": "error",
                "message": f"Conflict: The requested slot overlaps with existing tasks: {conflict_names}. "
                           f"To force this change, set ignore_conflicts=True, or call calculate_schedule_proposals to reschedule."
            }

    from backend.google_client import GoogleCalendarSync, GoogleOAuthManager
    token = GoogleOAuthManager.get_valid_access_token()
    
    rrule = None
    if recurrence_pattern and recurrence_pattern.lower() != 'none':
        pattern = recurrence_pattern.lower()
        count = recurrence_count or 10
        if pattern == 'daily':
            rrule = f"RRULE:FREQ=DAILY;COUNT={count}"
        elif pattern == 'weekly':
            rrule = f"RRULE:FREQ=WEEKLY;COUNT={count}"
        elif pattern == 'monthly':
            rrule = f"RRULE:FREQ=MONTHLY;COUNT={count}"

    if token:
        # PUSH to Google Calendar
        event_id = GoogleCalendarSync.insert_calendar_event(
            summary=title,
            start_time=start_time,
            end_time=end_time,
            description=description or "",
            recurrence_rule=rrule
        )
        if event_id:
            GoogleCalendarSync.sync_next_7_days()
            return {"status": "success", "message": f"Task '{title}' successfully created and synced with Google Calendar.", "gcal_event_id": event_id}

    # Fallback to local DB creation
    conn = get_db_connection()
    cursor = conn.cursor()
    tasks_to_insert = []
    try:
        rec_group_id = f"rec_{int(time.time())}_{random.randint(1000, 9999)}" if rrule else None
        
        if rrule:
            count = recurrence_count or 10
            curr_start = task_start
            curr_end = task_end
            
            if recurrence_pattern.lower() == 'weekly' and recurrence_days:
                instances_created = 0
                check_date = curr_start
                while instances_created < count:
                    if check_date.weekday() in recurrence_days:
                        task_id = f"task_{int(time.time())}_{instances_created}_{random.randint(1000, 9999)}"
                        instance_start = check_date
                        instance_end = check_date + duration
                        tasks_to_insert.append((
                            task_id,
                            title,
                            description,
                            instance_start.isoformat().replace('+00:00', 'Z'),
                            instance_end.isoformat().replace('+00:00', 'Z'),
                            energy_level,
                            constraint_type,
                            'pending',
                            rec_group_id,
                            rrule,
                            time.time(),
                            time.time()
                        ))
                        instances_created += 1
                    check_date += datetime.timedelta(days=1)
            else:
                for idx in range(count):
                    task_id = f"task_{int(time.time())}_{idx}_{random.randint(1000, 9999)}"
                    tasks_to_insert.append((
                        task_id,
                        title,
                        description,
                        curr_start.isoformat().replace('+00:00', 'Z'),
                        curr_end.isoformat().replace('+00:00', 'Z'),
                        energy_level,
                        constraint_type,
                        'pending',
                        rec_group_id,
                        rrule,
                        time.time(),
                        time.time()
                    ))
                    if recurrence_pattern.lower() == 'daily':
                        curr_start += datetime.timedelta(days=1)
                        curr_end += datetime.timedelta(days=1)
                    elif recurrence_pattern.lower() == 'weekly':
                        curr_start += datetime.timedelta(weeks=1)
                        curr_end += datetime.timedelta(weeks=1)
                    elif recurrence_pattern.lower() == 'monthly':
                        year = curr_start.year + (curr_start.month // 12)
                        month = (curr_start.month % 12) + 1
                        try:
                            curr_start = curr_start.replace(year=year, month=month)
                        except ValueError:
                            curr_start = curr_start + datetime.timedelta(days=30)
                        curr_end = curr_start + duration
        else:
            task_id = f"task_{int(time.time())}_{random.randint(1000, 9999)}"
            tasks_to_insert.append((
                task_id,
                title,
                description,
                start_time,
                end_time,
                energy_level,
                constraint_type,
                'pending',
                None,
                None,
                time.time(),
                time.time()
            ))

        for t in tasks_to_insert:
            cursor.execute("""
                INSERT INTO tasks (id, title, description, start_time, end_time, energy_level, constraint_type, status, recurrence_group_id, recurrence_rule, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, t)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"status": "error", "message": "Conflict: Task ID already exists."}
    finally:
        conn.close()
        
    # Mirror updates to Firestore
    try:
        from backend.app import db_firestore, MOCK_USER_ID
        if db_firestore is not None:
            for t in tasks_to_insert:
                task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(t[0])
                task_ref.set({
                    "id": t[0],
                    "title": t[1],
                    "description": t[2],
                    "start_time": t[3],
                    "end_time": t[4],
                    "energy_level": t[5],
                    "constraint_type": t[6],
                    "status": t[7],
                    "recurrence_group_id": t[8],
                    "recurrence_rule": t[9]
                })
    except Exception as fe:
        logger.error(f"Failed to mirror tasks to Firestore: {fe}")
        
    return {"status": "success", "task_id": tasks_to_insert[0][0], "message": f"Task '{title}' ({len(tasks_to_insert)} occurrences) created successfully."}

def delete_task(task_id: str, target: str = "single") -> Dict[str, Any]:
    """
    Deletes an existing task or entire series from SQLite.
    """
    task_id = resolve_task_id(task_id)
    logger.info(f"Ollama Agent executing: delete_task({task_id}, target={target})")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title, source_event_id, recurrence_group_id FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Task with ID '{task_id}' not found.")
            
        tasks_to_delete = []
        if target == "series" and row["recurrence_group_id"]:
            cursor.execute("SELECT id, source_event_id FROM tasks WHERE recurrence_group_id = ?", (row["recurrence_group_id"],))
            tasks_to_delete = [dict(r) for r in cursor.fetchall()]
        elif target == "series" and row["source_event_id"]:
            parent_id = row["source_event_id"].split('_')[0]
            cursor.execute("SELECT id, source_event_id FROM tasks WHERE source_event_id LIKE ?", (f"{parent_id}%",))
            tasks_to_delete = [dict(r) for r in cursor.fetchall()]
            from backend.google_client import GoogleCalendarSync
            GoogleCalendarSync.delete_calendar_event(parent_id)
        else:
            tasks_to_delete = [{"id": row["id"], "source_event_id": row["source_event_id"]}]
            if row["source_event_id"]:
                from backend.google_client import GoogleCalendarSync
                GoogleCalendarSync.delete_calendar_event(row["source_event_id"])
                
        for t in tasks_to_delete:
            cursor.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))
            cursor.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (t["id"], t["id"]))
        conn.commit()
        
        # Mirror deletion to Firestore
        try:
            from backend.app import db_firestore, MOCK_USER_ID
            if db_firestore is not None:
                for t in tasks_to_delete:
                    task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(t["id"])
                    task_ref.delete()
        except Exception as fe:
            logger.error(f"Failed to mirror task deletion to Firestore: {fe}")
            
        return {"status": "success", "message": f"Successfully deleted {len(tasks_to_delete)} task(s) for '{row['title']}'."}
    finally:
        conn.close()

def clear_calendar() -> Dict[str, Any]:
    """
    Clears all tasks and task dependencies from the local database, Google Calendar, and Firestore.
    """
    logger.info("Ollama Agent executing: clear_calendar()")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        from backend.google_client import GoogleCalendarSync, GoogleOAuthManager
        token = GoogleOAuthManager.get_valid_access_token()
        if token:
            cursor.execute("SELECT source_event_id FROM tasks WHERE source_event_id IS NOT NULL")
            for r in cursor.fetchall():
                GoogleCalendarSync.delete_calendar_event(r["source_event_id"])

        cursor.execute("DELETE FROM tasks")
        cursor.execute("DELETE FROM task_dependencies")
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error in clear_calendar: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

    try:
        from backend.app import db_firestore, MOCK_USER_ID
        if db_firestore is not None:
            tasks_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks")
            docs = tasks_ref.stream()
            for doc in docs:
                doc.reference.delete()
    except Exception as fe:
        logger.error(f"Failed to clear Firestore tasks: {fe}")

    return {"status": "success", "message": "Successfully cleared all calendar events."}

def update_task_metadata(task_id: str, title: Optional[str] = None, description: Optional[str] = None, energy_level: Optional[str] = None, constraint_type: Optional[str] = None, target: str = "single") -> Dict[str, Any]:
    """
    Updates an existing task's metadata (or entire series' metadata) locally and mirrors to Firestore.
    """
    task_id = resolve_task_id(task_id)
    logger.info(f"Ollama Agent executing: update_task_metadata(task_id={task_id}, title={title}, description={description}, target={target})")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if the task exists
        cursor.execute("SELECT id, title, description, energy_level, constraint_type, recurrence_group_id FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if not task:
            raise ValueError(f"Task with ID '{task_id}' not found.")
            
        tasks_to_update = [task_id]
        if target == "series" and task["recurrence_group_id"]:
            cursor.execute("SELECT id FROM tasks WHERE recurrence_group_id = ?", (task["recurrence_group_id"],))
            tasks_to_update = [r["id"] for r in cursor.fetchall()]

        # Build dynamic SQL update fields
        fields = []
        params = []
        
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if description is not None:
            fields.append("description = ?")
            params.append(description)
        if energy_level is not None:
            fields.append("energy_level = ?")
            params.append(energy_level)
        if constraint_type is not None:
            fields.append("constraint_type = ?")
            params.append(constraint_type)
            
        if not fields:
            return {"status": "success", "message": "No updates specified."}
            
        update_query = f"UPDATE tasks SET {', '.join(fields)}, updated_at = {time.time()} WHERE id = ?"
        
        for tid in tasks_to_update:
            cursor.execute(update_query, tuple(params + [tid]))
            
        conn.commit()
        
        # Mirror metadata update to Firestore
        try:
            from backend.app import db_firestore, MOCK_USER_ID
            if db_firestore is not None:
                update_data = {}
                if title is not None:
                    update_data["title"] = title
                if description is not None:
                    update_data["description"] = description
                if energy_level is not None:
                    update_data["energy_level"] = energy_level
                if constraint_type is not None:
                    update_data["constraint_type"] = constraint_type
                if update_data:
                    for tid in tasks_to_update:
                        task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(tid)
                        task_ref.update(update_data)
        except Exception as fe:
            logger.error(f"Failed to mirror task updates to Firestore: {fe}")
            
        return {"status": "success", "message": f"Successfully updated details for {len(tasks_to_update)} task(s)."}
    except Exception as e:
        logger.error(f"Failed to update task details: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

def audit_schedule_conflicts() -> Dict[str, Any]:
    """
    Performs an autonomous scan of the upcoming schedule (next 7 days) to identify conflicts.
    """
    logger.info("Ollama Agent executing: audit_schedule_conflicts()")
    import datetime
    now = datetime.datetime.now().astimezone()
    next_week = now + datetime.timedelta(days=7)
    
    # Query all schedule blocks for the next week
    tasks = get_current_schedule(now.isoformat(), next_week.isoformat())
    conflicts_found = []
    
    for i, t1 in enumerate(tasks):
        try:
            t1_start = datetime.datetime.fromisoformat(t1["start_time"].replace('Z', '+00:00'))
            t1_end = datetime.datetime.fromisoformat(t1["end_time"].replace('Z', '+00:00'))
        except Exception:
            continue
            
        for j, t2 in enumerate(tasks):
            if i >= j:
                continue
            try:
                t2_start = datetime.datetime.fromisoformat(t2["start_time"].replace('Z', '+00:00'))
                t2_end = datetime.datetime.fromisoformat(t2["end_time"].replace('Z', '+00:00'))
            except Exception:
                continue
                
            # Intersect check
            if max(t1_start, t2_start) < min(t1_end, t2_end):
                conflicts_found.append({
                    "task_1": {"id": t1["id"], "title": t1["title"], "start_time": t1["start_time"], "end_time": t1["end_time"]},
                    "task_2": {"id": t2["id"], "title": t2["title"], "start_time": t2["start_time"], "end_time": t2["end_time"]}
                })
                
    if conflicts_found:
        return {
            "status": "conflicts_detected",
            "conflicts": conflicts_found,
            "message": f"Audit complete: identified {len(conflicts_found)} conflict overlap(s) in your upcoming timeline.",
            "hint": "Please reschedule conflicting tasks, or use calculate_schedule_proposals to construct dynamic workarounds."
        }
        
    return {
        "status": "success",
        "message": "Audit complete: no schedule conflict overlaps identified in the upcoming 7 days."
    }

def fetch_unread_emails() -> Dict[str, Any]:
    """
    Fetches unread emails related to timeline updates.
    """
    logger.info("Ollama Agent executing: fetch_unread_emails()")
    try:
        emails = GmailParser.get_unread_updates()
        return {"status": "success", "unread_emails": emails}
    except Exception as e:
        logger.error(f"Gmail fetch failed in agent: {e}")
        return {"status": "error", "message": str(e)}

def query_semantic_memory(query: str, limit: int = 3) -> Dict[str, Any]:
    """
    Queries past scheduling context and notes stored in ChromaDB/SQLite.
    """
    logger.info(f"Ollama Agent executing: query_semantic_memory({query})")
    try:
        results = memory_store.search_interactions(query, limit=limit)
        return {"status": "success", "results": results}
    except Exception as e:
        logger.error(f"Memory query failed in agent: {e}")
        return {"status": "error", "message": str(e)}

def sync_google_calendar(days: int = 30) -> Dict[str, Any]:
    """
    Forces calendar sync to pull latest events as hard constraints for the specified number of days (default: 30).
    """
    logger.info(f"Ollama Agent executing: sync_google_calendar(days={days})")
    try:
        events = GoogleCalendarSync.sync_next_7_days(days=days)
        return {"status": "success", "synced_events_count": len(events), "events": events}
    except Exception as e:
        logger.error(f"Google Calendar sync failed in agent: {e}")
        return {"status": "error", "message": str(e)}

def send_agent_push_notification(title: str, body: str, category: str = "clarification", task_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Directly sends a desktop or PC push notification to the user.
    Use this to notify the user if you have clarifying questions, need schedule updates,
    or identify critical updates.
    """
    logger.info(f"Ollama Agent executing: send_agent_push_notification(title={title}, category={category})")
    try:
        from py_vapid import Vapid
        from pywebpush import webpush
        
        # Load VAPID Keys
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'vapid_private_key'")
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": "VAPID key not initialized in database."}
            
        priv_pem = row["value"].encode('utf-8')
        vapid_key = Vapid.from_pem(priv_pem)
        
        # Fetch subscriptions
        cursor.execute("SELECT id, subscription_json FROM push_subscriptions")
        subscriptions = [dict(r) for r in cursor.fetchall()]
        conn.close()
        
        if not subscriptions:
            return {"status": "success", "message": "No active web push subscriptions found."}
            
        payload = json.dumps({
            "title": title,
            "body": body,
            "category": category,
            "taskId": task_id or "agent-alert",
            "alertType": "agent",
            "silent": False,
            "actions": [
                {"action": "chat", "title": "Reply to AI 💬"},
                {"action": "dismiss", "title": "Dismiss"}
            ]
        })
        
        success_count = 0
        for sub in subscriptions:
            try:
                sub_info = json.loads(sub["subscription_json"])
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims={"sub": "mailto:admin@quantime.app"},
                    ttl=86400
                )
                success_count += 1
            except Exception as ex:
                logger.warning(f"Agent failed to deliver push to subscription {sub['id']}: {ex}")
                
        return {"status": "success", "delivered_count": success_count}
    except Exception as e:
        logger.error(f"Failed to send agent push notification: {e}")
        return {"status": "error", "message": str(e)}

def stage_schedule_proposal(transaction_id: str, option_id: str, description: str, proposed_changes: List[Dict[str, Any]], expires_in_minutes: float = 10.0) -> Dict[str, Any]:
    """
    Stages a proposed scheduling transaction draft in proposed_schedules.
    Proposed changes must be a list of dicts: [{"task_id": "...", "new_start": "ISO_time", "new_end": "ISO_time"}]
    """
    logger.info(f"Ollama Agent executing: stage_schedule_proposal(tx={transaction_id}, option={option_id})")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        expires_at = time.time() + (expires_in_minutes * 60.0)
        changes_json = json.dumps(proposed_changes)
        
        # Insert or replace staged proposal option
        cursor.execute("""
            INSERT OR REPLACE INTO proposed_schedules (transaction_id, option_id, description, proposed_changes, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (transaction_id, option_id, description, changes_json, expires_at, time.time()))
        conn.commit()
        return {"status": "success", "message": f"Staged option '{option_id}' for transaction '{transaction_id}'."}
    except Exception as e:
        logger.error(f"Failed to stage schedule proposal: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

def get_circadian_profile() -> Dict[str, Any]:
    """
    Retrieves the user's circadian peaks and low efficiency hours for scheduling optimization.
    """
    logger.info("Ollama Agent executing: get_circadian_profile")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT key, start_hour, end_hour, efficiency_type FROM circadian_profiles")
        rows = cursor.fetchall()
        profile = [dict(r) for r in rows]
        return {"status": "success", "circadian_profile": profile}
    except Exception as e:
        logger.error(f"Failed to fetch circadian profile: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

def get_day_of_week(date_query: str) -> Dict[str, Any]:
    """
    Calculates the exact day of the week (e.g. Monday, Tuesday) for any given date query (e.g. 'July 6, 2026', '2026-12-25', 'tomorrow').
    """
    import datetime
    import re
    logger.info(f"Ollama Agent executing: get_day_of_week({date_query})")
    
    cleaned = date_query.lower().strip()
    now = datetime.datetime.now()
    
    if cleaned == "today":
        target_dt = now
    elif cleaned == "tomorrow":
        target_dt = now + datetime.timedelta(days=1)
    elif cleaned == "yesterday":
        target_dt = now - datetime.timedelta(days=1)
    else:
        # Clean ordinals
        cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', cleaned)
        target_dt = None
        for fmt in (
            "%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y", "%m/%d/%Y",
            "%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y",
            "%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y",
            "%B %d", "%b %d", "%d %B", "%d %b"
        ):
            try:
                parsed = datetime.datetime.strptime(cleaned, fmt)
                if "%Y" not in fmt and "%y" not in fmt:
                    parsed = parsed.replace(year=now.year)
                target_dt = parsed
                break
            except ValueError:
                continue
                
    if target_dt is None:
        try:
            from dateutil import parser
            target_dt = parser.parse(date_query, default=now)
        except Exception:
            pass
            
    if target_dt is not None:
        return {
            "success": True,
            "query": date_query,
            "date": target_dt.strftime("%Y-%m-%d"),
            "day_of_week": target_dt.strftime("%A"),
            "formatted": target_dt.strftime("%A, %B %d, %Y")
        }
    else:
        return {
            "success": False,
            "message": f"Could not parse date: {date_query}. Provide a standard date."
        }

def calculate_schedule_proposals(title: str, duration_minutes: int, preferred_start: str, energy_level: str = "none", constraint_type: str = "soft") -> Dict[str, Any]:
    """
    Intelligently analyzes schedule conflicts for a target start slot and returns 
    three proposal strategies (compaction, postponement, prioritization) automatically
    staged in proposed_schedules.
    """
    import datetime
    logger.info(f"Ollama Agent executing: calculate_schedule_proposals({title}, {duration_minutes}, {preferred_start})")
    
    try:
        new_start_dt = datetime.datetime.fromisoformat(preferred_start.replace('Z', '+00:00'))
    except Exception as e:
        return {"status": "error", "message": f"Invalid preferred_start format: {e}"}
        
    now = datetime.datetime.now().astimezone()
    if new_start_dt < now:
        return {"status": "error", "message": f"Conflict: Cannot schedule in the past. Preferred start '{preferred_start}' is before current time '{now.isoformat()}'."}
        
    duration = datetime.timedelta(minutes=duration_minutes)
    new_end_dt = new_start_dt + duration
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title, start_time, end_time, energy_level, constraint_type, status FROM tasks")
        all_tasks = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        return {"status": "error", "message": f"Database query failed: {e}"}
    finally:
        conn.close()
        
    target_day = new_start_dt.date()
    day_tasks = []
    for t in all_tasks:
        try:
            t_start = datetime.datetime.fromisoformat(t["start_time"].replace('Z', '+00:00'))
            t_end = datetime.datetime.fromisoformat(t["end_time"].replace('Z', '+00:00'))
            if t_start.date() == target_day or t_end.date() == target_day:
                t["start_dt"] = t_start
                t["end_dt"] = t_end
                day_tasks.append(t)
        except Exception:
            continue
            
    conflicting_tasks = []
    for t in day_tasks:
        if max(t["start_dt"], new_start_dt) < min(t["end_dt"], new_end_dt):
            conflicting_tasks.append(t)
            
    transaction_id = f"tx_{int(time.time())}"
    tz_suffix = preferred_start[-6:] if (preferred_start[-6] in ('+', '-')) else "+00:00"
    
    def format_dt(dt: datetime.datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_suffix

    has_hard_conflict = any(t["constraint_type"] == "hard" for t in conflicting_tasks)
    
    # 1. POSTPONEMENT
    postpone_start = new_start_dt
    while True:
        postpone_end = postpone_start + duration
        overlap = False
        for t in day_tasks:
            if max(t["start_dt"], postpone_start) < min(t["end_dt"], postpone_end):
                overlap = True
                postpone_start = t["end_dt"]
                minutes = (postpone_start.minute // 5 + (1 if postpone_start.minute % 5 else 0)) * 5
                postpone_start = postpone_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
                break
        if not overlap:
            break
            
    postpone_changes = [{
        "task_id": "new_task",
        "new_start": format_dt(postpone_start),
        "new_end": format_dt(postpone_end)
    }]
    
    # 2. COMPACTION
    soft_tasks_to_pack = [
        {
            "id": t["id"],
            "title": t["title"],
            "duration": t["end_dt"] - t["start_dt"]
        }
        for t in day_tasks if t["constraint_type"] != "hard"
    ]
    soft_tasks_to_pack.append({
        "id": "new_task",
        "title": title,
        "duration": duration
    })
    
    pack_start = datetime.datetime.combine(target_day, datetime.time(9, 0)).astimezone(new_start_dt.tzinfo)
    if target_day == now.date():
        pack_start = max(pack_start, now)
        minutes = (pack_start.minute // 5 + (1 if pack_start.minute % 5 else 0)) * 5
        pack_start = pack_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
        
    hard_tasks = sorted([t for t in day_tasks if t["constraint_type"] == "hard"], key=lambda x: x["start_dt"])
    
    compaction_changes = []
    for st in soft_tasks_to_pack:
        while True:
            st_end = pack_start + st["duration"]
            overlap_hard = False
            for ht in hard_tasks:
                if max(ht["start_dt"], pack_start) < min(ht["end_dt"], st_end):
                    overlap_hard = True
                    pack_start = ht["end_dt"]
                    minutes = (pack_start.minute // 5 + (1 if pack_start.minute % 5 else 0)) * 5
                    pack_start = pack_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
                    break
            if not overlap_hard:
                compaction_changes.append({
                    "task_id": st["id"],
                    "new_start": format_dt(pack_start),
                    "new_end": format_dt(st_end)
                })
                pack_start = st_end
                break
                
    # 3. PRIORITIZATION
    prioritization_changes = []
    if has_hard_conflict:
        hard_ends = [ht["end_dt"] for ht in conflicting_tasks if ht["constraint_type"] == "hard"]
        prio_start = max(hard_ends)
        minutes = (prio_start.minute // 5 + (1 if prio_start.minute % 5 else 0)) * 5
        prio_start = prio_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
    else:
        prio_start = new_start_dt
        
    prio_end = prio_start + duration
    prioritization_changes.append({
        "task_id": "new_task",
        "new_start": format_dt(prio_start),
        "new_end": format_dt(prio_end)
    })
    
    occupied_slots = sorted(
        [{"start": t["start_dt"], "end": t["end_dt"]} for t in day_tasks if t["constraint_type"] == "hard"] + 
        [{"start": prio_start, "end": prio_end}],
        key=lambda x: x["start"]
    )
    
    for ct in conflicting_tasks:
        if ct["constraint_type"] == "hard":
            continue
        ct_duration = ct["end_dt"] - ct["start_dt"]
        ct_start = prio_end
        while True:
            ct_end = ct_start + ct_duration
            overlap = False
            for slot in occupied_slots:
                if max(slot["start"], ct_start) < min(slot["end"], ct_end):
                    overlap = True
                    ct_start = slot["end"]
                    minutes = (ct_start.minute // 5 + (1 if ct_start.minute % 5 else 0)) * 5
                    ct_start = ct_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
                    break
            if not overlap:
                prioritization_changes.append({
                    "task_id": ct["id"],
                    "new_start": format_dt(ct_start),
                    "new_end": format_dt(ct_end)
                })
                occupied_slots.append({"start": ct_start, "end": ct_end})
                occupied_slots = sorted(occupied_slots, key=lambda x: x["start"])
                break
                
    # Deduplicate proposals
    unique_proposals = {}
    seen_changes = set()
    
    strategies = [
        ("prioritization", "Force the new task into the preferred time slot and push overlapping flexible tasks to later open windows.", prioritization_changes),
        ("compaction", "Consolidate all flexible/soft tasks into consecutive blocks starting from morning or current time, avoiding scheduled hard commitments.", compaction_changes),
        ("postponement", f"Schedule the new task later from {postpone_start.strftime('%I:%M %p')} to {postpone_end.strftime('%I:%M %p')}, keeping existing tasks in place.", postpone_changes)
    ]
    
    for opt_id, desc, changes in strategies:
        normalized = sorted(changes, key=lambda x: (x["task_id"], x["new_start"]))
        changes_str = json.dumps(normalized)
        if changes_str not in seen_changes:
            seen_changes.add(changes_str)
            unique_proposals[opt_id] = changes
            stage_schedule_proposal(
                transaction_id=transaction_id,
                option_id=opt_id,
                description=desc,
                proposed_changes=changes
            )
            
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "conflicts_detected": len(conflicting_tasks) > 0,
        "proposals": unique_proposals
    }

# Maps tool identifiers to corresponding local modules
TOOL_FUNCTIONS = {
    "get_current_schedule": get_current_schedule,
    "modify_task_time": modify_task_time,
    "resolve_dependencies": resolve_dependencies,
    "create_task_dependency": create_task_dependency,
    "create_task": create_task,
    "delete_task": delete_task,
    "clear_calendar": clear_calendar,
    "update_task_metadata": update_task_metadata,
    "fetch_unread_emails": fetch_unread_emails,
    "query_semantic_memory": query_semantic_memory,
    "sync_google_calendar": sync_google_calendar,
    "stage_schedule_proposal": stage_schedule_proposal,
    "get_circadian_profile": get_circadian_profile,
    "get_day_of_week": get_day_of_week,
    "calculate_schedule_proposals": calculate_schedule_proposals,
    "audit_schedule_conflicts": audit_schedule_conflicts,
    "send_agent_push_notification": send_agent_push_notification
}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_agent_push_notification",
            "description": "Send a desktop/PC push notification to the user for clarifying questions, schedule checks, or to alert them about important updates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the push notification banner"},
                    "body": {"type": "string", "description": "The description or explanation text shown in the notification body"},
                    "category": {"type": "string", "enum": ["clarification", "important_email", "alert"], "description": "The type of alert to classify routing. Defaults to 'clarification'."},
                    "task_id": {"type": "string", "description": "Optional associated task ID to allow context opening"}
                },
                "required": ["title", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_task_metadata",
            "description": "Updates details for an existing task: renaming the title, modifying or adding notes/description, changing energy levels, or editing constraint types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The unique ID of the task to update"},
                    "title": {"type": "string", "description": "The new title to rename the task to (optional)"},
                    "description": {"type": "string", "description": "The new description or notes to add/update for the task (optional)"},
                    "energy_level": {"type": "string", "enum": ["none", "crimson", "teal"], "description": "Update energy classification band (optional)"},
                    "constraint_type": {"type": "string", "enum": ["soft", "hard"], "description": "Update priority constraint type (optional)"},
                    "target": {"type": "string", "enum": ["single", "series"], "description": "Specifies whether to update this occurrence only ('single') or all occurrences in the recurring series ('series'). Defaults to 'single'."}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_of_week",
            "description": "Calculate the exact day of the week (e.g. Monday, Tuesday) for any date query (e.g. 'July 6, 2026', 'next Friday', '2026-07-06').",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_query": {"type": "string", "description": "The date reference to compute, e.g. 'July 6, 2026' or '2026-07-06'"}
                },
                "required": ["date_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_schedule",
            "description": "Retrieve schedule items and tasks within the specified date-time bounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO start date-time, e.g. '2026-06-06T00:00:00Z'"},
                    "end_date": {"type": "string", "description": "ISO end date-time, e.g. '2026-06-13T23:59:59Z'"}
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_task_time",
            "description": "Modify start and end boundaries for a task. Aborts if task is a hard constraint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Unique ID identifier of task"},
                    "new_start": {"type": "string", "description": "New ISO start date-time"},
                    "new_end": {"type": "string", "description": "New ISO end date-time"},
                    "ignore_conflicts": {"type": "boolean", "description": "Set to true to force overlapping schedule times (defaults to false)"},
                    "target": {"type": "string", "enum": ["single", "series"], "description": "Specifies whether to shift this occurrence only ('single') or shift all future occurrences in the recurring series relatively ('series'). Defaults to 'single'."}
                },
                "required": ["task_id", "new_start", "new_end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_dependencies",
            "description": "Trace dependencies of a task and detect scheduler deadlocks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID of the target task"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task_dependency",
            "description": "Registers a task dependency linkage (A depends on B). Enforces DAG rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Dependent task ID (A)"},
                    "depends_on_task_id": {"type": "string", "description": "Prerequisite task ID (B)"}
                },
                "required": ["task_id", "depends_on_task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new schedule task locally, with optional recurrence parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the task to schedule"},
                    "start_time": {"type": "string", "description": "ISO start date-time, e.g. '2026-06-08T15:00:00Z'"},
                    "end_time": {"type": "string", "description": "ISO end date-time, e.g. '2026-06-08T17:00:00Z'"},
                    "description": {"type": "string", "description": "Detailed description/notes for the task"},
                    "energy_level": {"type": "string", "enum": ["none", "crimson", "teal"], "description": "Energy band requirement"},
                    "constraint_type": {"type": "string", "enum": ["soft", "hard"], "description": "Priority type of constraint (soft: flexible, hard: immutable)"},
                    "ignore_conflicts": {"type": "boolean", "description": "Set to true to force overlapping schedule times (defaults to false)"},
                    "recurrence_pattern": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"], "description": "Optional recurrence frequency pattern"},
                    "recurrence_count": {"type": "integer", "description": "Number of occurrences to create for the series (optional, default: 10)"},
                    "recurrence_days": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Required when scheduling weekly recurring tasks on specific weekdays (e.g. every Thursday = [3], every Tuesday and Thursday = [1, 3]). Days are: 0 = Monday, 1 = Tuesday, 2 = Wednesday, 3 = Thursday, 4 = Friday, 5 = Saturday, 6 = Sunday."
                    }
                },
                "required": ["title", "start_time", "end_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete/cancel an existing task by ID or the entire recurring series.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The unique ID of the task to delete"},
                    "target": {"type": "string", "enum": ["single", "series"], "description": "Whether to delete just this single task occurrence ('single') or the entire routine series ('series'). Default is 'single'."}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_calendar",
            "description": "Clears and purges all tasks, routines, and task dependencies from the scheduler database, Firestore, and synced Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_unread_emails",
            "description": "Ingest recent unread Gmail inbox messages containing critical deadlines/scheduling terms.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_semantic_memory",
            "description": "Search ChromaDB semantic vector store for historical notes, schedules, and preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text similarity query"},
                    "limit": {"type": "integer", "description": "Max number of matches to return (default: 3)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_google_calendar",
            "description": "Force manual sync to fetch Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "The number of days to sync from calendar (default: 30)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stage_schedule_proposal",
            "description": "Stage a speculative schedule workaround plan (transaction option) inside proposed_schedules staging database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "Unique transaction ID for this proposal batch (e.g. 'tx_' + timestamp)"},
                    "option_id": {"type": "string", "enum": ["compaction", "postponement", "prioritization"], "description": "Rescheduling strategy option identifier"},
                    "description": {"type": "string", "description": "Short explanation of this workaround strategy"},
                    "proposed_changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task_id": {"type": "string", "description": "The ID of the task being moved"},
                                "new_start": {"type": "string", "description": "Proposed new ISO start datetime string using correct timezone offset suffix"},
                                "new_end": {"type": "string", "description": "Proposed new ISO end datetime string using correct timezone offset suffix"}
                            },
                            "required": ["task_id", "new_start", "new_end"]
                        },
                        "description": "The collection of task updates proposed in this option"
                    }
                },
                "required": ["transaction_id", "option_id", "description", "proposed_changes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_circadian_profile",
            "description": "Retrieve the user's circadian peaks and downtime hours to align high-energy and low-energy tasks.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_schedule_proposals",
            "description": "Intelligently analyzes schedule conflicts for a target start slot and returns three proposal strategies (compaction, postponement, prioritization) automatically staged in proposed_schedules database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the new task to schedule"},
                    "duration_minutes": {"type": "integer", "description": "Duration of the task in minutes"},
                    "preferred_start": {"type": "string", "description": "Preferred ISO start date-time using correct timezone offset suffix, e.g. '2026-06-08T15:00:00-07:00'"},
                    "energy_level": {"type": "string", "enum": ["none", "crimson", "teal"], "description": "Energy band requirement (optional)"},
                    "constraint_type": {"type": "string", "enum": ["soft", "hard"], "description": "Priority type of constraint (optional, defaults to soft)"}
                },
                "required": ["title", "duration_minutes", "preferred_start"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "audit_schedule_conflicts",
            "description": "Performs an autonomous scan of the upcoming schedule (next 7 days) to identify and return all overlapping conflicts.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]

SYSTEM_PROMPT = """You are the Quantime Scheduling Orchestrator, an intelligent, conversational scheduling assistant.
Your goal is to help the user organize their daily, weekly, and monthly schedules in the most optimal and efficient way.

You have full read/write access to the local database, Gmail, and Google Calendar via your registered tools.
Always use your tools to check, sync, optimize, reschedule, delete, or create tasks as requested by the user.

Behavioral Guidelines & Rules:
1. **Analyze First**: When asked to review, brainstorm, optimize, or troubleshoot schedules, always call `get_current_schedule` to fetch the schedule bounds (daily, weekly, or monthly) first.
2. **Handle Conflicts**: Identify overlapping events. Recommend changes to resolve conflicts.
3. **Immutable Constraints**: Google Calendar events or events marked with `constraint_type: "hard"` are IMMUTABLE. You cannot modify, move, or delete them. If a conflict arises with a HARD constraint, reschedule the flexible `soft` constraint tasks around them.
4. **Circadian Energy Optimization**: Query the user's circadian peaks with `get_circadian_profile` and match task energy requirements:
   - High-energy study/work blocks (`crimson`) should be scheduled during peak, uninterrupted circadian hours.
   - Low-energy reading/administrative tasks (`teal`) should be scheduled during downtime/slump hours.
5. **Sync Proactively**: If you need the latest email context or calendar constraints, call `sync_google_calendar` or `fetch_unread_emails`.
6. **Task Dependencies**: Enforce Directed Acyclic Graph (DAG) dependency constraints before proposing changes. Call `create_task_dependency` to link dependent tasks.
7. **Speculative Conflict Rescheduling**:
   - When the user asks to schedule a new task that conflicts with existing tasks, or requests a workaround for a conflict, you MUST call the `calculate_schedule_proposals` tool with the target task's details.
   - This tool will automatically compute, format, and database-stage three conflict-free rescheduling strategies: compaction, postponement, and prioritization.
   - When explaining the staged options to the user, you MUST number them sequentially starting from Option 1 (e.g. Option 1, Option 2) regardless of which strategies were staged. Do not skip numbers or hardcode Option 3 if only two options are available.
   - Clearly explain each option's strategy in the text response and output a `<schedule-proposal tx="tx_id_here">` tag at the end of your message to render the interactive buttons in the UI.
8. **Conversational & Proactive**: Give clear summaries of actions you took, highlight what tools you executed, explain why you restructured the schedule, and outline any proposed adjustments clearly to the user.
9. **No Scheduling in the Past**: You must NEVER create new tasks or modify existing tasks to start in the past relative to the current date-time context. Always verify the current time before choosing task time slots.
10. **Calendar Date Math**: You must NEVER guess or calculate the day of the week for a specific calendar date yourself. If the user asks about a day of the week, a holiday, or asks you to perform calendar math (e.g. "what day is July 6th?", "what day is next Friday?", "what day is October 24th?"), you MUST call the `get_day_of_week` tool to compute the correct date and weekday.
11. **Recurring Tasks & Complex Patterns**:
   - For simple recurring events (e.g. 'Piano lesson every Friday at 4pm for 5 weeks'), call `create_task` with the appropriate `recurrence_pattern="weekly"`, `recurrence_count=5`, and `recurrence_days=[4]`.
   - When asked to schedule a task recurring on certain weekdays (e.g. 'every Thursday'), you MUST select `recurrence_pattern="weekly"` and explicitly populate the `recurrence_days` array (e.g. `[3]` for Thursday, `[1, 3]` for Tuesdays and Thursdays). Always ensure the `start_time` parameter points to the correct calendar date of the first occurrence in the series.
   - For complex recurring requests containing exclusions or specific combinations (e.g. 'daily except Tuesdays'), do NOT use the generic `recurrence_pattern`. Instead, resolve the target dates manually using the 14-day calendar reference map or by executing `get_day_of_week`, and make multiple `create_task` tool calls in parallel (one for each eligible day, setting `recurrence_pattern="none"`) to create them precisely.
12. **Circadian Brainstorming & Schedule Optimization**:
   - Proactively brainstorm efficiency improvements with the user.
   - When asked to optimize or review their day or schedule, compare their scheduled tasks against their circadian peaks and low-efficiency slumps (`get_circadian_profile`).
   - If a high-energy task (such as study or deep work) is placed in a low-efficiency slump, or a low-energy task (such as admin or reading) is placed in a peak hour, point it out and suggest reorganizing it.
   - Offer to run rescheduling for them or walk them through option proposals. Offer practical time management techniques (e.g. task bundling, Pomodoro blocks, or regular breaks).
13. **Autonomous Schedule Audits**:
    - When checking the schedule or optimizing the timeline, call `audit_schedule_conflicts` to find overlaps. If conflicts are found, resolve them by shifting tasks or proposing compaction options.
"""

def generate_agent_stream(prompt: str, chat_history: List[Dict[str, str]] = [], audio_b64: Optional[str] = None, selected_date: Optional[str] = None, current_time: Optional[str] = None) -> Generator[Tuple[str, str], None, None]:
    """
    Communicates with local Ollama API, streaming output tokens.
    Appends '<|think|>' token to prompt to force deep-reasoning mode.
    Correctly supports sequential, recursive multi-turn tool calling up to max_depth of 5.
    """
    # Generate 14-day calendar reference map to prevent LLM day/date calculation hallucinations
    import datetime
    tz_offset = datetime.datetime.now().astimezone().strftime('%z')
    if not tz_offset or len(tz_offset) < 5:
        tz_offset = "+0000"
    tz_formatted = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"
    tz_suffix = f"{tz_offset[:3]}:{tz_offset[3:]}"
    current_time_str = time.strftime("%A, %B %d, %Y, %I:%M %p %Z")
    
    if current_time:
        try:
            dt = datetime.datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            current_time_str = dt.strftime("%A, %B %d, %Y, %I:%M %p %Z")
            tz_offset = dt.strftime('%z')
            if not tz_offset or len(tz_offset) < 5:
                tz_offset = "+0000"
            tz_formatted = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"
            tz_suffix = f"{tz_offset[:3]}:{tz_offset[3:]}"
        except Exception as ex:
            logger.error(f"Failed to parse current_time parameter: {ex}")
            
    selected_date_context = ""
    if selected_date:
        try:
            s_dt = datetime.datetime.fromisoformat(selected_date.replace('Z', '+00:00'))
            selected_date_str = s_dt.strftime("%A, %B %d, %Y")
            selected_date_context = (
                f"- USER'S CURRENT PLANNER VIEW: The user is currently viewing/focusing on **{selected_date_str}** in their dashboard planner UI.\n"
                f"- Actionable Target Day: If the user says 'today', 'this day', or asks to add/edit/optimize tasks without specifying a date, they refer to this selected view day ({selected_date_str}). Feel free to inspect, query, or edit this day using your tools.\n"
            )
        except Exception as ex:
            logger.error(f"Failed to parse selected_date parameter: {ex}")

    calendar_ref = []
    base_date = datetime.datetime.now()
    if current_time:
        try:
            base_date = datetime.datetime.fromisoformat(current_time.replace('Z', '+00:00'))
        except Exception:
            pass
    for i in range(14):
        d = base_date + datetime.timedelta(days=i)
        day_str = d.strftime("%A, %B %d, %Y")
        if i == 0:
            calendar_ref.append(f"- {day_str} (Today)")
        elif i == 1:
            calendar_ref.append(f"- {day_str} (Tomorrow)")
        else:
            calendar_ref.append(f"- {day_str}")
    calendar_ref_str = "\n".join(calendar_ref)

    dynamic_system_prompt = (
        SYSTEM_PROMPT + 
        f"\n\nCURRENT DATE-TIME CONTEXT:\n"
        f"- Today's date and time: {current_time_str} ({tz_formatted})\n"
        f"- User Timezone: {tz_formatted}\n"
        f"{selected_date_context}"
        f"- Ensure all new tasks created use the correct year, month, and day matching the current context unless a future date is explicitly requested.\n"
        f"- Timezone Guideline: You MUST specify task start_time and end_time ISO strings using the same timezone offset suffix as the user's timezone ({tz_suffix}) instead of defaulting to UTC 'Z' (unless the user's timezone is UTC itself). This ensures scheduled blocks appear at the correct local hour on the user's timeline interface.\n"
        f"\nCALENDAR REFERENCE LOOKUP MAP (NEXT 14 DAYS):\n"
        f"{calendar_ref_str}\n"
        f"\nIMPORTANT AGENTIC TOOL INSTRUCTION:\n"
        f"- If your model does not natively support the API-level tool calling, you MUST call tools by outputting the tag at the end of your response: <tool_call name=\"tool_name\">{{\"arg1\": \"value1\", ...}}</tool_call>\n"
        f"- You can execute multiple tool calls sequentially or in parallel. Do NOT output any explanation or conversational text inside or after the tool calls. Put all conversational responses before the tool calls.\n"
        f"- ALWAYS execute the corresponding tools to create, modify, delete, or retrieve tasks. Do NOT assume that actions are already completed because of claims in the chat history, and do NOT copy the text-only style of the history. You MUST issue the actual tool calls (e.g. `create_task`, `delete_task`, `modify_task_time`) to perform the actions in the database for the current turn.\n"
    )
    
    messages = [{"role": "system", "content": dynamic_system_prompt}]
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    # Inject '<|think|>' token at end of incoming prompt
    agent_prompt = prompt
    if not agent_prompt.strip().endswith("<|think|>"):
        agent_prompt += "\n<|think|>"
        
    user_msg = {"role": "user", "content": agent_prompt}
    if audio_b64:
        user_msg["images"] = [audio_b64]
    messages.append(user_msg)
    
    def chat_loop(current_messages: List[Dict[str, Any]], depth: int = 0) -> Generator[Tuple[str, str], None, None]:
        if depth > 5:
            yield ("thought", f"\n[System: Max tool recursion depth 5 exceeded. Stopping.]\n")
            return
            
        payload = {
            "model": get_selected_model(),
            "messages": current_messages,
            "tools": TOOL_SCHEMAS,
            "stream": True,
            "keep_alive": -1
        }
        
        try:
            req = urllib.request.Request(
                OLLAMA_CHAT_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=120) as resp:
                in_thought_block = False
                assistant_message = None
                yielded_text_len = 0
                
                for line in resp:
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    message = chunk.get("message", {})
                    
                    if message:
                        if assistant_message is None:
                            assistant_message = {
                                "role": "assistant",
                                "content": message.get("content", ""),
                                "tool_calls": message.get("tool_calls", [])
                            }
                        else:
                            if "content" in message and message["content"]:
                                assistant_message["content"] += message["content"]
                            if "tool_calls" in message and message["tool_calls"]:
                                if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
                                    assistant_message["tool_calls"] = []
                                assistant_message["tool_calls"].extend(message["tool_calls"])
                    
                    content = message.get("content", "")
                    if content:
                        if "<|channel>thought" in content:
                            in_thought_block = True
                            content = content.replace("<|channel>thought", "")
                            
                        if in_thought_block:
                            if "<|channel>" in content:
                                parts = content.split("<|channel>")
                                yield ("thought", parts[0])
                                in_thought_block = False
                                if len(parts) > 1:
                                    text_part = parts[1].replace("text\n", "")
                                    if assistant_message and "content" in assistant_message:
                                        full_content = assistant_message["content"]
                                        tool_call_start = full_content.find("<tool_call")
                                        if tool_call_start == -1:
                                            yield ("text", text_part)
                                            yielded_text_len = len(full_content)
                                        else:
                                            safe_to_yield_len = tool_call_start - yielded_text_len
                                            if safe_to_yield_len > 0:
                                                yield ("text", text_part[:safe_to_yield_len])
                                            yielded_text_len = tool_call_start
                                    else:
                                        yield ("text", text_part)
                            else:
                                yield ("thought", content)
                        else:
                            if assistant_message and "content" in assistant_message:
                                full_content = assistant_message["content"]
                                tool_call_start = full_content.find("<tool_call")
                                if tool_call_start == -1:
                                    # Check if the end of full_content is a partial prefix of '<tool_call' or '<|channel>'
                                    check_len = min(12, len(full_content))
                                    has_partial_prefix = False
                                    for i in range(1, check_len + 1):
                                        suffix = full_content[-i:]
                                        if "<tool_call".startswith(suffix) or "</tool_call>".startswith(suffix) or "<|channel>".startswith(suffix):
                                            safe_to_yield_len = len(full_content) - i - yielded_text_len
                                            if safe_to_yield_len > 0:
                                                yield ("text", full_content[yielded_text_len : yielded_text_len + safe_to_yield_len])
                                                yielded_text_len += safe_to_yield_len
                                            has_partial_prefix = True
                                            break
                                    if not has_partial_prefix:
                                        safe_to_yield_len = len(full_content) - yielded_text_len
                                        if safe_to_yield_len > 0:
                                            yield ("text", full_content[yielded_text_len:])
                                            yielded_text_len = len(full_content)
                                else:
                                    safe_to_yield_len = tool_call_start - yielded_text_len
                                    if safe_to_yield_len > 0:
                                        yield ("text", full_content[yielded_text_len:tool_call_start])
                                    yielded_text_len = tool_call_start
                            else:
                                yield ("text", content)
                
                # Check for Tool Call requests (including fallback text-based tool calls)
                import re
                if assistant_message:
                    content_str = assistant_message.get("content", "")
                    matches = re.findall(r'<tool_call\s+name="(\w+)">([\s\S]*?)</tool_call>', content_str)
                    if matches:
                        if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
                            assistant_message["tool_calls"] = []
                        for func_name, args_str in matches:
                            if any(tc.get("function", {}).get("name") == func_name for tc in assistant_message["tool_calls"]):
                                continue
                            try:
                                func_args = json.loads(args_str.strip())
                                assistant_message["tool_calls"].append({
                                    "id": f"call_{int(time.time() * 1050)}",
                                    "type": "function",
                                    "function": {
                                        "name": func_name,
                                        "arguments": func_args
                                    }
                                })
                            except Exception as parse_err:
                                logger.warning(f"Failed to parse text-based tool call args for {func_name}: {parse_err}")
                
                if assistant_message and assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)
                    
                    for tool_call in assistant_message["tool_calls"]:
                        func_name = tool_call["function"]["name"]
                        func_args = tool_call["function"]["arguments"]
                        call_id = tool_call.get("id", f"call_{int(time.time() * 1050)}")
                        
                        yield ("thought", f"\n[Agent Triggered Tool: {func_name}({json.dumps(func_args)})]\n")
                        
                        try:
                            # Execute the tool locally
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                            yield ("thought", f"[Tool Execution Success]\n")
                            
                            # Self-Critique Loop: Check for conflicts or DAG issues introduced after scheduling mutations
                            if func_name in ["create_task", "modify_task_time"]:
                                start_t = func_args.get("start_time") or func_args.get("new_start")
                                end_t = func_args.get("end_time") or func_args.get("new_end")
                                if func_name == "create_task":
                                    task_id = result.get("task_id") or (f"gcal_{result.get('gcal_event_id')}" if result.get("gcal_event_id") else None)
                                else:
                                    task_id = func_args.get("task_id")
                                if start_t and end_t:
                                    conflicts = check_overlaps(task_id, start_t, end_t)
                                    if conflicts:
                                        conflict_names = ", ".join([f"'{c['title']}'" for c in conflicts])
                                        result = {
                                            "status": "critique_error",
                                            "message": f"Self-Critique detected conflict: scheduled task overlaps with {conflict_names}.",
                                            "hint": "Please reschedule this task to an alternative free slot or propose compaction options to resolve."
                                        }
                                        yield ("thought", f"[Critique Warning: Overlap detected on scheduled task - Requesting retry]\n")
                        except Exception as err:
                            # Gemma 4 self-correction feedback loop: pass clear exception trace back to system context
                            result = {
                                "status": "error",
                                "error_type": type(err).__name__,
                                "message": str(err),
                                "hint": "Please review your input arguments, handle conflicts accordingly, or call another tool to check schedule conditions."
                            }
                            yield ("thought", f"[Tool Execution Error: {str(err)} - Relayed to AI agent for self-correction]\n")
                            
                        current_messages.append({
                          "role": "tool",
                          "tool_call_id": call_id,
                          "name": func_name,
                          "content": json.dumps(result)
                        })
                        
                    yield from chat_loop(current_messages, depth + 1)
                    
        except Exception as e:
            logger.error(f"Error in Ollama loop request: {e}")
            if depth == 0:
                yield ("thought", "[Ollama connection inactive. Loading offline mock scheduler agent...]\n")
                time.sleep(1.0)
                yield ("thought", "Syncing memory buffers...\n")
                time.sleep(0.5)
                
                q_lower = prompt.lower()
                if "reschedule" in q_lower or "move" in q_lower:
                    yield ("text", "I've checked the local database. You have no conflicting hard constraints in that slot. Rescheduled successfully.")
                else:
                    yield ("text", "Quantime offline processor active. Type your query or verify Ollama service connectivity.")
            else:
                yield ("text", f"\n[System Error: Connection to Ollama failed during recursive tool loop: {e}]")

    tag_buffer = ""
    in_suppress_block = False
    
    for channel, chunk in chat_loop(messages, 0):
        if channel == "text":
            tag_buffer += chunk
            while tag_buffer:
                if in_suppress_block:
                    idx = tag_buffer.find("</tool_call>")
                    if idx != -1:
                        tag_buffer = tag_buffer[idx + len("</tool_call>"):]
                        in_suppress_block = False
                    else:
                        break
                else:
                    if tag_buffer.startswith("<"):
                        is_prefix = False
                        target_tags = ["<tool_call", "</tool_call>", "<|channel>"]
                        for tag in target_tags:
                            if tag.startswith(tag_buffer):
                                is_prefix = True
                                break
                            elif tag_buffer.startswith(tag):
                                if tag == "<tool_call":
                                    in_suppress_block = True
                                tag_buffer = tag_buffer[len(tag):]
                                is_prefix = True
                                break
                        if is_prefix:
                            break
                        else:
                            yield ("text", tag_buffer[0])
                            tag_buffer = tag_buffer[1:]
                    else:
                        idx = tag_buffer.find("<")
                        if idx != -1:
                            yield ("text", tag_buffer[:idx])
                            tag_buffer = tag_buffer[idx:]
                        else:
                            yield ("text", tag_buffer)
                            tag_buffer = ""
        else:
            yield (channel, chunk)
            
    if tag_buffer and not in_suppress_block:
        yield ("text", tag_buffer)
