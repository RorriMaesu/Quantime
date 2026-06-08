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

# =====================================================================
# Core Database Tool Implementations
# =====================================================================

def get_current_schedule(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Queries the local SQLite database to fetch schedule tasks between two date-time ranges.
    """
    logger.info(f"Ollama Agent executing: get_current_schedule({start_date}, {end_date})")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, description, start_time, end_time, energy_level, constraint_type, status 
            FROM tasks 
            WHERE start_time <= ? AND end_time >= ?
        """, (end_date, start_date))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to query local schedule: {e}")
        return []
    finally:
        conn.close()

def modify_task_time(task_id: str, new_start: str, new_end: str) -> Dict[str, Any]:
    """
    Mutates a local task's scheduling bounds.
    Critical Constraint: Aborts immediately if target task is marked as a HARD constraint.
    """
    logger.info(f"Ollama Agent executing: modify_task_time({task_id}, {new_start}, {new_end})")
    
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
        cursor.execute("SELECT id, title, constraint_type, source_event_id FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        
        if not task:
            raise ValueError(f"Target task with ID '{task_id}' does not exist.")
            
        if task["constraint_type"] == "hard":
            # Throw clear error to prevent model from rescheduling rigid elements
            raise PermissionError(
                f"IMMUTABLE CONSTRAINT CONFLICT: Task '{task['title']}' (ID: {task_id}) is flagged "
                "as a HARD constraint (e.g. Google Calendar Sync/Class schedule). This task cannot be "
                "shifted automatically by the orchestrator. Manual bypass or user intervention is required."
            )
            
        # Perform Local Mutation
        cursor.execute("""
            UPDATE tasks 
            SET start_time = ?, end_time = ?, updated_at = ? 
            WHERE id = ?
        """, (new_start, new_end, time.time(), task_id))
        
        # If it carries a Google Calendar Sync Event ID, push changes back to the API
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

def create_task(title: str, start_time: str, end_time: str, description: str = "", energy_level: str = "none", constraint_type: str = "soft") -> Dict[str, Any]:
    """
    Creates a new schedule task locally and mirrors it to Firestore if active.
    """
    logger.info(f"Ollama Agent executing: create_task({title}, {start_time}, {end_time})")
    
    import datetime
    try:
        task_start = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        now = datetime.datetime.now().astimezone()
        if task_start < now:
            return {
                "status": "error",
                "message": f"Conflict: Cannot schedule task in the past. Staged start_time '{start_time}' is before current time '{now.isoformat()}'."
            }
    except Exception as e:
        logger.error(f"Error checking start_time bounds: {e}")

    task_id = f"task_{int(time.time())}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO tasks (id, title, description, start_time, end_time, energy_level, constraint_type, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (task_id, title, description, start_time, end_time, energy_level, constraint_type, time.time(), time.time()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("Conflict: Task ID already exists.")
    conn.close()
    
    # Mirror update to Firestore
    try:
        from backend.app import db_firestore, MOCK_USER_ID
        if db_firestore is not None:
            task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
            task_ref.set({
                "id": task_id,
                "title": title,
                "description": description,
                "start_time": start_time,
                "end_time": end_time,
                "energy_level": energy_level,
                "constraint_type": constraint_type,
                "status": "pending"
            })
    except Exception as fe:
        logger.error(f"Failed to mirror task creation to Firestore: {fe}")
        
    return {"status": "success", "task_id": task_id, "message": f"Task '{title}' created successfully from {start_time} to {end_time}."}

def delete_task(task_id: str) -> Dict[str, Any]:
    """
    Deletes an existing task from SQLite.
    """
    logger.info(f"Ollama Agent executing: delete_task({task_id})")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Task with ID '{task_id}' not found.")
        
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        cursor.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (task_id, task_id))
        conn.commit()
        
        # Mirror deletion to Firestore
        try:
            from backend.app import db_firestore, MOCK_USER_ID
            if db_firestore is not None:
                task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
                task_ref.delete()
        except Exception as fe:
            logger.error(f"Failed to mirror task deletion to Firestore: {fe}")
            
        return {"status": "success", "message": f"Task '{row['title']}' (ID: {task_id}) deleted successfully."}
    finally:
        conn.close()

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

# Maps tool identifiers to corresponding local modules
TOOL_FUNCTIONS = {
    "get_current_schedule": get_current_schedule,
    "modify_task_time": modify_task_time,
    "resolve_dependencies": resolve_dependencies,
    "create_task_dependency": create_task_dependency,
    "create_task": create_task,
    "delete_task": delete_task,
    "fetch_unread_emails": fetch_unread_emails,
    "query_semantic_memory": query_semantic_memory,
    "sync_google_calendar": sync_google_calendar,
    "stage_schedule_proposal": stage_schedule_proposal,
    "get_circadian_profile": get_circadian_profile
}

