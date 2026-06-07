# post_install_wizard.ps1
# Quantime Headless Setup Wizard (Executes post-file-copy inside Inno Setup)

$ErrorActionPreference = "Continue"
$appDir = $PSScriptRoot

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
$ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe"
if (-not (Test-Path $ollamaPath)) {
    Write-Output "Ollama not detected. Downloading and installing headlessly..."
    $ollamaSetup = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaSetup
    
    # Run setup silently in the background
    Write-Output "Installing Ollama..."
    $proc = Start-Process -FilePath $ollamaSetup -ArgumentList "/silent" -Wait -PassThru
}

# Start the Ollama process
Write-Output "Launching Ollama Service..."
Start-Process -FilePath $ollamaPath -WindowStyle Hidden
Start-Sleep -Seconds 5

# ---------------------------------------------------------------------
# 4. Ingest and Compile LLM Model
# ---------------------------------------------------------------------
Write-Output "Pulling Gemma LLM model weights (this can take several minutes)..."
Start-Process -FilePath "ollama" -ArgumentList "pull gemma:2b" -Wait -NoNewWindow

Write-Output "Compiling speculative decoding gemma4-agent-mtp model..."
Start-Process -FilePath "ollama" -ArgumentList "create gemma4-agent-mtp -f `"$appDir\Modelfile`"" -Wait -NoNewWindow

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

Write-Output "Installing frontend package packages..."
Push-Location "$appDir\frontend"
Start-Process -FilePath "npm.cmd" -ArgumentList "install" -Wait -NoNewWindow
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
