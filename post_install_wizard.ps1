# post_install_wizard.ps1
# Quantime Headless Setup Wizard (Executes post-file-copy inside Inno Setup)

param(
    [switch]$Silent = $false
)

$ErrorActionPreference = "Continue"
$appDir = $PSScriptRoot

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

Write-Output "Starting Quantime Post-Installation Wizard..."

# ---------------------------------------------------------------------
# 1. Verify/Download Python
# ---------------------------------------------------------------------
$pythonCmd = "python"
$pythonTest = Start-Process -FilePath "python" -ArgumentList "--version" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
if ($null -eq $pythonTest) {
    Write-Output "Python not detected globally. Setting up portable Python environment..."
    $embedPythonUrl = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
    $zipPath = "$env:TEMP\python-portable.zip"
    $embedDir = "$appDir\backend\python-embed"
    
    New-Item -ItemType Directory -Force -Path $embedDir
    Invoke-WebRequest -Uri $embedPythonUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $embedDir -Force
    
    # Configure path to use local portable python
    $pythonCmd = "$embedDir\python.exe"
}

# ---------------------------------------------------------------------
# 2. Verify/Download Node.js & NPM
# ---------------------------------------------------------------------
$nodeTest = Start-Process -FilePath "node" -ArgumentList "--version" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
if ($null -eq $nodeTest) {
    Write-Output "Node.js not detected. Downloading portable Node executable..."
    $nodeUrl = "https://nodejs.org/dist/v18.16.0/win-x64/node.exe"
    $nodeDir = "$appDir\frontend\node-portable"
    New-Item -ItemType Directory -Force -Path $nodeDir
    Invoke-WebRequest -Uri $nodeUrl -OutFile "$nodeDir\node.exe"
    # Set path locally
    $env:PATH += ";$nodeDir"
}

# ---------------------------------------------------------------------
# 3. Check/Install Ollama Service
# ---------------------------------------------------------------------
$ollamaPath = Find-Ollama
if ($null -eq $ollamaPath) {
    Write-Output "Ollama not detected. Downloading and installing headlessly..."
    $ollamaSetup = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaSetup
    
    # Run setup silently in the background
    Write-Output "Installing Ollama..."
    $proc = Start-Process -FilePath $ollamaSetup -ArgumentList "/silent" -Wait -PassThru
    
    Start-Sleep -Seconds 3
    $ollamaPath = Find-Ollama
    if ($null -eq $ollamaPath) {
        $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe"
    }
} else {
    Write-Output "Ollama detected at: $ollamaPath"
}

# Start the Ollama process if not already running
$runningProc = Get-Process | Where-Object { $_.Name -like "*ollama*" } -ErrorAction SilentlyContinue
if (-not $runningProc) {
    Write-Output "Launching Ollama Service..."
    Start-Process -FilePath $ollamaPath -WindowStyle Hidden
    Start-Sleep -Seconds 5
} else {
    Write-Output "Ollama service is already running."
}

# ---------------------------------------------------------------------
# 4. Ingest and Compile LLM Model (Delegated to Web GUI Setup Wizard)
# ---------------------------------------------------------------------
Write-Output "Model setup deferred to the first-run GUI wizard in the web app."

# ---------------------------------------------------------------------
# 5. Initialize Databases & Environment Files
# ---------------------------------------------------------------------
Write-Output "Initializing settings and local database..."
if (-not (Test-Path "$appDir\backend\.env")) {
    Copy-Item "$appDir\backend\.env.example" "$appDir\backend\.env" -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------
# 6. Install Dependencies
# ---------------------------------------------------------------------
Write-Output "Installing Python virtual environment packages..."
Start-Process -FilePath $pythonCmd -ArgumentList "-m venv `"$appDir\backend\.venv`"" -Wait -NoNewWindow
Start-Process -FilePath "$appDir\backend\.venv\Scripts\pip" -ArgumentList "install -r `"$appDir\backend\requirements.txt`"" -Wait -NoNewWindow

# Verify and Download Microsoft VibeVoice if missing
$vibevoicePath = "$appDir\backend\vibevoice_src"
if (-not (Test-Path $vibevoicePath)) {
    Write-Output "VibeVoice source codebase is missing. Downloading from GitHub..."
    $zipUrl = "https://github.com/microsoft/VibeVoice/archive/refs/heads/main.zip"
    $zipPath = "$env:TEMP\vibevoice.zip"
    $extractDir = "$env:TEMP\vibevoice-extract"
    
    if (Test-Path $extractDir) {
        Remove-Item -Recurse -Force $extractDir
    }
    
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    $extractedFolder = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    Move-Item -Path $extractedFolder.FullName -Destination $vibevoicePath -Force
    Remove-Item $zipPath -Force
    Remove-Item -Recurse -Force $extractDir
    Write-Output "VibeVoice downloaded successfully."
}

Write-Output "Installing VibeVoice library and dependencies..."
Start-Process -FilePath "$appDir\backend\.venv\Scripts\pip" -ArgumentList "install `"$vibevoicePath`"" -Wait -NoNewWindow

Write-Output "Installing frontend package packages and compiling production bundle..."
Push-Location "$appDir\frontend"
Start-Process -FilePath "npm.cmd" -ArgumentList "install" -Wait -NoNewWindow
Start-Process -FilePath "npm.cmd" -ArgumentList "run build" -Wait -NoNewWindow
Pop-Location

# ---------------------------------------------------------------------
# 7. Configure Firewall Exception Rules
# ---------------------------------------------------------------------
Write-Output "Enabling firewall communication rules for cross-device mobile sync..."
netsh advfirewall firewall add rule name="Quantime Gateway" dir=in action=allow protocol=TCP localport=8000,5173

# ---------------------------------------------------------------------
# 8. Register Windows Task Scheduler Autostart
# ---------------------------------------------------------------------
Write-Output "Registering Task Scheduler startup background tasks..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-ExecutionPolicy Bypass -File `"$appDir\install_windows_app.ps1`" -Silent" -Wait -NoNewWindow

Write-Output "Quantime installation completed successfully!"