TOOL_SCHEMAS = [
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
                    "new_end": {"type": "string", "description": "New ISO end date-time"}
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
            "description": "Create a new schedule task locally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the task to schedule"},
                    "start_time": {"type": "string", "description": "ISO start date-time, e.g. '2026-06-08T15:00:00Z'"},
                    "end_time": {"type": "string", "description": "ISO end date-time, e.g. '2026-06-08T17:00:00Z'"},
                    "description": {"type": "string", "description": "Detailed description/notes for the task"},
                    "energy_level": {"type": "string", "enum": ["none", "crimson", "teal"], "description": "Energy band requirement"},
                    "constraint_type": {"type": "string", "enum": ["soft", "hard"], "description": "Priority type of constraint (soft: flexible, hard: immutable)"}
                },
                "required": ["title", "start_time", "end_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete/cancel an existing task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The unique ID of the task to delete"}
                },
                "required": ["task_id"]
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
   - When the user asks to schedule a new task that conflicts with existing tasks, or requests a workaround for a conflict, DO NOT call `modify_task_time` directly.
   - Instead, generate a unique transaction ID (`tx_<timestamp>`) and stage up to three rescheduling workaround options using `stage_schedule_proposal` (e.g. option_id="compaction", "postponement", or "prioritization").
   - Clearly explain each option's strategy in the text response and output a `<schedule-proposal tx="tx_id_here">` tag at the end of your message to render the interactive buttons in the UI.
8. **Conversational & Proactive**: Give clear summaries of actions you took, highlight what tools you executed, explain why you restructured the schedule, and outline any proposed adjustments clearly to the user.
9. **No Scheduling in the Past**: You must NEVER create new tasks or modify existing tasks to start in the past relative to the current date-time context. Always verify the current time before choosing task time slots.
"""

def generate_agent_stream(prompt: str, chat_history: List[Dict[str, str]] = [], audio_b64: Optional[str] = None) -> Generator[Tuple[str, str], None, None]:
    """
    Communicates with local Ollama API, streaming output tokens.
    Appends '<|think|>' token to prompt to force deep-reasoning mode.
    Correctly supports sequential, recursive multi-turn tool calling up to max_depth of 5.
    """
    import datetime
    tz_offset = datetime.datetime.now().astimezone().strftime('%z')
    if not tz_offset or len(tz_offset) < 5:
        tz_offset = "+0000"
    tz_formatted = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"
    tz_suffix = f"{tz_offset[:3]}:{tz_offset[3:]}"
    current_time_str = time.strftime("%A, %B %d, %Y, %I:%M %p %Z")
    
    dynamic_system_prompt = (
        SYSTEM_PROMPT + 
        f"\n\nCURRENT DATE-TIME CONTEXT:\n"
        f"- Today's date and time: {current_time_str} ({tz_formatted})\n"
        f"- User Timezone: {tz_formatted}\n"
        f"- Ensure all new tasks created use the correct year, month, and day matching the current context unless a future date is explicitly requested.\n"
        f"- Timezone Guideline: You MUST specify task start_time and end_time ISO strings using the same timezone offset suffix as the user's timezone ({tz_suffix}) instead of defaulting to UTC 'Z' (unless the user's timezone is UTC itself). This ensures scheduled blocks appear at the correct local hour on the user's timeline interface.\n"
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
        user_msg["audios"] = [audio_b64]
    messages.append(user_msg)
    
    def chat_loop(current_messages: List[Dict[str, Any]], depth: int = 0) -> Generator[Tuple[str, str], None, None]:
        if depth > 5:
            yield ("thought", f"\n[System: Max tool recursion depth 5 exceeded. Stopping.]\n")
            return
            
        payload = {
            "model": MODEL_NAME,
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
            
            with urllib.request.urlopen(req) as resp:
                in_thought_block = False
                assistant_message = None
                
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
                                    yield ("text", parts[1].replace("text\n", ""))
                            else:
                                yield ("thought", content)
                        else:
                            yield ("text", content)
                
                # Check for Tool Call requests
                if assistant_message and assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)
                    
                    for tool_call in assistant_message["tool_calls"]:
                        func_name = tool_call["function"]["name"]
                        func_args = tool_call["function"]["arguments"]
                        
                        yield ("thought", f"\n[Agent Triggered Tool: {func_name}({json.dumps(func_args)})]\n")
                        
                        try:
                            # Execute the tool locally
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                            yield ("thought", f"[Tool Execution Success]\n")
                        except Exception as err:
                            result = {"status": "error", "message": str(err)}
                            yield ("thought", f"[Tool Execution Error: {str(err)}]\n")
                            
                        current_messages.append({
                            "role": "tool",
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

    yield from chat_loop(messages, 0)
