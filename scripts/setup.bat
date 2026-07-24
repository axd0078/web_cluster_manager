@echo off
chcp 65001 >nul
echo ========================================
echo   Web Cluster Manager — Setup
echo ========================================
echo.

set "ROOT=%~dp0.."

REM -- Find Python --
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=python"
) else if exist "D:\Myanaconda\anaconda3\python.exe" (
    set "PYTHON=D:\Myanaconda\anaconda3\python.exe"
) else if exist "%USERPROFILE%\anaconda3\python.exe" (
    set "PYTHON=%USERPROFILE%\anaconda3\python.exe"
) else (
    echo ERROR: Python not found. Install Python 3.8+
    pause
    exit /b 1
)

echo Python: %PYTHON%

echo.
echo [1/4] Installing server dependencies...
"%PYTHON%" -m pip install -r "%ROOT%\server\requirements.txt" -q
if %errorlevel% neq 0 (echo ERROR: Failed to install server deps & pause & exit /b 1)
echo   OK

echo [2/4] Installing agent dependencies...
"%PYTHON%" -m pip install -r "%ROOT%\agent\requirements.txt" -q
if %errorlevel% neq 0 (echo ERROR: Failed to install agent deps & pause & exit /b 1)
echo   OK

echo [3/4] Installing frontend dependencies...
cd /d "%ROOT%\web-ui"
call npm install --silent 2>nul
if %errorlevel% neq 0 (echo ERROR: npm install failed & pause & exit /b 1)
echo   OK
cd /d "%ROOT%"

echo [4/4] Building frontend...
cd /d "%ROOT%\web-ui"
call npm run build --silent 2>nul
if %errorlevel% neq 0 (echo ERROR: Frontend build failed & pause & exit /b 1)
echo   OK
cd /d "%ROOT%"

echo.
echo ========================================
echo   Setup complete!
echo   Run: scripts\start-dev.bat
echo   Browser: http://localhost:5173
echo   First start requires WCM_BOOTSTRAP_ADMIN_PASSWORD (12+ chars).
echo   Agent enrollment requires a one-time token created in System Settings.
echo ========================================
pause
