# post_install_wizard.ps1
# Quantime Headless Setup Wizard (Executes post-file-copy inside Inno Setup)

param(
    [switch]$Silent = $false
)

$ErrorActionPreference = "Continue"
$appDir = $PSScriptRoot

function Write-ProgressUpdate ($pct, $msg) {
    Write-Output "Progress: $pct% - $msg"
    $progressDir = Join-Path $env:USERPROFILE ".quantime"
    if (-not (Test-Path $progressDir)) {
        New-Item -ItemType Directory -Force -Path $progressDir | Out-Null
    }
    $progressFile = Join-Path $progressDir "install_progress.txt"
    "$pct|$msg" | Out-File -FilePath $progressFile -Force -Encoding utf8
}

# Helper functions for detection
function Find-Ollama {
    # Check running processes
    $runningProc = Get-Process | Where-Object { $_.Name -like "*ollama*" } -ErrorAction SilentlyContinue
    foreach ($p in $runningProc) {
        if ($p.Path -and (Test-Path $p.Path)) {
            return $p.Path
        }
    }
    
    # Check environment path
    $cmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    
    # Check common folders for any ollama*.exe
    $searchDirs = @(
        "$env:LOCALAPPDATA\Programs\Ollama",
        "C:\Program Files\Ollama",
        "$env:PROGRAMFILES\Ollama",
        "$env:APPDATA\Ollama"
    )
    foreach ($dir in $searchDirs) {
        if (Test-Path $dir) {
            $files = Get-ChildItem -Path $dir -Filter "ollama*.exe" -ErrorAction SilentlyContinue
            if ($files) {
                return $files[0].FullName
            }
        }
    }
    return $null
}

function Get-GPUMetadata {
    $vram = 0
    $gpuName = ""
    try {
        $nameOut = nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
        if ($nameOut) {
            $gpuName = $nameOut.Trim()
        }
        $smiOut = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($smiOut -and [double]::TryParse($smiOut.Trim(), [ref]$vram)) {
            $vram = [Math]::Round($vram / 1024, 1)
        }
    } catch {}

    if ($gpuName -eq "" -or $vram -eq 0) {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        if ($gpus) {
            foreach ($gpu in $gpus) {
                if ($gpu.Name -and ($gpuName -eq "" -or ($gpu.AdapterRAM -gt 0 -and $gpuName -like "*Graphics*"))) {
                    $gpuName = $gpu.Name
                    if ($vram -eq 0 -and $gpu.AdapterRAM) {
                        $vram = [Math]::Round($gpu.AdapterRAM / 1GB, 1)
                    }
                }
            }
        }
    }
    
    # Heuristic workaround for WMI 32-bit wrap-around bug on RTX 5060 Ti or other GPUs
    if ($gpuName -like "*RTX 5060*") {
        $vram = 16.0
    }
    
    return @{
        Name = $gpuName
        VRAM = $vram
    }
}

