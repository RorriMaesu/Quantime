# backend/tray_icon.py
import os
import sys
import time
import subprocess
import webbrowser
import threading
import socket
import pystray
from PIL import Image, ImageDraw

_lock_socket = None

def lock_single_instance():
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 49999))
        _lock_socket.listen(1)
    except socket.error:
        # Silently exit if another instance is already running
        sys.exit(0)

def check_and_start_ollama():
    """Ensures the Ollama GUI application is running by launching it on startup.
    If it is already running, the Ollama GUI app will exit silently due to its single-instance lock."""
    import os
    import subprocess
    import shutil

    possible_paths = []
    
    # 1. Check LOCALAPPDATA
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        for filename in ["ollama app.exe", "Ollama.exe", "ollama.exe"]:
            possible_paths.append(os.path.join(local_appdata, "Programs", "Ollama", filename))
            
    # 2. Check System PATH (shutil.which)
    path_exe = shutil.which("ollama") or shutil.which("ollama.exe")
    if path_exe:
        possible_paths.append(path_exe)
        # Also look in the same directory as the PATH executable for the GUI app
        path_dir = os.path.dirname(path_exe)
        for filename in ["ollama app.exe", "Ollama.exe"]:
            possible_paths.append(os.path.join(path_dir, filename))
            
    # 3. Check Windows Registry uninstall keys
    if os.name == 'nt':
        try:
            import winreg
            for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
                for view in [0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY]:
                    try:
                        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                        with winreg.OpenKeyEx(hive, key_path, 0, winreg.KEY_READ | view) as key:
                            info = winreg.QueryInfoKey(key)
                            for idx in range(info[0]):
                                sub_name = winreg.EnumKey(key, idx)
                                try:
                                    with winreg.OpenKey(key, sub_name) as subkey:
                                        disp_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                        if "Ollama" in disp_name:
                                            loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                            if loc:
                                                for filename in ["ollama app.exe", "Ollama.exe", "ollama.exe"]:
                                                    possible_paths.append(os.path.join(loc, filename))
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

    # Launch the first existing executable path
    for ollama_path in possible_paths:
        if os.path.exists(ollama_path):
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 6  # SW_MINIMIZE
            try:
                subprocess.Popen([ollama_path], startupinfo=startupinfo)
                return
            except Exception:
                pass

# Add directory root to path
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

log_dir = os.path.join(os.path.expanduser("~"), ".quantime")
os.makedirs(log_dir, exist_ok=True)

fastapi_proc = None
vite_proc = None
tunnel_proc = None
running = True

def create_image():
    # Generate a beautiful 64x64 indigo gradient circle with a white sparkle star
    width = 64
    height = 64
    image = Image.new('RGBA', (width, height), color=(0, 0, 0, 0)) # transparent bg
    dc = ImageDraw.Draw(image)
    # Draw background circle
    dc.ellipse([4, 4, 60, 60], fill=(79, 70, 229)) # Indigo-600
    # Draw small inner accent circle
    dc.ellipse([8, 8, 56, 56], fill=(99, 102, 241)) # Indigo-500
    # Draw center sparkle/cross star
    dc.polygon([
        (32, 16), (35, 29), (48, 32), (35, 35),
        (32, 48), (29, 35), (16, 32), (29, 29)
    ], fill=(255, 255, 255))
    return image

def kill_process_tree(proc):
    if not proc:
        return
    try:
        if os.name == 'nt':
            # Force kill the process and all of its child processes
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            proc.terminate()
    except Exception:
        pass

def find_node():
    import shutil
    portable = os.path.join(base_dir, "frontend", "node-portable", "node.exe")
    if os.path.exists(portable):
        return portable
    global_node = shutil.which("node")
    if global_node:
        return global_node
    return "node"

