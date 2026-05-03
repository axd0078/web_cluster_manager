# ============================================================
# Web Cluster Manager — Agent 安装脚本 (Windows PowerShell)
# ============================================================
# 用法:
#   以管理员身份运行: 右键 → 以管理员身份运行
#   或: powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = "$env:ProgramFiles\WebClusterAgent"
$ServiceName = "WebClusterAgent"

Write-Host "=== Web Cluster Manager Agent Installer ===" -ForegroundColor Cyan

# ── 1. 权限检查 ──
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "ERROR: 需要管理员权限。请右键 → 以管理员身份运行。" -ForegroundColor Red
    pause
    exit 1
}

# ── 2. 检查 Python ──
$PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonPath) {
    Write-Host "ERROR: 未找到 Python，请先安装 Python 3.8+ 并添加到 PATH。" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "Python: $PythonPath" -ForegroundColor Green

# ── 3. 创建安装目录 ──
if (Test-Path $InstallDir) {
    Write-Host "Stopping existing agent..." -ForegroundColor Yellow
    Stop-Service $ServiceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Write-Host "Installing to $InstallDir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ── 4. 复制文件（排除无用文件） ──
$Exclude = @("install.ps1", "__pycache__", ".pyc", ".git", ".gitignore", "*.pyc")
Write-Host "Copying files..." -ForegroundColor Cyan

Get-ChildItem -Path $ScriptDir -Recurse |
    Where-Object {
        $full = $_.FullName
        -not ($Exclude | Where-Object { $full -like "*$_*" }) -and
        -not $_.PSIsContainer
    } |
    ForEach-Object {
        $target = Join-Path $InstallDir $_.FullName.Substring($ScriptDir.Length + 1)
        $targetDir = Split-Path $target -Parent
        if (-not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        }
        Copy-Item $_.FullName -Destination $target -Force
        Write-Host "  $($_.Name)" -ForegroundColor Gray
    }

# ── 5. 安装 Python 依赖 ──
Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
Start-Process -FilePath "pip" -ArgumentList "install", "-r", (Join-Path $InstallDir "requirements.txt"), "--quiet" -Wait -NoNewWindow

# ── 6. 创建 Windows 服务（使用 nssm 方式） ──
# 简化方案：创建开机启动的快捷方式或计划任务
Write-Host "Setting up auto-start..." -ForegroundColor Cyan

# 检查是否安装了 nssm
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssm) {
    Write-Host "Using nssm to create Windows service..." -ForegroundColor Green
    nssm stop $ServiceName 2>$null
    nssm remove $ServiceName confirm 2>$null
    nssm install $ServiceName "$PythonPath" "`"$InstallDir\main.py`""
    nssm set $ServiceName AppDirectory "$InstallDir"
    nssm set $ServiceName DisplayName "Web Cluster Manager Agent"
    nssm set $ServiceName Start SERVICE_AUTO_START
    nssm start $ServiceName
    Write-Host "Service installed and started." -ForegroundColor Green
}
else {
    Write-Host "nssm not found, using scheduled task instead..." -ForegroundColor Yellow
    # Create a scheduled task that runs at startup
    $TaskName = "WebClusterAgent"
    $Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$InstallDir\main.py`"" -WorkingDirectory $InstallDir
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Scheduled task '$TaskName' created and started." -ForegroundColor Green
}

# ── 7. 完成 ──
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Agent 安装完成!" -ForegroundColor Green
Write-Host "  安装目录: $InstallDir" -ForegroundColor White
Write-Host "  配置修改: $InstallDir\config.json" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "启动后请修改 config.json:" -ForegroundColor Yellow
Write-Host "  - server_url: 改为你的服务端地址" -ForegroundColor Yellow
Write-Host "  - node_id: 改为唯一的节点标识" -ForegroundColor Yellow
Write-Host "  - token: 改为服务端配置的 AGENT_TOKEN" -ForegroundColor Yellow
Write-Host ""
pause
