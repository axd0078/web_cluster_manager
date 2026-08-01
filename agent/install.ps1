# Run from an elevated PowerShell session. The Ed25519 public key file name
# must be the fingerprint key_id emitted by tools/update_package.py plus ".pub".
param(
    [Parameter(Mandatory=$true)][string]$ServerUrl,
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [Parameter(Mandatory=$true)][string]$CaCert,
    [Parameter(Mandatory=$true)][string]$UpdatePublicKey,
    [ValidateSet("CurrentUser", "System")][string]$RunAs = "CurrentUser"
)

$ErrorActionPreference = "Stop"
Write-Host "=== Web Cluster Manager Agent Installer ===" -ForegroundColor Cyan

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "The trusted updater bootstrap must run from an elevated PowerShell session"
}
if ($RunAs -eq "System") {
    throw "The ordinary Agent cannot run as SYSTEM; install the separate Broker for privileged access"
}
if (-not (Test-Path -LiteralPath $CaCert -PathType Leaf)) {
    throw "CA certificate not found"
}
if (-not (Test-Path -LiteralPath $UpdatePublicKey -PathType Leaf)) {
    throw "Ed25519 update public key not found"
}
$KeyLeaf = Split-Path -Leaf $UpdatePublicKey
if ($KeyLeaf -notmatch '^[A-Za-z0-9_.-]{1,64}\.pub$') {
    throw "Update public key must be named <key-id>.pub"
}

$Python = (Get-Command python -ErrorAction Stop).Source
& $Python --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Python 3 is required" }

$InstallDir = Join-Path $env:ProgramFiles "WebClusterAgent"
$RuntimeDir = Join-Path $InstallDir "runtime"
$ReleasesDir = Join-Path $InstallDir "releases"
$BootstrapDir = Join-Path $ReleasesDir "bootstrap-3.1.0"
$PayloadDir = Join-Path $BootstrapDir "payload"
$TrustedKeysDir = Join-Path $InstallDir "trusted_update_keys"
$DataDir = Join-Path $env:ProgramData "WebClusterAgent"
$SpoolDir = Join-Path $DataDir "update-spool"
$LegacyDataDir = Join-Path $InstallDir "data"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ActivePointer = Join-Path $InstallDir "active.json"
$FirstBootstrap = -not (Test-Path -LiteralPath $ActivePointer -PathType Leaf)

New-Item -ItemType Directory -Force -Path $InstallDir, $RuntimeDir, $ReleasesDir, $TrustedKeysDir | Out-Null
if ((Test-Path -LiteralPath $LegacyDataDir -PathType Container) -and
    -not (Test-Path -LiteralPath $DataDir)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DataDir) | Out-Null
    Move-Item -LiteralPath $LegacyDataDir -Destination $DataDir
}
New-Item -ItemType Directory -Force -Path $DataDir, $SpoolDir | Out-Null
$AgentSpoolDirs = @("incoming", "packages", "requests", "agent-status", "health", "cancel")
$HelperSpoolDirs = @("processing", "helper-status")
foreach ($Name in ($AgentSpoolDirs + $HelperSpoolDirs)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $SpoolDir $Name) | Out-Null
}

Copy-Item -Force -LiteralPath (Join-Path $ScriptDir "launcher.py") -Destination $RuntimeDir
Copy-Item -Force -LiteralPath (Join-Path $ScriptDir "update_helper.py") -Destination $RuntimeDir
Copy-Item -Force -LiteralPath (Join-Path $ScriptDir "update_package.py") -Destination $RuntimeDir
Copy-Item -Force -LiteralPath (Join-Path $ScriptDir "requirements.txt") -Destination $RuntimeDir
if ($FirstBootstrap) {
    New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null
    Get-ChildItem -LiteralPath $ScriptDir -Filter "*.py" -File |
        Where-Object { $_.Name -ne "updater.py" } |
        Copy-Item -Force -Destination $PayloadDir
    Copy-Item -Force -LiteralPath (Join-Path $ScriptDir "requirements.txt") -Destination $PayloadDir
}
Copy-Item -Force -LiteralPath $CaCert -Destination (Join-Path $InstallDir "ca.crt")
Copy-Item -Force -LiteralPath $UpdatePublicKey -Destination (Join-Path $TrustedKeysDir $KeyLeaf)

