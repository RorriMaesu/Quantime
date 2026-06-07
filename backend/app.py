# backend/app.py
import os
import sys
import time
import asyncio
import logging
import urllib.request
import json
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add workspace directory to path to ensure backend imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import init_db, get_db_connection, circuit_breaker, FirestoreThrottlingException
from backend.google_client import GoogleOAuthManager, GoogleCalendarSync, GmailParser
from backend.ollama_agent import generate_agent_stream, modify_task_time, get_current_schedule

logger = logging.getLogger("quantime.gateway")
logging.basicConfig(level=logging.INFO)

# Initialize FastAPI Application
app = FastAPI(title="Quantime Gateway API", version="1.1")

# Configure Cross-Origin Resource Sharing (CORS) for development UI access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize local SQLite database schemas
init_db()

# =====================================================================
# Dual-Mode Firebase Initialization (With Local Mock Fallback)
# =====================================================================

firebase_app = None
db_firestore = None
FIREBASE_KEY_PATH = os.environ.get("FIREBASE_APPLICATION_CREDENTIALS", "firebase_key.json")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "quantime-pwa-mock")

def get_current_user_id() -> str:
    """Retrieves the active user ID from database or environment configurations."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'user_id'")
        row = cursor.fetchone()
        conn.close()
        if row:
            return row["value"]
    except Exception:
        pass
    return os.environ.get("USER_ID", "user")

MOCK_USER_ID = get_current_user_id()

if os.path.exists(FIREBASE_KEY_PATH) or os.environ.get("FIREBASE_APPLICATION_CREDENTIALS"):
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        
        if os.path.exists(FIREBASE_KEY_PATH):
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
        else:
            cred = credentials.ApplicationDefault()
            
        firebase_app = firebase_admin.initialize_app(cred, {
            'projectId': FIREBASE_PROJECT_ID
        })
        db_firestore = firestore.client()
        logger.info(f"Firebase Admin initialized in LIVE mode on project: {FIREBASE_PROJECT_ID}")
    except Exception as e:
        logger.error(f"Firebase initialization exception: {e}. Defaulting to MOCK mode.")
else:
    logger.warning("Firebase credentials key missing. Firestore real-time sync operating in MOCK mode.")

# =====================================================================
# Real-Time Firestore Synchronization Logic
# =====================================================================

def update_firestore_document(doc_ref, data: Dict[str, Any], force: bool = False) -> None:
    """Updates Firestore document, checking the Circuit Breaker rate limit first."""
    if db_firestore is None:
        logger.info(f"[Mock Firestore Update] Ref: {doc_ref.id if hasattr(doc_ref, 'id') else 'Doc'} -> {data}")
        return
        
    try:
        if not force:
            # Enforce 5 writes per 10 seconds limit
            circuit_breaker.consume()
        doc_ref.update(data)
    except FirestoreThrottlingException as throttle_err:
        logger.warning(f"Circuit Breaker blocked write: {throttle_err}")
    except Exception as e:
        logger.error(f"Firestore update failed: {e}")

def update_chat_record(chat_id: str, sender: str = 'agent', text: str = "", thoughts: str = "", status: str = "processing"):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO chats (id, sender, text, thoughts, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chat_id, sender, text, thoughts, status, time.time()))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to write chat record to SQLite: {e}")
    finally:
        conn.close()

async def handle_agent_processing(chat_id: str, prompt: str, chat_history: List[Dict[str, str]], doc_ref) -> None:
    """
    Orchestration loop that calls the speculative reasoning agent,
    buffering stream chunks to respect Circuit Breaker thresholds.
    """
    logger.info(f"Processing chat ID {chat_id}...")
    thoughts_buf = ""
    text_buf = ""
    last_write_time = time.time()
    
    # Store user message locally to sync chat context
    update_chat_record(f"user_{chat_id}", 'user', prompt, '', 'done')
    
    # Immediately flip status to processing (forces write bypass to guarantee state transition)
    update_chat_record(chat_id, 'agent', '', '', 'processing')
    if doc_ref is not None:
        update_firestore_document(doc_ref, {"status": "processing"}, force=True)
    
    # Run Ollama streaming response loop
    for channel, chunk in generate_agent_stream(prompt, chat_history):
        if channel == "thought":
            thoughts_buf += chunk
        elif channel == "text":
            text_buf += chunk
            
        # Throttled write every 2 seconds
        if time.time() - last_write_time > 2.0:
            update_chat_record(chat_id, 'agent', text_buf, thoughts_buf, 'processing')
            if doc_ref is not None:
                update_firestore_document(doc_ref, {
                    "text": text_buf,
                    "thoughts": thoughts_buf
                })
            last_write_time = time.time()
            await asyncio.sleep(0.01) # Yield execution thread
            
    # Final finalize write (forces writing to avoid losing completion tail)
    update_chat_record(chat_id, 'agent', text_buf, thoughts_buf, 'done')
    if doc_ref is not None:
        update_firestore_document(doc_ref, {
            "text": text_buf,
            "thoughts": thoughts_buf,
            "status": "done"
        }, force=True)
    
    # Record transaction to semantic vector memory
    try:
        from backend.memory_store import memory_store
        memory_store.add_interaction(
            doc_id=chat_id,
            text_content=f"User: {prompt}\nAgent: {text_buf}",
            metadata={"source": "firestore_chat", "user_id": MOCK_USER_ID}
        )
    except Exception as e:
        logger.error(f"Failed to record semantic memory: {e}")

def on_chats_snapshot(col_snapshot, changes, read_time):
    """Snapshot Listener for new/pending chat queries."""
    for change in changes:
        if change.type.name in ['ADDED', 'MODIFIED']:
            doc = change.document
            data = doc.to_dict()
            
            if data.get("sender") == "user" and data.get("status") == "pending":
                chat_id = doc.id
                prompt = data.get("text", "")
                
                # Expose background execution runner
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    handle_agent_processing(chat_id, prompt, [], doc.reference)
                )

def on_tasks_snapshot(col_snapshot, changes, read_time):
    """Snapshot Listener for lock-screen task completions/snoozes from the PWA."""
    for change in changes:
        if change.type.name == 'MODIFIED':
            doc = change.document
            data = doc.to_dict()
            task_id = doc.id
            new_status = data.get("status")
            
            if new_status in ["completed", "snoozed"]:
                logger.info(f"Task status shift detected via Firestore: {task_id} -> {new_status}")
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Check for Snooze operations
                if new_status == "snoozed":
                    cursor.execute("SELECT start_time, end_time, title, source_event_id FROM tasks WHERE id = ?", (task_id,))
                    row = cursor.fetchone()
                    if row:
                        from datetime import datetime, timedelta
                        try:
                            start_dt = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
                            end_dt = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
                            new_start = (start_dt + timedelta(minutes=15)).isoformat()
                            new_end = (end_dt + timedelta(minutes=15)).isoformat()
                            
                            cursor.execute("""
                                UPDATE tasks SET start_time = ?, end_time = ?, updated_at = ? WHERE id = ?
                            """, (new_start, new_end, time.time(), task_id))
                            logger.info(f"Task '{row['title']}' snoozed by 15 mins locally.")
                            
                            if row["source_event_id"]:
                                GoogleCalendarSync.patch_calendar_event(row["source_event_id"], new_start, new_end, row["title"])
                        except Exception as dt_err:
                            logger.error(f"Failed to execute snooze shift: {dt_err}")
                else:
                    cursor.execute("UPDATE tasks SET status = 'completed', updated_at = ? WHERE id = ?", (time.time(), task_id))
                    cursor.execute("SELECT source_event_id, title, start_time, end_time FROM tasks WHERE id = ?", (task_id,))
                    row = cursor.fetchone()
                    # Mark completed status updates back to Google Calendar if sync event exists
                    if row and row["source_event_id"]:
                        # Prepend [COMPLETED] to calendar event summary
                        GoogleCalendarSync.patch_calendar_event(row["source_event_id"], row["start_time"], row["end_time"], f"[COMPLETED] {row['title']}")
                        
                conn.commit()
                conn.close()

# Start background sync loops
if db_firestore is not None:
    try:
        chats_query = db_firestore.collection("users").document(MOCK_USER_ID).collection("chats")
        tasks_query = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks")
        
        chats_watch = chats_query.on_snapshot(on_chats_snapshot)
        tasks_watch = tasks_query.on_snapshot(on_tasks_snapshot)
        logger.info("Outbound Snapshot Listeners registered to Google Firestore.")
    except Exception as e:
        logger.error(f"Failed to register on_snapshot listeners: {e}")

# =====================================================================
# REST Router Endpoints
# =====================================================================

class TaskSchema(BaseModel):
    id: str
    title: str
    description: Optional[str] = ""
    start_time: str
    end_time: str
    energy_level: str = "none"
    constraint_type: str = "soft"
    status: str = "pending"

@app.get("/health")
def health_check():
    """
    Exposes diagnostic status on database connection integrity,
    Ollama service connectivity, and current CircuitBreaker token capacities.
    """
    db_connected = False
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        if cursor.fetchone()[0] == 1:
            db_connected = True
        conn.close()
    except Exception:
        db_connected = False
        
    ollama_connected = False
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                ollama_connected = True
    except Exception:
        ollama_connected = False
        
    return {
        "status": "healthy" if (db_connected and ollama_connected) else "degraded",
        "database_connected": db_connected,
        "ollama_connected": ollama_connected,
        "circuit_breaker_tokens": round(circuit_breaker.tokens, 2),
        "firebase_mode": "LIVE" if db_firestore is not None else "MOCK"
    }

class ProfileSchema(BaseModel):
    user_id: str
    user_name: str

@app.get("/api/profile")
def get_user_profile():
    """Retrieves user profile details (ID, name) from database or fallback configuration."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'user_id'")
        id_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'user_name'")
        name_row = cursor.fetchone()
        
        u_id = id_row["value"] if id_row else os.environ.get("USER_ID", "user")
        u_name = name_row["value"] if name_row else os.environ.get("USER_NAME", "User")
        return {"user_id": u_id, "user_name": u_name}
    except Exception as e:
        return {"user_id": "user", "user_name": "User", "error": str(e)}
    finally:
        conn.close()

