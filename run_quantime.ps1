# run_quantime.ps1
# Quantime Full-Stack Startup Orchestrator (Windows PowerShell)

$ErrorActionPreference = "Stop"

# Helper for coloring logs
function Write-Header ($text) {
    Write-Host "`n=== $text ===" -ForegroundColor Cyan
}

function Write-Ok ($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Write-Warn ($text) {
    Write-Host "[WARNING] $text" -ForegroundColor Yellow
}

function Write-Info ($text) {
    Write-Host "[INFO] $text" -ForegroundColor Gray
}

Write-Host "==================================================" -ForegroundColor Magenta
Write-Host "         QUANTIME ORCHESTRATOR LAUNCHER" -ForegroundColor Magenta
Write-Host "==================================================" -ForegroundColor Magenta

# ---------------------------------------------------------------------
# 1. Pre-Flight Credential Check
# ---------------------------------------------------------------------
Write-Header "Pre-Flight Credential Check"

$credentialsPath = "backend/credentials.json"
$firebaseKeyPath = "backend/firebase_key.json"
$mockMode = $false

if (-not (Test-Path $credentialsPath)) {
    Write-Warn "Google Workspace 'backend/credentials.json' is missing."
    $mockMode = $true
} else {
    Write-Ok "Google credentials found."
}

if (-not (Test-Path $firebaseKeyPath)) {
    Write-Warn "Firebase service account 'backend/firebase_key.json' is missing."
    $mockMode = $true
} else {
    Write-Ok "Firebase application credentials key found."
}

if ($mockMode) {
    Write-Warn "Quantime will automatically initialize in Firebase Mock/Offline Mode."
} else {
    Write-Ok "Quantime credentials configured for Live Mode."
}

# ---------------------------------------------------------------------
# 2. Service Dependency Check (Ollama)
# ---------------------------------------------------------------------
Write-Header "Service Dependency Check (Ollama)"

$ollamaUrl = "http://localhost:11434/api/tags"
$ollamaOnline = $false

try {
    $resp = Invoke-RestMethod -Uri $ollamaUrl -Method Get -TimeoutSec 3
    $ollamaOnline = $true
    Write-Ok "Ollama service is online and listening on port 11434."
} catch {
    Write-Warn "Ollama is currently offline. Attempting to start Ollama service..."
    
    # Attempt to locate and launch the standard Ollama executable on Windows
    $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe"
    if (Test-Path $ollamaPath) {
        Start-Process -FilePath $ollamaPath -WindowStyle Hidden
        Write-Info "Ollama executable spawned in background. Sleeping 5 seconds for initialization..."
        Start-Sleep -Seconds 5
        
        try {
            $resp = Invoke-RestMethod -Uri $ollamaUrl -Method Get -TimeoutSec 3
            $ollamaOnline = $true
            Write-Ok "Ollama started successfully and verified online."
        } catch {
            Write-Warn "Ollama process launched but port 11434 is still unreachable. Proceeding with caution..."
        }
    } else {
        Write-Warn "Ollama executable not found in $ollamaPath. Please make sure Ollama is installed."
    }
}

# ---------------------------------------------------------------------
# 3. Automated Installation Checks
# ---------------------------------------------------------------------
Write-Header "Automated Installation Checks"

# Python Virtual Environment
if (-not (Test-Path "backend/.venv")) {
    Write-Warn "Python virtual environment (.venv) not found. Initializing..."
    Start-Process -FilePath "python" -ArgumentList "-m venv backend/.venv" -Wait
    Write-Ok "Virtual environment created."
}

Write-Info "Activating Python virtual environment and installing backend packages..."
& "backend/.venv/Scripts/pip" install -r backend/requirements.txt
Write-Ok "Backend dependencies up to date."


# Frontend node modules (Target npm.cmd explicitly to prevent Win32 launch exception)
if (-not (Test-Path "frontend/node_modules")) {
    Write-Warn "'frontend/node_modules' is missing. Executing npm install..."
    Push-Location frontend
    Start-Process -FilePath "npm.cmd" -ArgumentList "install" -Wait -NoNewWindow
    Pop-Location
    Write-Ok "Frontend dependencies loaded."
} else {
    Write-Ok "Frontend package modules found."
}

# ---------------------------------------------------------------------
# 4. Model Compilation Verification
# ---------------------------------------------------------------------
Write-Header "Model Compilation Verification"

if ($ollamaOnline) {
    try {
        $tags = Invoke-RestMethod -Uri $ollamaUrl -Method Get
        $modelFound = $false
        foreach ($model in $tags.models) {
            if ($model.name -like "*gemma4-agent-mtp*") {
                $modelFound = $true
                break
            }
        }
        
        if (-not $modelFound) {
            Write-Warn "'gemma4-agent-mtp' model weights not compiled in Ollama registry."
            Write-Info "Executing build_model.ps1 compilation pipeline..."
            & "./build_model.ps1"
        } else {
            Write-Ok "Model 'gemma4-agent-mtp' found in Ollama registry."
        }
    } catch {
        Write-Warn "Ollama registry query failed. Skipping compile checks."
    }
} else {
    Write-Warn "Ollama offline. Skipping model checks."
}

# ---------------------------------------------------------------------
# 5. Database Verification & Initialization
# ---------------------------------------------------------------------
Write-Header "Database Verification"

Write-Info "Running database schema updates..."
& "backend/.venv/Scripts/python" backend/database.py
Write-Ok "SQLite WAL tables validated."

# ---------------------------------------------------------------------
# 6. Asynchronous Concurrent Launch
# ---------------------------------------------------------------------
Write-Header "Concurrent Server Launch"

# Spin up FastAPI Backend Server
Write-Info "Launching FastAPI Gateway on http://localhost:8000..."
Start-Process -FilePath "backend/.venv/Scripts/uvicorn" -ArgumentList "app:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 30" -WorkingDirectory "backend" -WindowStyle Minimized

# Spin up Vite Frontend Dev Server (Target npm.cmd explicitly to prevent Win32 launch exception)
Write-Info "Launching Vite PWA Dashboard on http://localhost:5173..."
Start-Process -FilePath "npm.cmd" -ArgumentList "run dev" -WorkingDirectory "frontend" -WindowStyle Minimized

# Spin up Localtunnel Public Gateway (Target npm.cmd explicitly to prevent Win32 launch exception)
Write-Info "Launching Localtunnel Public Gateway on https://quantime-scheduler-green.loca.lt..."
Start-Process -FilePath "npm.cmd" -ArgumentList "run tunnel" -WorkingDirectory "frontend" -WindowStyle Minimized

Start-Sleep -Seconds 2

# Output dashboard connection matrix
Write-Host "`n"
Write-Host "+--------------------------------------------------+" -ForegroundColor Green
Write-Host "|           QUANTIME ECOSYSTEM ACTIVE RUNTIME      |" -ForegroundColor Green
Write-Host "+--------------------------------------------------+" -ForegroundColor Green
Write-Host "|  PWA Dashboard:     http://localhost:5173        |" -ForegroundColor Green
Write-Host "|  Public Gateway:    https://quantime-scheduler-green.loca.lt |" -ForegroundColor Green
Write-Host "|  FastAPI Gateway:   http://localhost:8000        |" -ForegroundColor Green
Write-Host "|  REST Diagnostics:  http://localhost:8000/health |" -ForegroundColor Green
Write-Host "+--------------------------------------------------+" -ForegroundColor Green
Write-Host "`nEcosystem successfully launched in minimized console windows." -ForegroundColor Yellow
Write-Host "Check minimised shells or diagnostic logs if routing conflicts emerge.`n"
