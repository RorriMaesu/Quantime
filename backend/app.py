# backend/app.py
import os
import sys
import time
import asyncio
import logging
import urllib.request
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import hashlib
import base64
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.serialization import load_pem_public_key, Encoding, PublicFormat

# Add workspace directory to path to ensure backend imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import init_db, get_db_connection, circuit_breaker, FirestoreThrottlingException
from backend.google_client import GoogleOAuthManager, GoogleCalendarSync, GmailParser
from backend.ollama_agent import generate_agent_stream, modify_task_time, get_current_schedule
from backend.voice_processor import pcm_to_wav, synthesize_text_to_pcm, SimpleSilenceDetector

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

@app.on_event("startup")
async def startup_event():
    """Asynchronously pre-loads the Ollama model on startup to eliminate first-use latency."""
    def preload_model():
        import urllib.request
        import json
        try:
            url = "http://localhost:11434/api/generate"
            payload = {"model": "gemma4-agent-mtp", "keep_alive": -1}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            logger.info("Ollama model pre-loaded successfully in VRAM.")
        except Exception as e:
            logger.warning(f"Failed to pre-load Ollama model: {e}")

    import threading
    threading.Thread(target=preload_model, daemon=True).start()
    threading.Thread(target=notification_poller_thread, daemon=True).start()

# =====================================================================
# Dual-Mode Firebase Initialization (With Local Mock Fallback)
# =====================================================================

firebase_app = None
db_firestore = None
FIREBASE_KEY_PATH = os.environ.get("FIREBASE_APPLICATION_CREDENTIALS", os.path.abspath(os.path.join(os.path.dirname(__file__), "firebase_key.json")))
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
    logger.info("Firebase credentials key missing. Firestore real-time sync operating in MOCK mode.")

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

