# backend/google_client.py
import os
import json
import time
import urllib.request
import urllib.parse
import re
import logging
from typing import Dict, Any, List, Optional
from cryptography.fernet import Fernet
from backend.database import get_db_connection

# Attempt to load standard Google authentication libraries for robustness
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

logger = logging.getLogger("quantime.google_client")
logging.basicConfig(level=logging.INFO)

# Config files (Expected locally)
CREDENTIALS_FILE = "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.profile"
]

def get_fernet_cipher() -> Fernet:
    """
    Retrieves or generates the unique Fernet encryption key stored in SQLite.
    Encrypts/Decrypts OAuth tokens to prevent plain text disk leaks.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM user_profiles WHERE key = 'fernet_key'")
    row = cursor.fetchone()
    
    # Check if a custom key is specified in the environment variables
    env_key = os.environ.get("FERNET_KEY")
    if env_key:
        key = env_key.encode()
        # Persist env key if database doesn't have it
        if not row:
            cursor.execute("INSERT INTO user_profiles (key, value) VALUES ('fernet_key', ?)", (env_key,))
            conn.commit()
    else:
        if row:
            key = row["value"].encode()
        else:
            key = Fernet.generate_key()
            cursor.execute("INSERT INTO user_profiles (key, value) VALUES ('fernet_key', ?)", (key.decode(),))
            conn.commit()
            
    conn.close()
    return Fernet(key)

def load_client_secrets() -> Dict[str, Any]:
    """Loads client secrets from credentials.json or returns mock profiles."""
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
            if "web" in data:
                return data["web"]
            elif "installed" in data:
                return data["installed"]
            return data
    else:
        logger.warning("Google credentials.json missing. Operating Google integrations in MOCK mode.")
        return {
            "client_id": "MOCK_CLIENT_ID",
            "client_secret": "MOCK_CLIENT_SECRET",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }

class GoogleOAuthManager:
    """Manages encryption, storage, and token refreshes for Google Workspace APIs."""
    
    @staticmethod
    def get_auth_url(redirect_uri: str, state: Optional[str] = None) -> str:
        """Generates Google consent authentication URL."""
        if GOOGLE_LIBS_AVAILABLE and os.path.exists(CREDENTIALS_FILE):
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_FILE,
                scopes=SCOPES,
                redirect_uri=redirect_uri
            )
            kwargs = {'prompt': 'consent', 'access_type': 'offline'}
            if state:
                kwargs['state'] = state
            auth_url, _ = flow.authorization_url(**kwargs)
            return auth_url
        else:
            # Fallback HTTP direct constructor
            secrets = load_client_secrets()
            params = {
                "client_id": secrets["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent"
            }
            if state:
                params["state"] = state
            return f"{secrets.get('auth_uri', 'https://accounts.google.com/o/oauth2/auth')}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def exchange_code_for_tokens(code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchanges authorization code for access and refresh tokens."""
        if GOOGLE_LIBS_AVAILABLE and os.path.exists(CREDENTIALS_FILE):
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_FILE,
                scopes=SCOPES,
                redirect_uri=redirect_uri
            )
            flow.fetch_token(code=code)
            creds = flow.credentials
            tokens = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": int(creds.expiry.timestamp() - time.time()) if creds.expiry else 3600
            }
        else:
            # Native REST endpoint exchange fallback
            secrets = load_client_secrets()
            payload = {
                "code": code,
                "client_id": secrets["client_id"],
                "client_secret": secrets["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(
                secrets.get("token_uri", "https://oauth2.googleapis.com/token"),
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                tokens = json.loads(resp.read().decode("utf-8"))
                
        GoogleOAuthManager.save_tokens(tokens)
        return tokens

    @staticmethod
    def save_tokens(tokens: Dict[str, Any]) -> None:
        """Encrypts and persists access/refresh tokens in SQLite database."""
        cipher = get_fernet_cipher()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Encrypt Access Token
        if "access_token" in tokens:
            enc_access = cipher.encrypt(tokens["access_token"].encode()).decode()
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('google_access_token', ?)", (enc_access,))
            expiry = time.time() + tokens.get("expires_in", 3600)
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('google_token_expiry', ?)", (str(expiry),))
            
        # Encrypt Refresh Token (Google only returns this on initial consent)
        if "refresh_token" in tokens and tokens["refresh_token"]:
            enc_refresh = cipher.encrypt(tokens["refresh_token"].encode()).decode()
            cursor.execute("INSERT OR REPLACE INTO user_profiles (key, value) VALUES ('google_refresh_token', ?)", (enc_refresh,))
            
        conn.commit()
        conn.close()

    @staticmethod
    def get_valid_access_token() -> Optional[str]:
        """Decrypts and returns the active access token, refreshing it if expired."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'google_access_token'")
        access_row = cursor.fetchone()
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'google_token_expiry'")
        expiry_row = cursor.fetchone()
        
        if not access_row or not expiry_row:
            conn.close()
            return None
            
        cipher = get_fernet_cipher()
        access_token = cipher.decrypt(access_row["value"].encode()).decode()
        expiry = float(expiry_row["value"])
        
        if time.time() < expiry - 60:  # 60 second clock skew buffer
            conn.close()
            return access_token
            
        # Token expired, retrieve refresh token
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'google_refresh_token'")
        refresh_row = cursor.fetchone()
        if not refresh_row:
            conn.close()
            return None
            
        refresh_token = cipher.decrypt(refresh_row["value"].encode()).decode()
        conn.close()
        
        # Exchange refresh token for a new access token
        try:
            logger.info("Access token expired. Requesting refresh...")
            secrets = load_client_secrets()
            payload = {
                "client_id": secrets["client_id"],
                "client_secret": secrets["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(
                secrets.get("token_uri", "https://oauth2.googleapis.com/token"),
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                new_tokens = json.loads(resp.read().decode("utf-8"))
                
            GoogleOAuthManager.save_tokens(new_tokens)
            return GoogleOAuthManager.get_valid_access_token()
        except Exception as e:
            logger.error(f"Failed to refresh Google OAuth token: {e}")
            return None

    @staticmethod
    def fetch_user_profile(token: str) -> Dict[str, Any]:
        """
        Queries the Google UserInfo API using the access token.
        Retrieves the profile fields to dynamically identify the user.
        """
        url = "https://www.googleapis.com/oauth2/v3/userinfo"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                profile = json.loads(resp.read().decode("utf-8"))
                return {
                    "user_id": profile.get("sub", "user"),
                    "user_name": profile.get("name", "User")
                }
        except Exception as e:
            logger.error(f"Failed to fetch Google user profile: {e}")
            return {"user_id": "user", "user_name": "User"}


class GoogleCalendarSync:
    """Manages Google Calendar fetching and task updates."""
    
    @staticmethod
    def sync_next_7_days(days: int = 30) -> List[Dict[str, Any]]:
        """
        Fetches events from the user's primary Google Calendar for the specified number of days (default 30).
        Commits events into SQLite database as locked 'hard' constraints.
        """
        token = GoogleOAuthManager.get_valid_access_token()
        if not token:
            logger.warning("Calendar Sync triggered without valid OAuth token. Mock sync output active.")
            return []
            
        time_min = urllib.parse.quote(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        time_max = urllib.parse.quote(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + days * 86400)))
        
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin={time_min}&timeMax={time_max}&singleEvents=true&orderBy=startTime"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                events = data.get("items", [])
                
            conn = get_db_connection()
            cursor = conn.cursor()
            
            synced_events = []
            for event in events:
                event_id = event["id"]
                title = event.get("summary", "Untitled Google Event")
                desc = event.get("description", "")
                
                start_data = event.get("start", {})
                end_data = event.get("end", {})
                start_time = start_data.get("dateTime", start_data.get("date"))
                end_time = end_data.get("dateTime", end_data.get("date"))
                
                if not start_time or not end_time:
                    continue
                    
                # Store local copy as 'hard' constraint (Google Event source is protected)
                cursor.execute("""
                INSERT OR REPLACE INTO tasks (id, title, description, start_time, end_time, energy_level, constraint_type, status, source_event_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'none', 'hard', 'pending', ?, ?, ?)
                """, (f"gcal_{event_id}", title, desc, start_time, end_time, event_id, time.time(), time.time()))
                
                synced_events.append({
                    "id": f"gcal_{event_id}",
                    "title": title,
                    "start_time": start_time,
                    "end_time": end_time
                })
                
            conn.commit()
            conn.close()
            logger.info(f"Synced {len(synced_events)} Google Calendar events locally.")
            return synced_events
        except Exception as e:
            logger.error(f"Google Calendar Sync failed: {e}")
            return []

    @staticmethod
    def patch_calendar_event(event_id: str, new_start: str, new_end: str, summary: Optional[str] = None) -> bool:
        """Pushes schedule shifts back to Google Calendar via PATCH mutation API."""
        token = GoogleOAuthManager.get_valid_access_token()
        if not token:
            logger.warning(f"Mock patch executed for Google Calendar event {event_id} (No active OAuth token).")
            return True
            
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
        payload = {
            "start": {"dateTime": new_start},
            "end": {"dateTime": new_end}
        }
        if summary:
            payload["summary"] = summary
            
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="PATCH"
        )
        
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status in [200, 204]:
                    logger.info(f"Calendar Event {event_id} mutated successfully on Google Calendar API.")
                    return True
        except Exception as e:
            logger.error(f"Failed to push PATCH calendar updates: {e}")
            
        return False

class GmailParser:
    """Scans unread messages for timeline updates, parsing raw HTML cleanly for LLM input."""
    
    @staticmethod
    def strip_html_payload(html_body: str) -> str:
        """
        Removes HTML tags, inline styles, header metadata, scripts, and normalizes space.
        Uses BeautifulSoup4 for precise scraping or regex as a robust fallback.
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_body, "html.parser")
            
            # Remove scripts, stylesheet tags, and head blocks
            for tag in soup(["script", "style", "head", "title", "meta"]):
                tag.decompose()
                
            # Extracted plain text
            text = soup.get_text(separator="\n")
        except ImportError:
            # Fallback regex stripper
            text = re.sub(r"<style.*?>.*?</style>", "", html_body, flags=re.DOTALL)
            text = re.sub(r"<script.*?>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            
        # Clean whitespace and excess carriage returns
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()

    @staticmethod
    def get_unread_updates() -> List[Dict[str, Any]]:
        """
        Queries Gmail unread messages containing critical academic/deadline markers.
        Normalizes outputs for semantic indexing and vector storage.
        """
        token = GoogleOAuthManager.get_valid_access_token()
        if not token:
            logger.warning("Gmail inbox scanning bypassed. Mock unread items returned (No active OAuth token).")
            return []
            
        query = "subject:(syllabus OR exam OR assignment OR quiz OR due date OR class drop)"
        q_encoded = urllib.parse.quote(query)
        url = f"https://www.googleapis.com/gmail/v1/users/me/messages?q={q_encoded}&q=is:unread"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                messages = data.get("messages", [])
                
            parsed_messages = []
            for msg in messages[:5]:  # Fetch maximum of top 5 unread threads
                msg_id = msg["id"]
                detail_url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg_id}"
                detail_req = urllib.request.Request(detail_url, headers={"Authorization": f"Bearer {token}"})
                
                with urllib.request.urlopen(detail_req) as detail_resp:
                    msg_detail = json.loads(detail_resp.read().decode("utf-8"))
                    
                headers = msg_detail.get("payload", {}).get("headers", [])
                subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "No Subject")
                sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Unknown Sender")
                
                # Extract clean string body
                parts = msg_detail.get("payload", {}).get("parts", [])
                body = ""
                
                if not parts:
                    body_data = msg_detail.get("payload", {}).get("body", {}).get("data", "")
                    if body_data:
                        import base64
                        body = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
                else:
                    for part in parts:
                        if part.get("mimeType") in ["text/plain", "text/html"]:
                            part_body = part.get("body", {}).get("data", "")
                            if part_body:
                                import base64
                                decoded = base64.urlsafe_b64decode(part_body).decode("utf-8", errors="ignore")
                                if part.get("mimeType") == "text/html":
                                    body = GmailParser.strip_html_payload(decoded)
                                else:
                                    body = decoded
                                break
                                
                parsed_messages.append({
                    "id": msg_id,
                    "sender": sender,
                    "subject": subject,
                    "body": body[:500]  # Optimize window size for LLM input
                })
                
            return parsed_messages
        except Exception as e:
            logger.error(f"Gmail Ingestion Parser failed: {e}")
            return []