def get_tunnel_subdomain():
    import sqlite3
    db_path = os.path.join(os.path.expanduser("~"), ".quantime", "quantime.db")
    if not os.path.exists(db_path):
        return "quantime-scheduler-green"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS user_profiles (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("SELECT value FROM user_profiles WHERE key = 'tunnel_subdomain'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return "quantime-scheduler-green"

def start_services():
    global fastapi_proc, vite_proc, tunnel_proc
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    
    # 0. Check and start Ollama in the background
    check_and_start_ollama()
    
    # 1. Start FastAPI server using pythonw.exe
    pythonw_exe = os.path.join(base_dir, "backend", ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = "pythonw.exe"
        
    fastapi_log_path = os.path.join(log_dir, "fastapi.log")
    try:
        log_file = open(fastapi_log_path, "a")
    except Exception:
        log_file = subprocess.DEVNULL
        
    fastapi_proc = subprocess.Popen(
        [pythonw_exe, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30"],
        cwd=os.path.join(base_dir, "backend"),
        creationflags=creation_flags,
        stdout=log_file,
        stderr=log_file
    )
    
    # Check if we are in production mode (frontend/dist exists)
    frontend_dist_path = os.path.join(base_dir, "frontend", "dist")
    is_prod = os.path.exists(frontend_dist_path)
    
    node_bin = find_node()
    lt_js = os.path.join(base_dir, "frontend", "node_modules", "localtunnel", "bin", "lt.js")
    
    if not is_prod:
        # 2. Locate Vite JS
        vite_js = os.path.join(base_dir, "frontend", "node_modules", "vite", "bin", "vite.js")
        
        # 3. Start Vite frontend
        vite_log_path = os.path.join(log_dir, "vite.log")
        try:
            vite_log = open(vite_log_path, "a")
        except Exception:
            vite_log = subprocess.DEVNULL
            
        vite_proc = subprocess.Popen(
            [node_bin, vite_js],
            cwd=os.path.join(base_dir, "frontend"),
            creationflags=creation_flags,
            stdout=vite_log,
            stderr=vite_log
        )
        
        tunnel_port = "5173"
    else:
        vite_proc = None
        tunnel_port = "8000"
        
    # 4. Start Localtunnel gateway
    if os.path.exists(lt_js):
        tunnel_log_path = os.path.join(log_dir, "localtunnel.log")
        try:
            tunnel_log = open(tunnel_log_path, "w")
        except Exception:
            tunnel_log = subprocess.DEVNULL
            
        subdomain = get_tunnel_subdomain()
        tunnel_proc = subprocess.Popen(
            [node_bin, lt_js, "--port", tunnel_port, "--subdomain", subdomain, "--local-host", "127.0.0.1"],
            cwd=os.path.join(base_dir, "frontend"),
            creationflags=creation_flags,
            stdout=tunnel_log,
            stderr=tunnel_log
        )

def open_dashboard(icon, item):
    frontend_dist_path = os.path.join(base_dir, "frontend", "dist")
    port = "8000" if os.path.exists(frontend_dist_path) else "5173"
    webbrowser.open(f"http://localhost:{port}")

def restart_services(icon, item):
    global fastapi_proc, vite_proc, tunnel_proc
    # Kill and restart
    kill_process_tree(fastapi_proc)
    kill_process_tree(vite_proc)
    kill_process_tree(tunnel_proc)
    time.sleep(1)
    start_services()
    icon.notify("Quantime background services successfully restarted.", title="Quantime Services")

def on_exit(icon, item):
    global running
    running = False
    icon.stop()
    kill_process_tree(fastapi_proc)
    kill_process_tree(vite_proc)
    kill_process_tree(tunnel_proc)

def open_dashboard_when_ready():
    # Wait for backend (8000) to start accepting connections
    for _ in range(30):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(('127.0.0.1', 8000))
            s.close()
            break
        except Exception:
            time.sleep(0.5)
            
    frontend_dist_path = os.path.join(base_dir, "frontend", "dist")
    is_prod = os.path.exists(frontend_dist_path)
    
    if not is_prod:
        # Wait for frontend (5173) to start accepting connections
        for _ in range(30):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(('127.0.0.1', 5173))
                s.close()
                break
            except Exception:
                time.sleep(0.5)
            
    time.sleep(0.5)
    port = "8000" if is_prod else "5173"
    webbrowser.open(f"http://localhost:{port}")

def monitor_localtunnel():
    """Periodically health-checks the public LocalTunnel endpoint and restarts it if it goes offline or returns 502/503."""
    global tunnel_proc
    time.sleep(15)  # Wait for initial startup
    
    while running:
        tunnel_healthy = False
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://quantime-scheduler-green.loca.lt",
                headers={"Bypass-Tunnel-Reminder": "true"}  # Bypass the localtunnel landing page
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    tunnel_healthy = True
        except Exception as e:
            # If the HTTP status is not a 5xx error (e.g. 404, 401, 403), the tunnel is still active
            if hasattr(e, 'code') and e.code < 500:
                tunnel_healthy = True
                
        if not tunnel_healthy and running:
            # Force restart the tunnel process
            kill_process_tree(tunnel_proc)
            time.sleep(1)
            
            frontend_dist_path = os.path.join(base_dir, "frontend", "dist")
            port = "8000" if os.path.exists(frontend_dist_path) else "5173"
            
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            node_bin = find_node()
            lt_js = os.path.join(base_dir, "frontend", "node_modules", "localtunnel", "bin", "lt.js")
            if os.path.exists(lt_js):
                tunnel_log_path = os.path.join(log_dir, "localtunnel.log")
                try:
                    tunnel_log = open(tunnel_log_path, "w")
                except Exception:
                    tunnel_log = subprocess.DEVNULL
                    
                subdomain = get_tunnel_subdomain()
                tunnel_proc = subprocess.Popen(
                    [node_bin, lt_js, "--port", port, "--subdomain", subdomain, "--local-host", "127.0.0.1"],
                    cwd=os.path.join(base_dir, "frontend"),
                    creationflags=creation_flags,
                    stdout=tunnel_log,
                    stderr=tunnel_log
                )
            
        time.sleep(15)

def main():
    lock_single_instance()
    start_services()
    
    # Configure the system tray menu
    menu = pystray.Menu(
        pystray.MenuItem("Open Quantime Dashboard", open_dashboard, default=True),
        pystray.MenuItem("Restart Services", restart_services),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit)
    )
    
    # Create the tray icon
    icon = pystray.Icon("Quantime", create_image(), "Quantime Engine", menu)
    
    # Open dashboard only when services are ready
    threading.Thread(target=open_dashboard_when_ready, daemon=True).start()
    
    # Start self-healing LocalTunnel monitor
    threading.Thread(target=monitor_localtunnel, daemon=True).start()
    
    icon.run()

if __name__ == "__main__":
    main()