$RuntimeVenv = Join-Path $RuntimeDir "venv"
$BootstrapVenv = Join-Path $BootstrapDir "venv"
if (-not (Test-Path -LiteralPath (Join-Path $RuntimeVenv "Scripts\python.exe"))) {
    & $Python -m venv --copies $RuntimeVenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the immutable updater runtime" }
}
if ($FirstBootstrap -and -not (Test-Path -LiteralPath (Join-Path $BootstrapVenv "Scripts\python.exe"))) {
    & $Python -m venv --copies $BootstrapVenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the bootstrap Agent release" }
}
$RuntimePython = Join-Path $RuntimeVenv "Scripts\python.exe"
$BootstrapPython = Join-Path $BootstrapVenv "Scripts\python.exe"
& $RuntimePython -I -m pip install --disable-pip-version-check -r (Join-Path $RuntimeDir "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "Updater runtime dependency installation failed" }
if ($FirstBootstrap) {
    & $BootstrapPython -I -m pip install --disable-pip-version-check -r (Join-Path $PayloadDir "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "Bootstrap Agent dependency installation failed" }
}

if ($FirstBootstrap) {
    $Active = @{
        schema_version = 1
        release_id = "bootstrap-3.1.0"
        version = "3.1.0"
        entrypoint = "main.py"
        activation_execution_id = ""
        activation_operation = ""
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($ActivePointer, $Active, [Text.UTF8Encoding]::new($false))
}

$env:WCM_AGENT_DATA_DIR = $DataDir
$env:WCM_UPDATE_SPOOL_DIR = $SpoolDir
$env:WCM_UPDATE_TRUSTED_KEYS_DIR = $TrustedKeysDir
$env:WCM_UPDATE_ACTIVE_POINTER = $ActivePointer
$env:WCM_ENABLE_AGENT_UPDATES = "true"
if (-not (Test-Path -LiteralPath (Join-Path $DataDir "agent_config.json") -PathType Leaf)) {
    if (-not $env:WCM_ENROLLMENT_TOKEN) {
        throw "WCM_ENROLLMENT_TOKEN is required for the first Agent enrollment"
    }
    & $BootstrapPython -I (Join-Path $PayloadDir "main.py") --enroll-only `
        --server $ServerUrl --api $ApiUrl --ca-cert (Join-Path $InstallDir "ca.crt") `
        --data-dir $DataDir
    if ($LASTEXITCODE -ne 0) { throw "Secure Agent enrollment failed" }
}
$env:WCM_ENROLLMENT_TOKEN = $null

$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
& icacls.exe $InstallDir /inheritance:r `
    /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" |
    Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to secure the Agent installation ACL" }
& icacls.exe $DataDir /inheritance:r `
    /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${CurrentUser}:(OI)(CI)M" |
    Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to secure the Agent data ACL" }
& icacls.exe $SpoolDir /inheritance:r `
    /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${CurrentUser}:(RX)" |
    Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to secure the update spool ACL" }
foreach ($Name in $AgentSpoolDirs) {
    & icacls.exe (Join-Path $SpoolDir $Name) /inheritance:r `
        /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${CurrentUser}:(OI)(CI)M" |
        Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to secure Agent update inbox ACL" }
}
& icacls.exe (Join-Path $SpoolDir "processing") /inheritance:r `
    /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to secure updater processing ACL" }
& icacls.exe (Join-Path $SpoolDir "helper-status") /inheritance:r `
    /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${CurrentUser}:(OI)(CI)RX" |
    Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to secure updater status ACL" }

$AgentTaskName = "WebClusterAgent"
$AgentArguments = @(
    "`"$(Join-Path $RuntimeDir 'launcher.py')`""
    "--install-root `"${InstallDir}`""
    "--agent-data-dir `"${DataDir}`""
    "--server `"${ServerUrl}`""
    "--api `"${ApiUrl}`""
    "--ca-cert `"$(Join-Path $InstallDir 'ca.crt')`""
) -join " "
$AgentAction = New-ScheduledTaskAction -Execute $RuntimePython -Argument $AgentArguments -WorkingDirectory $RuntimeDir
if ($RunAs -eq "System") {
    $AgentTrigger = New-ScheduledTaskTrigger -AtStartup
    $AgentPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
} else {
    $AgentTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $AgentPrincipal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
}
$TaskSettings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $AgentTaskName -Action $AgentAction -Trigger $AgentTrigger `
    -Principal $AgentPrincipal -Settings $TaskSettings -Force | Out-Null

$HelperTaskName = "WebClusterAgentUpdater"
$HelperArguments = @(
    "`"$(Join-Path $RuntimeDir 'update_helper.py')`""
    "--install-root `"${InstallDir}`""
    "--spool-root `"${SpoolDir}`""
    "--trusted-keys `"${TrustedKeysDir}`""
) -join " "
$HelperAction = New-ScheduledTaskAction -Execute $RuntimePython -Argument $HelperArguments -WorkingDirectory $RuntimeDir
$HelperTrigger = New-ScheduledTaskTrigger -AtStartup
$HelperPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $HelperTaskName -Action $HelperAction -Trigger $HelperTrigger `
    -Principal $HelperPrincipal -Settings $TaskSettings -Force | Out-Null

Start-ScheduledTask -TaskName $HelperTaskName
Start-ScheduledTask -TaskName $AgentTaskName
Write-Host "Trusted Agent bootstrap installed and started." -ForegroundColor Green
Write-Host "Agent data: $DataDir"
Write-Host "Signed update inbox: $SpoolDir"
