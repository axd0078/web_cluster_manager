# Windows one-line install script
# Run in PowerShell: irm https://YOUR_SERVER/install.ps1 | iex
# Set WCM_ENROLLMENT_TOKEN in the current process before running.

param(
    [Parameter(Mandatory=$true)][string]$ServerUrl,
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [Parameter(Mandatory=$true)][string]$CaCert,
    [ValidateSet("CurrentUser", "System")][string]$RunAs = "CurrentUser"
)

$ErrorActionPreference = "Stop"
Write-Host "=== Web Cluster Manager Agent Installer ===" -ForegroundColor Cyan
if (-not $env:WCM_ENROLLMENT_TOKEN) {
    throw "WCM_ENROLLMENT_TOKEN is required and must be supplied as an environment variable"
}
if (-not (Test-Path -LiteralPath $CaCert)) { throw "CA certificate not found: $CaCert" }

# Check Python
try {
    python --version 2>&1 | Out-Null
} catch {
    Write-Host "Python not found. Please install Python 3.9+ from https://python.org" -ForegroundColor Red
    exit 1
}

# Create install directory
$InstallDir = "$env:ProgramFiles\WebClusterAgent"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host "Installing to $InstallDir"

# Copy agent files
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item -Recurse -Force "$ScriptDir\*" $InstallDir -Exclude "install.ps1"
Copy-Item -Force -LiteralPath $CaCert -Destination "$InstallDir\ca.crt"

# Install dependencies
Write-Host "Installing dependencies..."
pip install -r "$InstallDir\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { throw "Agent dependency installation failed" }

# Enroll once before installing the persistent service. The enrollment token is
# never embedded in the scheduled task command line.
python "$InstallDir\main.py" --enroll-only --server $ServerUrl --api $ApiUrl --ca-cert "$InstallDir\ca.crt" --data-dir "$InstallDir\data"
if ($LASTEXITCODE -ne 0) { throw "Secure Agent enrollment failed" }
$env:WCM_ENROLLMENT_TOKEN = $null

# Create scheduled task to run agent on startup
$TaskName = "WebClusterAgent"
$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$InstallDir\main.py`" --server $ServerUrl --api $ApiUrl --ca-cert `"$InstallDir\ca.crt`" --data-dir `"$InstallDir\data`"" -WorkingDirectory $InstallDir
if ($RunAs -eq "System") {
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
} else {
    # Docker Desktop runs in the interactive user's session and its named pipe
    # is normally unavailable to a SYSTEM scheduled task.
    $CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force | Out-Null

# Start immediately
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installation complete! Agent is running." -ForegroundColor Green
Write-Host "Server: $ServerUrl"
