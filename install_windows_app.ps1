# install_windows_app.ps1
# Programmatically configures Quantime to start silently on Windows Logon.

$vbsPath = Join-Path $PSScriptRoot "run_quantime_hidden.vbs"
$taskName = "QuantimeServer"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       Quantime Autostart Service Installer" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will schedule Quantime to launch silently in the"
Write-Host "background whenever you log into Windows."
Write-Host "This keeps the local scheduler and remote sync running headlessly."
Write-Host ""

if (-not (Test-Path $vbsPath)) {
    Write-Error "Error: run_quantime_hidden.vbs not found in current folder!"
    exit 1
}

# Create Scheduled Task Action, Trigger, and Settings
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Register the task
Write-Host "Registering task '$taskName' in Windows Task Scheduler..." -ForegroundColor Yellow
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Launches Quantime backend, Vite, and remote sync services headlessly at login." -Force -ErrorAction Stop
    Write-Host "Success! Quantime is scheduled to run on logon." -ForegroundColor Green
    Write-Host "You can manage or test this task in Windows Task Scheduler under the name '$taskName'." -ForegroundColor Green
    
    # Optionally start it now
    $response = Read-Host "Would you like to run the background task right now? (y/n)"
    if ($response -eq 'y' -or $response -eq 'yes') {
        Start-ScheduledTask -TaskName $taskName
        Write-Host "Task started successfully!" -ForegroundColor Green
    }
} catch {
    Write-Host "Failed to register scheduled task: $_" -ForegroundColor Red
    Write-Host "Please run this PowerShell console as Administrator and try again." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