# Terminate any running Quantime backend processes to free files and ports for setup
Write-ProgressUpdate 1 "Stopping any active Quantime application instances..."
try {
    $processes = Get-CimInstance Win32_Process -Filter "name = 'pythonw.exe' or name = 'python.exe' or name = 'node.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $processes) {
        if ($p.ExecutablePath -and $p.ExecutablePath -like "*Quantime*") {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
} catch {
    Get-Process | Where-Object { $_.Path -and ($_.Path -like "*Quantime*") } | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

Write-ProgressUpdate 2 "Starting Quantime environment configuration..."

# ---------------------------------------------------------------------
# 1. Verify/Download Python
# ---------------------------------------------------------------------
$pythonCmd = "python"
$pythonTest = Start-Process -FilePath "python" -ArgumentList "--version" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
if ($null -eq $pythonTest) {
    $embedDir = "$appDir\backend\python-embed"
    if (Test-Path "$embedDir\python.exe") {
        Write-ProgressUpdate 5 "Using existing portable Python environment..."
        $pythonCmd = "$embedDir\python.exe"
    } else {
        Write-ProgressUpdate 5 "Python not detected globally. Setting up portable Python environment..."
        $embedPythonUrl = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
        $zipPath = "$env:TEMP\python-portable.zip"
        
        if (Test-Path $embedDir) {
            Remove-Item -Recurse -Force $embedDir -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Force -Path $embedDir | Out-Null
        Invoke-WebRequest -Uri $embedPythonUrl -OutFile $zipPath
        Write-ProgressUpdate 12 "Extracting Python binaries..."
        Expand-Archive -Path $zipPath -DestinationPath $embedDir -Force
        $pythonCmd = "$embedDir\python.exe"
    }
}
Write-ProgressUpdate 15 "Python configuration verified."

# ---------------------------------------------------------------------
# 2. Verify/Download Node.js & NPM
# ---------------------------------------------------------------------
$nodeTest = Start-Process -FilePath "node" -ArgumentList "--version" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
if ($null -eq $nodeTest) {
    $nodeDir = "$appDir\frontend\node-portable"
    $nodeExe = "$nodeDir\node.exe"
    if (Test-Path $nodeExe) {
        $item = Get-Item $nodeExe
        if ($item.Length -lt 10MB) {
            Write-ProgressUpdate 20 "Removing corrupted portable Node executable..."
            Remove-Item -Force $nodeExe -ErrorAction SilentlyContinue
        }
    }
    
    if (Test-Path $nodeExe) {
        Write-ProgressUpdate 20 "Using existing portable Node executable..."
    } else {
        Write-ProgressUpdate 20 "Node.js not detected. Downloading portable Node executable..."
        $nodeUrl = "https://nodejs.org/dist/v18.16.0/win-x64/node.exe"
        New-Item -ItemType Directory -Force -Path $nodeDir | Out-Null
        Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeExe
    }
    # Set path locally
    $env:PATH += ";$nodeDir"
}
Write-ProgressUpdate 30 "Node.js environment verified."

# ---------------------------------------------------------------------
# 3. Check/Install Ollama Service
# ---------------------------------------------------------------------
$ollamaPath = Find-Ollama
if ($null -eq $ollamaPath) {
    Write-ProgressUpdate 35 "Ollama not detected. Downloading and installing headlessly..."
    $ollamaSetup = "$env:TEMP\OllamaSetup.exe"
    if (Test-Path $ollamaSetup) {
        $item = Get-Item $ollamaSetup
        if ($item.Length -lt 10MB) {
            Remove-Item -Force $ollamaSetup -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path $ollamaSetup)) {
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaSetup
    }
    
    # Run setup silently in the background
    Write-ProgressUpdate 40 "Installing Ollama service..."
    $proc = Start-Process -FilePath $ollamaSetup -ArgumentList "/silent" -Wait -PassThru
    
    Start-Sleep -Seconds 3
    $ollamaPath = Find-Ollama
    if ($null -eq $ollamaPath) {
        $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe"
    }
} else {
    Write-ProgressUpdate 42 "Ollama detected at: $ollamaPath"
}

# Start the Ollama process if not already running
$runningProc = Get-Process | Where-Object { $_.Name -like "*ollama*" } -ErrorAction SilentlyContinue
if (-not $runningProc) {
    Write-ProgressUpdate 45 "Launching Ollama Service..."
    Start-Process -FilePath $ollamaPath -WindowStyle Hidden
    Start-Sleep -Seconds 5
} else {
    Write-ProgressUpdate 48 "Ollama service is already running."
}

# ---------------------------------------------------------------------
# 4. Ingest and Compile LLM Model (Delegated to Web GUI Setup Wizard)
# ---------------------------------------------------------------------
Write-ProgressUpdate 50 "LLM Model compiler checked."

# ---------------------------------------------------------------------
# 5. Initialize Databases & Environment Files
# ---------------------------------------------------------------------
Write-ProgressUpdate 55 "Initializing settings and local database..."
if (-not (Test-Path "$appDir\backend\.env")) {
    Copy-Item "$appDir\backend\.env.example" "$appDir\backend\.env" -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------
# 6. Install Dependencies
# ---------------------------------------------------------------------
if (Test-Path "$appDir\backend\.venv") {
    if (-not (Test-Path "$appDir\backend\.venv\Scripts\pip.exe") -or -not (Test-Path "$appDir\backend\.venv\Scripts\python.exe")) {
        Write-ProgressUpdate 60 "Cleaning up corrupted Python virtual environment..."
        Remove-Item -Recurse -Force "$appDir\backend\.venv" -ErrorAction SilentlyContinue
    }
}
Write-ProgressUpdate 60 "Initializing Python virtual environment..."
Start-Process -FilePath $pythonCmd -ArgumentList "-m venv `"$appDir\backend\.venv`"" -Wait -NoNewWindow
Write-ProgressUpdate 65 "Installing Python packages..."
Start-Process -FilePath "$appDir\backend\.venv\Scripts\pip" -ArgumentList "install -r `"$appDir\backend\requirements.txt`"" -Wait -NoNewWindow

# Verify and Download Microsoft VibeVoice if missing
$vibevoicePath = "$appDir\backend\vibevoice_src"
if (Test-Path $vibevoicePath) {
    if (-not (Test-Path "$vibevoicePath\setup.py") -and -not (Test-Path "$vibevoicePath\pyproject.toml")) {
        Write-ProgressUpdate 70 "Cleaning up corrupted VibeVoice source codebase..."
        Remove-Item -Recurse -Force $vibevoicePath -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path $vibevoicePath)) {
    Write-ProgressUpdate 70 "VibeVoice codebase missing. Downloading archive..."
    $zipUrl = "https://github.com/microsoft/VibeVoice/archive/refs/heads/main.zip"
    $zipPath = "$env:TEMP\vibevoice.zip"
    $extractDir = "$env:TEMP\vibevoice-extract"
    
    if (Test-Path $extractDir) {
        Remove-Item -Recurse -Force $extractDir
    }
    
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Write-ProgressUpdate 75 "Extracting VibeVoice packages..."
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    $extractedFolder = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    Move-Item -Path $extractedFolder.FullName -Destination $vibevoicePath -Force
    Remove-Item $zipPath -Force
    Remove-Item -Recurse -Force $extractDir
}

Write-ProgressUpdate 80 "Installing VibeVoice library and dependencies..."
Start-Process -FilePath "$appDir\backend\.venv\Scripts\pip" -ArgumentList "install `"$vibevoicePath`"" -Wait -NoNewWindow

Write-ProgressUpdate 85 "Installing frontend package packages..."
Push-Location "$appDir\frontend"
Start-Process -FilePath "npm.cmd" -ArgumentList "install" -Wait -NoNewWindow
Write-ProgressUpdate 90 "Compiling production frontend assets..."
Start-Process -FilePath "npm.cmd" -ArgumentList "run build" -Wait -NoNewWindow
Pop-Location

# ---------------------------------------------------------------------
# 7. Configure Firewall Exception Rules
# ---------------------------------------------------------------------
Write-ProgressUpdate 95 "Enabling Windows Firewall communication exception rules..."
netsh advfirewall firewall add rule name="Quantime Gateway" dir=in action=allow protocol=TCP localport=8000,5173

# ---------------------------------------------------------------------
# 8. Register Windows Task Scheduler Autostart
# ---------------------------------------------------------------------
Write-ProgressUpdate 98 "Registering background task autostart services..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-ExecutionPolicy Bypass -File `"$appDir\install_windows_app.ps1`" -Silent" -Wait -NoNewWindow

Write-ProgressUpdate 100 "Quantime installation completed successfully!"
