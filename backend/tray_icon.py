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
    """Ensures the Ollama GUI application is running (showing in the tray) by cleaning up headless zombies."""
    gui_running = False
    try:
        out = subprocess.check_output(
            'wmic process where "name=\'ollama.exe\'" get commandline',
            shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode('utf-8', errors='ignore')
        
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        for line in lines:
            if "CommandLine" in line:
                continue
            if "serve" not in line.lower():
                gui_running = True
                break
    except Exception:
        pass

    if gui_running:
        # GUI is already active in the system tray; do nothing
        return

    # Kill any headless processes to free up port 11434
    try:
        subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(0.5)
    except Exception:
        pass

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        # Search for GUI app first (with space), then fall back to standard names
        for filename in ["ollama app.exe", "Ollama.exe", "ollama.exe"]:
            ollama_path = os.path.join(local_appdata, "Programs", "Ollama", filename)
            if os.path.exists(ollama_path):
                subprocess.Popen([ollama_path])
                return

# Add directory root to path
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

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

def start_services():
    global fastapi_proc, vite_proc, tunnel_proc
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    
    # 0. Check and start Ollama in the background
    check_and_start_ollama()
    
    # 1. Start FastAPI server using pythonw.exe
    pythonw_exe = os.path.join(base_dir, "backend", ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = "pythonw.exe"
        
    fastapi_proc = subprocess.Popen(
        [pythonw_exe, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=os.path.join(base_dir, "backend"),
        creationflags=creation_flags
    )
    
    # 2. Locate Node executor and run JS scripts directly (bypassing cmd/batch files)
    node_bin = find_node()
    vite_js = os.path.join(base_dir, "frontend", "node_modules", "vite", "bin", "vite.js")
    lt_js = os.path.join(base_dir, "frontend", "node_modules", "localtunnel", "bin", "lt.js")
    
    # 3. Start Vite frontend
    vite_proc = subprocess.Popen(
        [node_bin, vite_js],
        cwd=os.path.join(base_dir, "frontend"),
        creationflags=creation_flags
    )
    
    # 4. Start Localtunnel gateway
    tunnel_proc = subprocess.Popen(
        [node_bin, lt_js, "--port", "5173", "--subdomain", "quantime-scheduler-green"],
        cwd=os.path.join(base_dir, "frontend"),
        creationflags=creation_flags
    )

def open_dashboard(icon, item):
    webbrowser.open("http://localhost:5173")

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
    sys.exit(0)

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
    
    # Open dashboard on start
    threading.Thread(target=lambda: (time.sleep(2), webbrowser.open("http://localhost:5173"))).start()
    
    icon.run()

if __name__ == "__main__":
    main()
