param(
    [Parameter(Mandatory = $true)][string]$NodeId,
    [Parameter(Mandatory = $true)][string]$BrokerToken,
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [string]$Python = "python"
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell window."
}
if (-not $ServerUrl.StartsWith("wss://")) {
    throw "The privileged Broker requires a wss:// URL."
}
$parsedNodeId = [Guid]::Empty
if (-not [Guid]::TryParseExact($NodeId, "D", [ref]$parsedNodeId)) {
    throw "NodeId must be the registered node UUID."
}
if ($ServerUrl -notmatch '^wss://[A-Za-z0-9._~:/\[\]-]+/ws/broker/?$') {
    throw "ServerUrl must be a plain wss:// host URL ending in /ws/broker."
}

$root = Join-Path $env:ProgramData "WebClusterManager\Broker"
if (Test-Path -LiteralPath $root) {
    & takeown.exe /F $root /A /R /D Y | Out-Null
    & icacls $root /grant:r "*S-1-5-32-544:(OI)(CI)(F)" /T | Out-Null
}
New-Item -ItemType Directory -Force -Path $root | Out-Null
$appRoot = Join-Path $root "app"
$brokerRoot = Join-Path $appRoot "broker"
$agentRoot = Join-Path $appRoot "agent"
New-Item -ItemType Directory -Force -Path $brokerRoot, $agentRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "main.py") -Destination $brokerRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "__init__.py") -Destination $brokerRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "..\agent\terminal_crypto.py") -Destination $agentRoot -Force
$emptyInit = Join-Path $agentRoot "__init__.py"
if (-not (Test-Path -LiteralPath $emptyInit)) {
    [IO.File]::WriteAllText($emptyInit, "", [Text.UTF8Encoding]::new($false))
}
$tokenFile = Join-Path $root "broker.token"
[IO.File]::WriteAllText($tokenFile, $BrokerToken, [Text.UTF8Encoding]::new($false))

$script = Join-Path $brokerRoot "main.py"
$pythonPath = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop).Source
$runner = Join-Path $root "run-broker.ps1"
function ConvertTo-SingleQuotedLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}
$runnerLines = @(
    "`$ErrorActionPreference = 'Stop'",
    "`$env:WCM_BROKER_NODE_ID = $(ConvertTo-SingleQuotedLiteral $NodeId)",
    "`$env:WCM_BROKER_TOKEN_FILE = $(ConvertTo-SingleQuotedLiteral $tokenFile)",
    "`$env:WCM_BROKER_SERVER_URL = $(ConvertTo-SingleQuotedLiteral $ServerUrl)",
    "& $(ConvertTo-SingleQuotedLiteral $pythonPath) $(ConvertTo-SingleQuotedLiteral $script)"
)
[IO.File]::WriteAllLines($runner, $runnerLines, [Text.UTF8Encoding]::new($false))
& icacls $root /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" | Out-Null
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal (
    New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
)
Register-ScheduledTask -TaskName "WebClusterManagerBroker" -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName "WebClusterManagerBroker"
$BrokerToken = $null