@app.post("/api/profile")
def update_user_profile(profile: ProfileSchema):
    """Updates user profile properties in the local configuration database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('user_id', ?)", (profile.user_id,))
        cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('user_name', ?)", (profile.user_name,))
        conn.commit()
        return {"status": "success", "user_id": profile.user_id, "user_name": profile.user_name}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Database update failed: {e}")
    finally:
        conn.close()

class CredentialsSchema(BaseModel):
    client_id: str
    client_secret: str
    project_id: str
    auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    token_uri: str = "https://oauth2.googleapis.com/token"

# Global state for tracking model download and compilation progress
pull_progress = {
    "status": "idle", # "idle", "pulling", "creating", "completed", "failed"
    "completed": 0,
    "total": 0,
    "percent": 0.0,
    "detail": ""
}

def get_gpu_metadata():
    import subprocess
    gpu_name = "CPU Only / Unknown"
    vram_gb = 0.0
    
    # 1. Try running nvidia-smi
    try:
        smi_mem = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        smi_name = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        if smi_mem and smi_name:
            vram_mb = float(smi_mem.decode("utf-8").strip())
            vram_gb = round(vram_mb / 1024.0, 1)
            gpu_name = smi_name.decode("utf-8").strip()
            return {"name": gpu_name, "vram": vram_gb}
    except Exception:
        pass

    # 2. Try running wmic on Windows
    if os.name == 'nt':
        try:
            wmic_out = subprocess.check_output(
                ["wmic", "path", "win32_videocontroller", "get", "AdapterRAM,Name", "/format:list"],
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            lines = wmic_out.decode("utf-8", errors="ignore").splitlines()
            temp_name = ""
            temp_ram = 0
            for line in lines:
                if line.startswith("Name="):
                    temp_name = line.split("=", 1)[1].strip()
                elif line.startswith("AdapterRAM="):
                    ram_val = line.split("=", 1)[1].strip()
                    if ram_val.isdigit():
                        temp_ram = int(ram_val)
            
            if temp_name:
                gpu_name = temp_name
                if temp_ram > 0:
                    vram_gb = round(temp_ram / (1024**3), 1)
                
                # Apply wrap-around heuristic workaround
                if "RTX 5060" in gpu_name:
                    vram_gb = 16.0
                elif "RTX 5070" in gpu_name:
                    vram_gb = 12.0
                elif "RTX 5080" in gpu_name:
                    vram_gb = 16.0
                elif "RTX 5090" in gpu_name:
                    vram_gb = 24.0
                elif "RTX 4090" in gpu_name:
                    vram_gb = 24.0
                elif "RTX 4080" in gpu_name:
                    vram_gb = 16.0
                elif "RTX 4070" in gpu_name:
                    vram_gb = 12.0
                elif "RTX 3090" in gpu_name:
                    vram_gb = 24.0
                elif "RTX 3080" in gpu_name:
                    vram_gb = 10.0
                elif "RTX 3060" in gpu_name:
                    vram_gb = 12.0
        except Exception:
            pass

    return {"name": gpu_name, "vram": vram_gb}

def run_model_setup_background(model_tag: str):
    global pull_progress
    pull_progress = {
        "status": "pulling",
        "completed": 0,
        "total": 0,
        "percent": 0.0,
        "detail": f"Initializing pull for {model_tag}..."
    }
    
    try:
        # 1. Update Modelfile
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        modelfile_path = os.path.join(base_dir, "Modelfile")
        if os.path.exists(modelfile_path):
            with open(modelfile_path, "r") as f:
                content = f.read()
            new_lines = []
            replaced = False
            for line in content.splitlines():
                if line.startswith("FROM "):
                    new_lines.append(f"FROM {model_tag}")
                    replaced = True
                else:
                    new_lines.append(line)
            if not replaced:
                new_lines.insert(0, f"FROM {model_tag}")
            with open(modelfile_path, "w") as f:
                f.write("\n".join(new_lines) + "\n")
        
        # 2. Pull model via local Ollama API
        import urllib.request
        import json
        
        pull_url = "http://localhost:11434/api/pull"
        payload = {"name": model_tag, "stream": True}
        
        req = urllib.request.Request(
            pull_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req) as resp:
            for line in resp:
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                status_text = data.get("status", "")
                completed = data.get("completed", 0)
                total = data.get("total", 0)
                
                percent = 0.0
                if total > 0:
                    percent = round((completed / total) * 100, 1)
                
                pull_progress = {
                    "status": "pulling",
                    "completed": completed,
                    "total": total,
                    "percent": percent,
                    "detail": f"Pulling weights: {status_text}"
                }
        
        # 3. Create Custom Speculative decoding model
        pull_progress["status"] = "creating"
        pull_progress["detail"] = "Compiling speculative decoding gemma4-agent-mtp model..."
        
        import subprocess
        cmd = ["ollama", "create", "gemma4-agent-mtp", "-f", modelfile_path]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        for stdout_line in iter(process.stdout.readline, ""):
            pull_progress["detail"] = f"Compiling: {stdout_line.strip()}"
            
        process.stdout.close()
        return_code = process.wait()
        
        if return_code == 0:
            pull_progress = {
                "status": "completed",
                "completed": 1,
                "total": 1,
                "percent": 100.0,
                "detail": "Successfully compiled speculative decoding agent gemma4-agent-mtp!"
            }
        else:
            pull_progress = {
                "status": "failed",
                "completed": 0,
                "total": 0,
                "percent": 0.0,
                "detail": f"Ollama model compilation failed with exit code {return_code}."
            }
            
    except Exception as e:
        logger.error(f"Error in model setup task: {e}")
        pull_progress = {
            "status": "failed",
            "completed": 0,
            "total": 0,
            "percent": 0.0,
            "detail": f"Setup failed: {str(e)}"
        }

class PullModelSchema(BaseModel):
    model: str

@app.get("/api/setup/hardware")
def get_hardware_info():
    """Queries and returns host GPU name and VRAM size."""
    return get_gpu_metadata()

@app.get("/api/setup/status")
def get_setup_status():
    """Checks configuration status including credentials and Ollama models."""
    from backend.google_client import CREDENTIALS_FILE
    has_credentials = os.path.exists(CREDENTIALS_FILE)
    
    # Check if custom model exists
    has_model = False
    try:
        import urllib.request
        import json
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
            for model in tags.get("models", []):
                if "gemma4-agent-mtp" in model.get("name", ""):
                    has_model = True
                    break
    except Exception:
        pass
        
    return {
        "has_credentials": has_credentials,
        "has_model": has_model
    }

@app.post("/api/setup/pull-model")
def pull_model_endpoint(payload: PullModelSchema, background_tasks: BackgroundTasks):
    """Triggers background model download and custom agent compilation."""
    global pull_progress
    if pull_progress["status"] in ["pulling", "creating"]:
        raise HTTPException(status_code=400, detail="A model setup task is already in progress.")
        
    background_tasks.add_task(run_model_setup_background, payload.model)
    return {"status": "started"}

@app.get("/api/setup/pull-status")
def get_pull_status_endpoint():
    """Returns the current model pull and compilation status."""
    global pull_progress
    return pull_progress

@app.post("/api/setup/credentials")
def save_setup_credentials(creds: CredentialsSchema):
    """Saves Google OAuth client credentials dynamically."""
    from backend.google_client import CREDENTIALS_FILE
    creds_dict = {
        "web": {
            "client_id": creds.client_id,
            "project_id": creds.project_id,
            "auth_uri": creds.auth_uri,
            "token_uri": creds.token_uri,
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": creds.client_secret,
            "redirect_uris": ["http://localhost:8000/auth/callback"]
        }
    }
    try:
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds_dict, f, indent=2)
        logger.info("Successfully updated credentials.json dynamically!")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save credentials.json: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save credentials: {e}")

@app.get("/auth/url")
def get_oauth_url(request: Request, origin: Optional[str] = None):
    """Exposes consent URL redirect parameters, passing target origin via OAuth state."""
    redirect_uri = f"{request.base_url}auth/callback"
    url = GoogleOAuthManager.get_auth_url(redirect_uri, state=origin)
    return {"url": url}

@app.get("/auth/callback")
def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    expires_in: Optional[int] = None
):
    """Google OAuth redirect loopback interceptor callback endpoint supporting both Direct & Proxy modes."""
    try:
        if access_token:
            # Proxy Mode Callback: Token payload received directly
            tokens = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in if expires_in else 3600
            }
            GoogleOAuthManager.save_tokens(tokens)
        elif code:
            # Direct Mode Callback: Exchange code locally using client secrets
            redirect_uri = f"{request.base_url}auth/callback"
            tokens = GoogleOAuthManager.exchange_code_for_tokens(code, redirect_uri)
            access_token = tokens.get("access_token")
        else:
            raise HTTPException(status_code=400, detail="Missing authorization parameters.")

        if access_token:
            profile = GoogleOAuthManager.fetch_user_profile(access_token)
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('user_id', ?)", (profile["user_id"],))
                cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('user_name', ?)", (profile["user_name"],))
                conn.commit()
                logger.info(f"User profile dynamically synchronized: {profile}")
            except Exception as db_err:
                logger.error(f"Failed to persist Google profile: {db_err}")
            finally:
                conn.close()

        # Seed Google events
        GoogleCalendarSync.sync_next_7_days()
        
        # Default fallback redirect
        redirect_url = "http://localhost:5173/?auth=success"
        if state:
            clean_origin = state.strip().rstrip('/')
            if clean_origin.startswith("http://") or clean_origin.startswith("https://"):
                redirect_url = f"{clean_origin}/?auth=success"
                
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        logger.error(f"Callback authentication failed: {e}")
        return JSONResponse(status_code=400, content={"error": f"Authentication failed: {e}"})

@app.post("/api/sync")
def trigger_calendar_sync():
    """Manual sync action endpoint."""
    events = GoogleCalendarSync.sync_next_7_days()
    return {"status": "success", "synced_count": len(events), "events": events}

@app.get("/api/public-ip")
def get_public_ip():
    """Queries external checker dynamically to determine the host's public IP address."""
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json")
        with urllib.request.urlopen(req, timeout=3.0) as response:
            res = json.loads(response.read().decode("utf-8"))
            return {"public_ip": res.get("ip", "Unknown")}
    except Exception as e:
        logger.error(f"Failed to fetch public IP: {e}")
        return {"public_ip": "Unknown", "error": str(e)}