def get_recent_chat_history(limit: int = 10, exclude_chat_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Loads recent chat history from SQLite, enforcing a 2-hour session decay window."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        session_cutoff = time.time() - 7200
        if exclude_chat_id:
            cursor.execute("""
                SELECT id, sender, text 
                FROM chats 
                WHERE status = 'done' AND text IS NOT NULL AND text != ''
                  AND timestamp >= ?
                  AND id != ? AND id != ?
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (session_cutoff, exclude_chat_id, f"user_{exclude_chat_id}", limit))
        else:
            cursor.execute("""
                SELECT id, sender, text 
                FROM chats 
                WHERE status = 'done' AND text IS NOT NULL AND text != ''
                  AND timestamp >= ?
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (session_cutoff, limit))
        
        rows = cursor.fetchall()
        history = []
        for row in reversed(rows):
            role = "user" if row["sender"] == "user" else "assistant"
            history.append({"role": role, "content": row["text"]})
        return history
    except Exception as e:
        logger.error(f"Failed to fetch chat history: {e}")
        return []
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
    
    # Populate chat history if empty
    if not chat_history:
        chat_history = get_recent_chat_history(limit=10, exclude_chat_id=chat_id)
    
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

class TaskUpdateSchema(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

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
    notifications_enabled: Optional[str] = 'true'
    notification_lead_minutes: Optional[str] = '15'
    notification_on_start: Optional[str] = 'true'
    notification_dnd_focus: Optional[str] = 'true'
    voice_choice: Optional[str] = 'custom_cloned'

@app.get("/api/profile")
def get_user_profile():
    """Retrieves user profile details (ID, name) and Google integration status from database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'user_id'")
        id_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'user_name'")
        name_row = cursor.fetchone()
        
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'google_refresh_token'")
        google_row = cursor.fetchone()
        is_google_linked = google_row is not None and len(google_row["value"]) > 0
        
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'notifications_enabled'")
        ne_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'notification_lead_minutes'")
        nl_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'notification_on_start'")
        ns_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'notification_dnd_focus'")
        nd_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'voice_choice'")
        voice_row = cursor.fetchone()
        
        u_id = id_row["value"] if id_row else os.environ.get("USER_ID", "user")
        u_name = name_row["value"] if name_row else os.environ.get("USER_NAME", "User")
        
        return {
            "user_id": u_id,
            "user_name": u_name,
            "is_google_linked": is_google_linked,
            "notifications_enabled": ne_row["value"] if ne_row else 'true',
            "notification_lead_minutes": nl_row["value"] if nl_row else '15',
            "notification_on_start": ns_row["value"] if ns_row else 'true',
            "notification_dnd_focus": nd_row["value"] if nd_row else 'true',
            "voice_choice": voice_row["value"] if voice_row else 'custom_cloned'
        }
    except Exception as e:
        return {
            "user_id": "user",
            "user_name": "User",
            "is_google_linked": False,
            "notifications_enabled": "true",
            "notification_lead_minutes": "15",
            "notification_on_start": "true",
            "notification_dnd_focus": "true",
            "voice_choice": "custom_cloned",
            "error": str(e)
        }
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
        if profile.notifications_enabled is not None:
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('notifications_enabled', ?)", (profile.notifications_enabled,))
        if profile.notification_lead_minutes is not None:
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('notification_lead_minutes', ?)", (profile.notification_lead_minutes,))
        if profile.notification_on_start is not None:
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('notification_on_start', ?)", (profile.notification_on_start,))
        if profile.notification_dnd_focus is not None:
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('notification_dnd_focus', ?)", (profile.notification_dnd_focus,))
        if profile.voice_choice is not None:
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('voice_choice', ?)", (profile.voice_choice,))
        conn.commit()
        return {
            "status": "success",
            "user_id": profile.user_id,
            "user_name": profile.user_name,
            "notifications_enabled": profile.notifications_enabled,
            "notification_lead_minutes": profile.notification_lead_minutes,
            "notification_on_start": profile.notification_on_start,
            "notification_dnd_focus": profile.notification_dnd_focus,
            "voice_choice": profile.voice_choice
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Database update failed: {e}")
    finally:
        conn.close()

from fastapi import File, UploadFile
import shutil

@app.post("/api/voice/clone")
async def voice_clone_endpoint(file: UploadFile = File(...)):
    """Accepts a WAV audio file, saves it, and compiles it into a VibeVoice speaker preset."""
    try:
        # Create user_voice_ref.wav in backend folder
        wav_path = os.path.join(os.path.dirname(__file__), "user_voice_ref.wav")
        with open(wav_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        logger.info(f"Saved custom reference WAV to {wav_path}")
        
        # Compile preset
        preset_out_path = os.path.join(os.path.dirname(__file__), "user_voice_ref.pt")
        from voice_processor import generate_voice_preset
        generate_voice_preset(wav_path, preset_out_path)
        
        return {"status": "success", "message": "Voice profile cloned and compiled successfully!"}
    except Exception as e:
        logger.error(f"Voice cloning failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cloning failed: {str(e)}")

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
    from backend.google_client import load_client_secrets
    secrets = load_client_secrets()
    redirect_uris = secrets.get("redirect_uris", [])
    
    default_redirect = f"{request.base_url}auth/callback"
    # If request's base URL callback is not in the registered list, fallback to the first allowed URI
    if redirect_uris and default_redirect not in redirect_uris:
        redirect_uri = redirect_uris[0]
    else:
        redirect_uri = default_redirect
        
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
            from backend.google_client import load_client_secrets
            secrets = load_client_secrets()
            redirect_uris = secrets.get("redirect_uris", [])
            
            default_redirect = f"{request.base_url}auth/callback"
            if redirect_uris and default_redirect not in redirect_uris:
                redirect_uri = redirect_uris[0]
            else:
                redirect_uri = default_redirect
                
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

@app.patch("/api/tasks/{task_id}")
def update_task_endpoint(task_id: str, payload: TaskUpdateSchema):
    """Updates a task's fields locally and mirrors to Firestore."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title, status, source_event_id, start_time, end_time FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found.")
            
        update_fields = []
        params = []
        if payload.status is not None:
            update_fields.append("status = ?")
            params.append(payload.status)
        if payload.title is not None:
            update_fields.append("title = ?")
            params.append(payload.title)
        if payload.start_time is not None:
            update_fields.append("start_time = ?")
            params.append(payload.start_time)
        if payload.end_time is not None:
            update_fields.append("end_time = ?")
            params.append(payload.end_time)
            
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update.")
            
        update_fields.append("updated_at = ?")
        params.append(time.time())
        params.append(task_id)
        
        query = f"UPDATE tasks SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        
        # If task is completed and Google Calendar sync is active, update Google Calendar
        if payload.status == "completed" and row["source_event_id"]:
            try:
                GoogleCalendarSync.patch_calendar_event(
                    row["source_event_id"], 
                    row["start_time"], 
                    row["end_time"], 
                    f"[COMPLETED] {row['title']}"
                )
            except Exception as ge:
                logger.error(f"Failed to patch calendar event on completion: {ge}")
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
        
    # Mirror update to Firestore
    if db_firestore is not None:
        try:
            task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
            task_ref.update({k: v for k, v in payload.dict().items() if v is not None})
        except Exception as fe:
            logger.error(f"Failed to mirror task update to Firestore: {fe}")
            
    return {"status": "success", "message": f"Task '{task_id}' updated successfully."}

class ProposalCommitSchema(BaseModel):
    transaction_id: str
    option_id: str

class ProposalRejectSchema(BaseModel):
    transaction_id: str

@app.get("/api/proposals/{tx_id}")
def get_proposal_options(tx_id: str):
    """Fetches staged proposal options for a specific transaction ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Cleanup expired proposals on read
        cursor.execute("DELETE FROM proposed_schedules WHERE expires_at < ?", (time.time(),))
        conn.commit()
        
        cursor.execute("SELECT option_id, description, proposed_changes FROM proposed_schedules WHERE transaction_id = ?", (tx_id,))
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Proposal transaction not found or expired.")
        options = []
        for r in rows:
            options.append({
                "option_id": r["option_id"],
                "description": r["description"],
                "proposed_changes": json.loads(r["proposed_changes"])
            })
        return {"transaction_id": tx_id, "options": options}
    finally:
        conn.close()

@app.post("/api/proposals/commit")
def commit_proposal_option(payload: ProposalCommitSchema):
    """
    Commit a staged proposal option. 
    1. Saves the current task states in state_snapshots for rollback ability.
    2. Overwrites modified tasks with the new proposed start_time and end_time.
    3. Triggers Google Calendar event syncing/patching where applicable.
    4. Deletes all staged options under this transaction ID.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Fetch the selected option
        cursor.execute("SELECT proposed_changes FROM proposed_schedules WHERE transaction_id = ? AND option_id = ?", 
                       (payload.transaction_id, payload.option_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Selected proposal option not found.")
        
        changes = json.loads(row["proposed_changes"])
        
        # 2. Get current state of all tasks database to write a snapshot
        cursor.execute("SELECT id, title, description, start_time, end_time, energy_level, constraint_type, status, source_event_id FROM tasks")
        current_tasks = [dict(r) for r in cursor.fetchall()]
        snapshot_data = json.dumps(current_tasks)
        cursor.execute("INSERT INTO state_snapshots (state_data, timestamp) VALUES (?, ?)", (snapshot_data, time.time()))
        
        # 3. Apply changes to tasks
        for change in changes:
            task_id = change["task_id"]
            new_start = change["new_start"]
            new_end = change["new_end"]
            
            # Update locally
            cursor.execute("UPDATE tasks SET start_time = ?, end_time = ?, updated_at = ? WHERE id = ?", (new_start, new_end, time.time(), task_id))
            
            # Mirror/Patch to Google Calendar if sync ID exists
            cursor.execute("SELECT source_event_id, title FROM tasks WHERE id = ?", (task_id,))
            task_row = cursor.fetchone()
            if task_row and task_row["source_event_id"]:
                try:
                    from backend.google_client import GoogleCalendarSync
                    GoogleCalendarSync.patch_calendar_event(task_row["source_event_id"], new_start, new_end, task_row["title"])
                except Exception as sync_err:
                    logger.error(f"Failed to batch patch calendar event for {task_id}: {sync_err}")
                    
        # 4. Clean up staged options
        cursor.execute("DELETE FROM proposed_schedules WHERE transaction_id = ?", (payload.transaction_id,))
        conn.commit()
        return {"status": "success", "message": f"Proposal option '{payload.option_id}' committed successfully."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/api/proposals/rollback")
def rollback_proposal():
    """
    Rolls back the schedule database to the last state snapshot.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT state_data FROM state_snapshots ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No snapshots available for rollback.")
        
        # Clear current tasks
        cursor.execute("DELETE FROM tasks")
        
        # Restore from snapshot
        restored_tasks = json.loads(row["state_data"])
        for t in restored_tasks:
            cursor.execute("""
                INSERT INTO tasks (id, title, description, start_time, end_time, energy_level, constraint_type, status, source_event_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (t["id"], t["title"], t["description"], t["start_time"], t["end_time"], t["energy_level"], t["constraint_type"], t["status"], t.get("source_event_id"), time.time(), time.time()))
            
            # Mirror/Patch back to Google Calendar if sync ID exists
            if t.get("source_event_id"):
                try:
                    from backend.google_client import GoogleCalendarSync
                    GoogleCalendarSync.patch_calendar_event(t["source_event_id"], t["start_time"], t["end_time"], t["title"])
                except Exception as sync_err:
                    logger.error(f"Failed to rollback calendar event for {t['id']}: {sync_err}")
                    
        # Delete that last snapshot
        cursor.execute("DELETE FROM state_snapshots WHERE id = (SELECT id FROM state_snapshots ORDER BY id DESC LIMIT 1)")
        conn.commit()
        return {"status": "success", "message": "Schedule rolled back to the previous snapshot."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/api/proposals/reject")
def reject_proposal(payload: ProposalRejectSchema):
    """Discards staged proposal options."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM proposed_schedules WHERE transaction_id = ?", (payload.transaction_id,))
        conn.commit()
        return {"status": "success", "message": "Proposal rejected and deleted."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


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

@app.websocket("/api/voice-chat")
async def voice_chat_websocket(websocket: WebSocket):
    await websocket.accept()
    logger.info("Voice chat WebSocket connection established.")
    
    interrupt_event = asyncio.Event()
    assistant_speaking = False
    
    async def receive_loop():
        nonlocal assistant_speaking
        try:
            while True:
                message = await websocket.receive()
                
                if "bytes" in message:
                    # Ignore binary audio stream since we use browser-native speech recognition
                    continue
                        
                elif "text" in message:
                    try:
                        msg_json = json.loads(message["text"])
                        if msg_json.get("type") == "interrupt":
                            logger.info("User interrupted (barge-in detected via message). Setting interrupt event...")
                            interrupt_event.set()
                        elif msg_json.get("type") == "prompt":
                            prompt_text = msg_json.get("prompt", "")
                            if prompt_text:
                                logger.info(f"Received speech prompt: {prompt_text}")
                                asyncio.create_task(run_agent_turn(prompt_text))
                    except Exception as je:
                        logger.error(f"Error parsing voice control message: {je}")
                        
        except WebSocketDisconnect:
            logger.info("Voice chat WebSocket disconnected.")
        except Exception as e:
            logger.error(f"Error in WebSocket receive loop: {e}")
            
    async def run_agent_turn(prompt_text: str):
        nonlocal assistant_speaking
        if assistant_speaking or not prompt_text:
            return
            
        assistant_speaking = True
        interrupt_event.clear()
        
        history = get_recent_chat_history(limit=5)
        await websocket.send_json({"type": "status", "status": "thinking"})
        
        text_buffer = ""
        sentence_buffer = ""
        
        chunk_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        def run_stream():
            try:
                generator = generate_agent_stream(prompt=prompt_text, chat_history=history, audio_b64=None)
                for channel, chunk in generator:
                    asyncio.run_coroutine_threadsafe(chunk_queue.put((channel, chunk)), loop)
            except Exception as ex:
                logger.error(f"Error in background generator thread: {ex}")
            finally:
                asyncio.run_coroutine_threadsafe(chunk_queue.put((None, None)), loop)
                
        import threading
        threading.Thread(target=run_stream, daemon=True).start()
        
        try:
            while True:
                if interrupt_event.is_set():
                    logger.info("LLM generation interrupted by user barge-in.")
                    await websocket.send_json({"type": "status", "status": "idle"})
                    break
                    
                channel, chunk = await chunk_queue.get()
                if channel is None:
                    break
                    
                if channel == "text":
                    text_buffer += chunk
                    sentence_buffer += chunk
                    await websocket.send_json({"type": "text", "text": chunk})
                    
                    if any(char in sentence_buffer for char in [".", "?", "!", "\n"]) and len(sentence_buffer.strip()) > 5:
                        clean_sentence = sentence_buffer.strip()
                        sentence_buffer = ""
                        
                        voice_choice = get_user_profile_value('voice_choice', 'custom_cloned')
                        pcm_bytes = synthesize_text_to_pcm(clean_sentence, voice=voice_choice)
                        if pcm_bytes:
                            if interrupt_event.is_set():
                                break
                            pcm_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
                            await websocket.send_json({"type": "audio", "audio": pcm_b64})
                elif channel == "thought":
                    await websocket.send_json({"type": "thought", "thought": chunk})
                            
            if sentence_buffer.strip() and not interrupt_event.is_set():
                voice_choice = get_user_profile_value('voice_choice', 'custom_cloned')
                pcm_bytes = synthesize_text_to_pcm(sentence_buffer.strip(), voice=voice_choice)
                if pcm_bytes:
                    pcm_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
                    await websocket.send_json({"type": "audio", "audio": pcm_b64})
                    
            if not interrupt_event.is_set():
                chat_id = f"voice_chat_{int(time.time() * 1000)}"
                update_chat_record(f"user_{chat_id}", "user", prompt_text, "", "done")
                update_chat_record(chat_id, "agent", text_buffer, "", "done")
                await websocket.send_json({"type": "status", "status": "idle"})
                
        except Exception as e:
            import traceback
            logger.error(f"Error in LLM voice generation loop: {e}\n{traceback.format_exc()}")
        finally:
            assistant_speaking = False
            
    await receive_loop()

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

# =====================================================================
# Notification Helper Functions & Endpoints
# =====================================================================

def get_or_create_vapid_keys():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM user_profiles WHERE key = 'vapid_private_key'")
    priv_row = cursor.fetchone()
    cursor.execute("SELECT value FROM user_profiles WHERE key = 'vapid_public_key'")
    pub_row = cursor.fetchone()
    
    if priv_row and pub_row:
        priv_pem = priv_row["value"].encode('utf-8')
        pub_pem = pub_row["value"].encode('utf-8')
        conn.close()
        return priv_pem, pub_pem
    else:
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        priv_pem = v.private_pem()
        pub_pem = v.public_pem()
        cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('vapid_private_key', ?)", (priv_pem.decode('utf-8'),))
        cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('vapid_public_key', ?)", (pub_pem.decode('utf-8'),))
        conn.commit()
        conn.close()
        return priv_pem, pub_pem

def get_vapid_public_key_b64(pub_pem: bytes) -> str:
    pub_key = load_pem_public_key(pub_pem)
    pub_bytes = pub_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(pub_bytes).decode('utf-8').rstrip('=')

def get_user_profile_value(key: str, default: str) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM user_profiles WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return row["value"]
    except Exception as e:
        logger.error(f"Error reading user profile {key}: {e}")
    finally:
        conn.close()
    return default

def get_active_focus_task_id(now_dt) -> Optional[str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, start_time, end_time, energy_level, status FROM tasks WHERE energy_level = 'crimson' AND status = 'pending'")
        rows = cursor.fetchall()
        for row in rows:
            start_dt = datetime.fromisoformat(row["start_time"].replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(row["end_time"].replace('Z', '+00:00'))
            if start_dt <= now_dt < end_dt:
                return row["id"]
    except Exception as e:
        logger.error(f"Error fetching active focus task: {e}")
    finally:
        conn.close()
    return None

def mark_notification_sent(task_id: str, start_time: str, alert_type: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO sent_notifications (task_id, start_time, alert_type, fired_at) VALUES (?, ?, ?, ?)",
            (task_id, start_time, alert_type, time.time())
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error writing to sent_notifications: {e}")
    finally:
        conn.close()

def complete_task(task_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title, start_time, end_time, status, source_event_id FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return False
            
        cursor.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            ("completed", time.time(), task_id)
        )
        
        if row["source_event_id"]:
            try:
                GoogleCalendarSync.patch_calendar_event(
                    row["source_event_id"],
                    row["start_time"],
                    row["end_time"],
                    f"[COMPLETED] {row['title']}"
                )
            except Exception as ge:
                logger.error(f"Failed to patch calendar event on completion: {ge}")
                
        conn.commit()
        
        if db_firestore is not None:
            try:
                task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
                task_ref.update({
                    "status": "completed",
                    "updated_at": time.time()
                })
            except Exception as fe:
                logger.error(f"Failed to mirror task completion to Firestore: {fe}")
                
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error completing task {task_id}: {e}")
        return False
    finally:
        conn.close()

def snooze_task_by_10m(task_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, title, start_time, end_time, status, source_event_id FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return False
        
        start_str = row["start_time"]
        end_str = row["end_time"]
        
        def add_10_mins(iso_str):
            has_z = iso_str.endswith('Z')
            clean_str = iso_str[:-1] if has_z else iso_str
            dt = datetime.fromisoformat(clean_str)
            dt_new = dt + timedelta(minutes=10)
            return dt_new.isoformat() + ('Z' if has_z else '')
            
        new_start = add_10_mins(start_str)
        new_end = add_10_mins(end_str)
        
        cursor.execute(
            "UPDATE tasks SET start_time = ?, end_time = ?, updated_at = ? WHERE id = ?",
            (new_start, new_end, time.time(), task_id)
        )
        
        if row["source_event_id"]:
            try:
                GoogleCalendarSync.patch_calendar_event(
                    row["source_event_id"],
                    new_start,
                    new_end,
                    row["title"]
                )
            except Exception as ge:
                logger.error(f"Failed to patch calendar event on snooze: {ge}")
                
        conn.commit()
        
        if db_firestore is not None:
            try:
                task_ref = db_firestore.collection("users").document(MOCK_USER_ID).collection("tasks").document(task_id)
                task_ref.update({
                    "start_time": new_start,
                    "end_time": new_end,
                    "updated_at": time.time()
                })
            except Exception as fe:
                logger.error(f"Failed to mirror task snooze to Firestore: {fe}")
                
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error snoozing task {task_id}: {e}")
        return False
    finally:
        conn.close()

async def check_and_send_notifications():
    try:
        enabled = get_user_profile_value('notifications_enabled', 'true') == 'true'
        if not enabled:
            return
            
        lead_mins = int(get_user_profile_value('notification_lead_minutes', '15'))
        on_start = get_user_profile_value('notification_on_start', 'true') == 'true'
        dnd_focus = get_user_profile_value('notification_dnd_focus', 'true') == 'true'
        
        now_dt = datetime.now().astimezone()
        active_focus_id = get_active_focus_task_id(now_dt) if dnd_focus else None
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, start_time, end_time, energy_level, status FROM tasks WHERE status = 'pending'")
        tasks = [dict(r) for r in cursor.fetchall()]
        
        cursor.execute("SELECT task_id, start_time, alert_type FROM sent_notifications")
        sent_set = {(r["task_id"], r["start_time"], r["alert_type"]) for r in cursor.fetchall()}
        conn.close()
        
        from py_vapid import Vapid
        priv_pem, _ = get_or_create_vapid_keys()
        vapid_key = Vapid.from_pem(priv_pem)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT subscription_json FROM push_subscriptions")
        subscriptions = [json.loads(r["subscription_json"]) for r in cursor.fetchall()]
        conn.close()
        
        if not subscriptions:
            return
            
        for T in tasks:
            try:
                start_dt = datetime.fromisoformat(T["start_time"].replace('Z', '+00:00'))
            except Exception:
                continue
                
            diff_seconds = (start_dt - now_dt).total_seconds()
            alert_to_send = None
            
            if 0 < diff_seconds <= (lead_mins * 60):
                if (T["id"], T["start_time"], "lead") not in sent_set:
                    alert_to_send = "lead"
            elif -300 <= diff_seconds <= 10:
                if on_start and (T["id"], T["start_time"], "start") not in sent_set:
                    alert_to_send = "start"
                    
            if alert_to_send:
                is_silent = False
                if dnd_focus and active_focus_id:
                    if T["id"] != active_focus_id and T["energy_level"] != "crimson":
                        is_silent = True
                        
                title = f"Upcoming: {T['title']}" if alert_to_send == "lead" else f"Starting now: {T['title']}"
                body = f"Starts in {lead_mins} minutes." if alert_to_send == "lead" else "It's time to begin!"
                
                payload = json.dumps({
                    "title": title,
                    "body": body,
                    "taskId": T["id"],
                    "alertType": alert_to_send,
                    "silent": is_silent
                })
                
                for sub_info in subscriptions:
                    try:
                        webpush(
                            subscription_info=sub_info,
                            data=payload,
                            vapid_private_key=vapid_key,
                            vapid_claims={"sub": "mailto:admin@quantime.app"}
                        )
                    except Exception as e:
                        logger.warning(f"Failed to deliver webpush to subscription: {e}")
                        
                mark_notification_sent(T["id"], T["start_time"], alert_to_send)
    except Exception as e:
        logger.error("Error checking and sending notifications:", exc_info=True)

def notification_poller_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def poller_loop():
        logger.info("Notification background poller loop started.")
        while True:
            try:
                await check_and_send_notifications()
            except Exception as e:
                logger.error(f"Error in check_and_send_notifications: {e}")
            await asyncio.sleep(60)
            
    loop.run_until_complete(poller_loop())

@app.get("/api/notifications/vapid-public-key")
def get_vapid_public_key():
    _, pub_pem = get_or_create_vapid_keys()
    pub_key_b64 = get_vapid_public_key_b64(pub_pem)
    return {"publicKey": pub_key_b64}

class SubscriptionPayload(BaseModel):
    subscription: Dict[str, Any]

@app.post("/api/notifications/subscribe")
def subscribe_notifications(payload: SubscriptionPayload):
    sub = payload.subscription
    endpoint = sub.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="Invalid subscription data")
    
    sub_id = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO push_subscriptions (id, subscription_json, created_at) VALUES (?, ?, ?)",
        (sub_id, json.dumps(sub), time.time())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "id": sub_id}

@app.post("/api/notifications/test")
def test_notifications():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subscription_json FROM push_subscriptions")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        raise HTTPException(status_code=400, detail="No push subscriptions registered. Please subscribe first.")
        
    from py_vapid import Vapid
    priv_pem, _ = get_or_create_vapid_keys()
    vapid_key = Vapid.from_pem(priv_pem)
    
    payload_data = json.dumps({
        "title": "Quantime Test Alert",
        "body": "Your notification configuration is working correctly!",
        "taskId": "test-id",
        "alertType": "test",
        "silent": False
    })
    
    success_count = 0
    fail_count = 0
    
    for row in rows:
        sub_info = json.loads(row["subscription_json"])
        try:
            webpush(
                subscription_info=sub_info,
                data=payload_data,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": "mailto:admin@quantime.app"}
            )
            success_count += 1
        except Exception as e:
            logger.warning(f"Failed to send test push: {e}")
            fail_count += 1
            
    return {"status": "success", "sent": success_count, "failed": fail_count}

class ActionPayload(BaseModel):
    taskId: str
    action: str

@app.post("/api/notifications/action")
def handle_notification_action(payload: ActionPayload):
    if payload.action == "complete":
        success = complete_task(payload.taskId)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to complete task")
        return {"status": "success", "message": f"Task {payload.taskId} completed."}
    elif payload.action == "snooze":
        success = snooze_task_by_10m(payload.taskId)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to snooze task")
        return {"status": "success", "message": f"Task {payload.taskId} snoozed by 10 minutes."}
    else:
        raise HTTPException(status_code=400, detail="Invalid action type")

# Serve static assets from frontend/dist
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_dist_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(frontend_dist_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist_path, "assets")), name="assets")
    
    @app.get("/{catchall:path}")
    def serve_frontend(catchall: str):
        if catchall.startswith("api") or catchall.startswith("auth"):
            raise HTTPException(status_code=404, detail="Not Found")
        full_path = os.path.join(frontend_dist_path, catchall)
        if os.path.isfile(full_path):
            return FileResponse(full_path)
        return FileResponse(os.path.join(frontend_dist_path, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
