# build_model.ps1
# Quantime Model Builder for Ollama (Windows PowerShell) - Local Offline Mode

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "    Quantime Ollama Model Compiler (Offline)" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# 1. Check if Ollama is running
Write-Host "Checking if Ollama service is reachable..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 5
    Write-Host "Ollama service is online." -ForegroundColor Green
} catch {
    Write-Host "Error: Could not connect to Ollama on http://localhost:11434." -ForegroundColor Red
    Write-Host "Please ensure Ollama is installed and running, then try again." -ForegroundColor Red
    Exit 1
}

# 2. Build custom model directly using the local gemma4 image
Write-Host "Building custom model 'gemma4-agent-mtp' from Modelfile using local gemma4 base..." -ForegroundColor Yellow
if (Test-Path "Modelfile") {
    & ollama create gemma4-agent-mtp -f Modelfile
} else {
    Write-Host "Error: Modelfile not found in current directory." -ForegroundColor Red
    Exit 1
}

# 3. Verify custom model
Write-Host "Verifying custom model deployment..." -ForegroundColor Yellow
$tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get
$modelExists = $false
foreach ($model in $tags.models) {
    if ($model.name -like "*gemma4-agent-mtp*") {
        $modelExists = $true
        break
    }
}

if ($modelExists) {
    Write-Host "Success: 'gemma4-agent-mtp' compiled and deployed successfully!" -ForegroundColor Green
    Write-Host "You can run it with: ollama run gemma4-agent-mtp" -ForegroundColor Cyan
} else {
    Write-Host "Error: Compiled model could not be found in Ollama's model list." -ForegroundColor Red
    Exit 1
}
