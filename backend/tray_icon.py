# backend/tray_icon.py
import os
import sys
import time
import subprocess
import webbrowser
import threading
import pystray
from PIL import Image, ImageDraw

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

def start_services():
    global fastapi_proc, vite_proc, tunnel_proc
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    npm_cmd = "npm.cmd" if os.name == 'nt' else "npm"
    
    # 1. Start FastAPI server using virtual environment python interpreter
    python_exe = os.path.join(base_dir, "backend", ".venv", "Scripts", "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable # fallback
        
    fastapi_proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=os.path.join(base_dir, "backend"),
        creationflags=creation_flags
    )
    
    # 2. Start Vite frontend
    vite_proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=os.path.join(base_dir, "frontend"),
        creationflags=creation_flags
    )
    
    # 3. Start Localtunnel gateway
    tunnel_proc = subprocess.Popen(
        [npm_cmd, "run", "tunnel"],
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
