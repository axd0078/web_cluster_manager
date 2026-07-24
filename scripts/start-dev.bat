@echo off
chcp 65001 >nul
title Web Cluster Manager

echo ========================================
echo   Web Cluster Manager
echo ========================================
echo.

set "ROOT=%~dp0.."

where python >nul 2>&1
if %errorlevel% equ 0 (set "PYTHON=python") else (
    if exist "D:\Myanaconda\anaconda3\python.exe" (set "PYTHON=D:\Myanaconda\anaconda3\python.exe"
    ) else if exist "%USERPROFILE%\anaconda3\python.exe" (set "PYTHON=%USERPROFILE%\anaconda3\python.exe"
    ) else (echo ERROR: Python not found & pause & exit /b 1)
)
echo Python: %PYTHON%

echo.
echo [1/3] Starting server...
if not exist "%ROOT%\server\data\cluster.db" if "%WCM_BOOTSTRAP_ADMIN_PASSWORD%"=="" (
    echo ERROR: First start requires WCM_BOOTSTRAP_ADMIN_PASSWORD with at least 12 characters.
    pause
    exit /b 1
)
start "Server-8000" cmd /k "cd /d ""%ROOT%\server"" && ""%PYTHON%"" -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo Waiting for server health...
powershell -NoProfile -Command "$ok=$false; 1..30 | %% { try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/v2/health -TimeoutSec 1; if($r.StatusCode -eq 200){$ok=$true;break} } catch {}; Start-Sleep -Milliseconds 500 }; if(-not $ok){exit 1}"
if %errorlevel% neq 0 (echo ERROR: Server failed health check & pause & exit /b 1)

echo [2/3] Starting agent...
if exist "%ROOT%\agent\agent_data\agent_config.json" (
    start "Agent" cmd /k "cd /d ""%ROOT%\agent"" && ""%PYTHON%"" main.py"
) else if not "%WCM_ENROLLMENT_TOKEN%"=="" (
    start "Agent" cmd /k "cd /d ""%ROOT%\agent"" && ""%PYTHON%"" main.py"
) else (
    echo   SKIPPED: create an enrollment token in System Settings and set WCM_ENROLLMENT_TOKEN.
)

echo [3/3] Starting webui...
start "WebUI-5173" cmd /k "cd /d ""%ROOT%\web-ui"" && npm run dev"

echo.
echo ========================================
echo   All started!
echo   Browser: http://localhost:5173
echo   Use the administrator password supplied at first startup.
echo ========================================
pause