@app.get("/api/tasks")
def list_tasks(start_date: str = "2026-06-01T00:00:00Z", end_date: str = "2026-06-30T23:59:59Z"):
    """Exposes local schedule list."""
    tasks = get_current_schedule(start_date, end_date)
    return {"tasks": tasks}

@app.post("/api/tasks")
def create_task(task: TaskSchema):
    """Registers task item locally and mirrors to Firestore."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO tasks (id, title, description, start_time, end_time, energy_level, constraint_type, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task.id, task.title, task.description, task.start_time, task.end_time, task.energy_level, task.constraint_type, task.status, time.time(), time.time()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Conflict: Task ID already exists.")
    conn.close()
    
    # Mirror update to Firestore
    if db_firestore is not None:
        task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task.id)
        task_ref.set(task.dict())
        
    return {"status": "success", "task": task.dict()}

@app.delete("/api/tasks/{task_id}")
def delete_task_endpoint(task_id: str):
    """Deletes a task from the local database and mirrors the deletion to Firestore."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found.")
            
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        cursor.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (task_id, task_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
        
    # Mirror deletion to Firestore
    if db_firestore is not None:
        try:
            task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
            task_ref.delete()
        except Exception as fe:
            logger.error(f"Failed to mirror task deletion to Firestore: {fe}")
            
    return {"status": "success", "message": f"Task '{row['title']}' deleted successfully."}

@app.get("/api/chats")
def list_chats():
    """Retrieves all chat interactions from local SQLite."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, sender, text, thoughts, status, timestamp FROM chats ORDER BY timestamp ASC")
        rows = cursor.fetchall()
        chats = []
        for r in rows:
            chats.append({
                "id": r["id"],
                "sender": r["sender"],
                "text": r["text"] or "",
                "thoughts": r["thoughts"] or "",
                "status": r["status"],
                "timestamp": int(r["timestamp"] * 1000)
            })
        return {"chats": chats}
    except Exception as e:
        logger.error(f"Failed to query chats: {e}")
        return {"chats": []}
    finally:
        conn.close()

@app.post("/api/chats")
def handle_chat_message(prompt: str, background_tasks: BackgroundTasks):
    """Unified chat message endpoint. Commits user message to local database and spawns agentic processing thread."""
    chat_id = f"chat_{int(time.time() * 1000)}"
    
    # Store user query message first
    update_chat_record(chat_id=f"user_{chat_id}", sender="user", text=prompt, thoughts="", status="done")
    
    # Spawn background task
    background_tasks.add_task(
        handle_agent_processing,
        chat_id,
        prompt,
        [],
        None
    )
    return {"status": "processing", "chat_id": chat_id}

@app.delete("/api/chats")
def delete_chats_endpoint():
    """Truncates the local chats table and clears history in Firestore (if active)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM chats")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
        
    # Truncate Firestore chats subcollection
    if db_firestore is not None:
        try:
            chats_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("chats")
            docs = chats_ref.stream()
            for doc in docs:
                doc.reference.delete()
        except Exception as fe:
            logger.error(f"Failed to clear Firestore chats subcollection: {fe}")
            
    return {"status": "success", "message": "Chat logs cleared successfully."}

@app.get("/api/gmail")
def list_unread_emails():
    """Unread Gmail updates inspection endpoint."""
    emails = GmailParser.get_unread_updates()
    return {"unread_emails": emails}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
