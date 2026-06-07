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
# 4. Ingest and Compile LLM Model
# ---------------------------------------------------------------------
$gpuInfo = Get-GPUMetadata
$gpuName = $gpuInfo.Name
$vram = $gpuInfo.VRAM

# Determine default recommendation
$recommendedIndex = 4 # Default to Gemma 2 2B
$recommendationText = "Gemma 2 2B (Lightweight, low VRAM/CPU)"

if ($vram -ge 12) {
    $recommendedIndex = 1
    $recommendationText = "Gemma 4 12B (Best performance, rich agent reasoning)"
} elseif ($vram -ge 8) {
    $recommendedIndex = 2
    $recommendationText = "Gemma 2 9B (Excellent balance of size & reasoning)"
}

$options = @(
    @{ Index = 1; Name = "Gemma 4 12B"; Tag = "gemma4"; Desc = "High-end reasoning, recommended for >= 12GB VRAM" },
    @{ Index = 2; Name = "Gemma 2 9B"; Tag = "gemma2:9b"; Desc = "Balanced reasoning, recommended for >= 8GB VRAM" },
    @{ Index = 3; Name = "Llama 3 8B"; Tag = "llama3:8b"; Desc = "Alternative LLM model, recommended for >= 8GB VRAM" },
    @{ Index = 4; Name = "Gemma 2 2B"; Tag = "gemma2:2b"; Desc = "Lightweight/CPU model, recommended for < 8GB VRAM" },
    @{ Index = 5; Name = "Skip / Keep Existing"; Tag = ""; Desc = "Do not download any model (use existing setup)" }
)

$choice = $recommendedIndex

if (-not $Silent -and [Environment]::UserInteractive) {
    # Try clear host but ignore errors
    try { Clear-Host } catch {}
    Write-Host "==================================================" -ForegroundColor Magenta
    Write-Host "         QUANTIME MODEL SELECTION WIZARD" -ForegroundColor Magenta
    Write-Host "==================================================" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "Detected Hardware Settings:" -ForegroundColor Cyan
    Write-Host "  GPU Name:  $gpuName" -ForegroundColor Yellow
    Write-Host "  GPU VRAM:  $vram GB" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Recommended Model based on your system:" -ForegroundColor Cyan
    Write-Host "  --> $recommendationText" -ForegroundColor Green
    Write-Host ""
    Write-Host "Please select a model to download and compile:" -ForegroundColor Cyan
    
    foreach ($opt in $options) {
        $prefix = "   "
        if ($opt.Index -eq $recommendedIndex) {
            $prefix = " * "
        }
        $recFlag = ""
        if ($opt.Index -eq $recommendedIndex) {
            $recFlag = " [RECOMMENDED]"
        }
        Write-Host "$prefix$($opt.Index)) $($opt.Name) ($($opt.Tag)) - $($opt.Desc)$recFlag"
    }
    Write-Host ""
    
    $promptMsg = "Enter selection [1-5] (default is $recommendedIndex): "
    $userInput = Read-Host -Prompt $promptMsg
    if ($userInput -match "^[1-5]$") {
        $choice = [int]$userInput
    }
}

$selectedOpt = $options[$choice - 1]
$selectedModelTag = $selectedOpt.Tag

if ($selectedModelTag) {
    Write-Output "Selected Model: $($selectedOpt.Name) ($selectedModelTag)"
    
    # 4.1 Update Modelfile FROM line
    $modelfilePath = "$appDir\Modelfile"
    if (Test-Path $modelfilePath) {
        Write-Output "Updating Modelfile base to FROM $selectedModelTag..."
        $modelfileContent = Get-Content $modelfilePath
        $newModelfile = @()
        $replaced = $false
        foreach ($line in $modelfileContent) {
            if ($line -match "^FROM\s+") {
                $newModelfile += "FROM $selectedModelTag"
                $replaced = $true
            } else {
                $newModelfile += $line
            }
        }
        if (-not $replaced) {
            $newModelfile = @("FROM $selectedModelTag") + $newModelfile
        }
        Set-Content -Path $modelfilePath -Value $newModelfile -Force
    }

    # 4.2 Pull and compile model
    # Get exact path to ollama CLI if possible
    $ollamaCli = "ollama"
    if ($ollamaPath -and (Test-Path $ollamaPath)) {
        $ollamaDir = Split-Path -Parent $ollamaPath
        $possibleCli = Join-Path $ollamaDir "ollama.exe"
        if (Test-Path $possibleCli) {
            $ollamaCli = $possibleCli
        } else {
            $ollamaCli = $ollamaPath
        }
    }
    
    Write-Output "Pulling $selectedModelTag LLM model weights (this can take several minutes)..."
    Start-Process -FilePath $ollamaCli -ArgumentList "pull $selectedModelTag" -Wait -NoNewWindow
    
    Write-Output "Compiling speculative decoding gemma4-agent-mtp model..."
    Start-Process -FilePath $ollamaCli -ArgumentList "create gemma4-agent-mtp -f `"$modelfilePath`"" -Wait -NoNewWindow
} else {
    Write-Output "Skipping model download and compilation step as requested."
}

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
