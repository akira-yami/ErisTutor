import os
import shutil
import subprocess
import json
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import psutil
import requests
import urllib.request

app = FastAPI(title="Eris Service Manager")

# Setup paths
BASE_DIR = Path(__file__).parent.parent.absolute()
STATIC_DIR = BASE_DIR / "gemini_manager" / "static"
STATE_DIR = BASE_DIR / ".eris_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

PORTS = {
    "whale2api": 9090,
    "cline_bridge": 7832,
    "tts_pool": 9091,
}

def port_listening(port: int, timeout: float = 0.5) -> bool:
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False

@app.get("/api/setup/requirements")
def check_requirements():
    results = {}
    
    # Python
    import sys
    ok_py = sys.version_info >= (3, 12)
    results["python"] = {"ok": ok_py, "version": sys.version.split()[0]}
    
    # Node.js
    try:
        nv = subprocess.check_output(["node", "--version"], stderr=subprocess.STDOUT).decode().strip()
        maj = int(nv.lstrip("v").split(".")[0])
        results["node"] = {"ok": maj >= 22, "version": nv}
    except Exception:
        results["node"] = {"ok": False, "version": "Not Found"}
        
    # npm
    try:
        nv2 = subprocess.check_output(["npm", "--version"], stderr=subprocess.STDOUT, shell=True).decode().strip()
        results["npm"] = {"ok": True, "version": nv2}
    except Exception:
        results["npm"] = {"ok": False, "version": "Not Found"}
        
    # venv
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    results["venv"] = {"ok": venv_py.exists(), "version": "Created" if venv_py.exists() else "Missing"}
    
    return results

@app.post("/api/setup/dependencies")
def install_dependencies():
    # Install dependencies in background
    try:
        # We will use a bat file or run it via Popen
        venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
        if not venv_py.exists():
            raise HTTPException(status_code=400, detail="VENV not found. Please create it first.")
            
        subprocess.Popen(
            f'cmd.exe /c start "Installing Dependencies" cmd /k "{venv_py} -m pip install -r requirements.txt && npm ci --prefix cline-bridge"',
            cwd=str(BASE_DIR),
            shell=True
        )
        return {"message": "Dependency installation started in a new window."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/services/health")
def get_health():
    services = [
        {"id": "whale2api", "name": "DeepSeek Brain", "port": PORTS["whale2api"]},
        {"id": "tts_pool", "name": "ElevenLabs TTS", "port": PORTS["tts_pool"]},
        {"id": "cline_bridge", "name": "Cline Bridge", "port": PORTS["cline_bridge"]},
    ]
    
    for s in services:
        listening = port_listening(s["port"])
        if listening:
            s["status"] = "OK"
        else:
            s["status"] = "DOWN"
            
    return services

@app.post("/api/system/build")
def system_build():
    build_script = BASE_DIR / "_fullbuild.py"
    if not build_script.exists():
        raise HTTPException(status_code=404, detail="_fullbuild.py not found")
        
    try:
        venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
        subprocess.Popen(
            f'cmd.exe /c start "Eris Full Build" cmd /k "{venv_py} _fullbuild.py"',
            cwd=str(BASE_DIR),
            shell=True
        )
        return {"message": "Build started in a new window."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/services/start")
def start_all():
    setup_script = BASE_DIR / "eris_setup.py"
    try:
        # Note: In a real robust system, we should have a generic start script.
        # Since eris_setup.py handles starting logic in its menu, we could write a wrapper.
        # For now, let's just trigger a wrapper script or tell user to run it via CLI.
        raise HTTPException(status_code=501, detail="Start all via Web UI is coming soon. Please run eris_setup.py for now.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mount static files
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
