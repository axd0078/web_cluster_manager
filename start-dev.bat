@echo off
chcp 65001 >nul
title Web 集群管理系统 — 启动器

echo ========================================
echo   Web 集群管理系统 — 启动中...
echo ========================================
echo.

set ROOT=%~dp0

REM ── 检查 Python ──
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM ── 检查 Node ──
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo 警告: 未找到 Node.js，前端将无法启动
)

echo 启动服务端 (http://localhost:8000)...
start "Cluster-Server" cmd /c "cd /d "%ROOT%server" && title 服务端 :8000 && uvicorn main:app --host 0.0.0.0 --port 8000"

echo 等待服务端就绪...
timeout /t 3 /nobreak >nul

echo 启动 Agent...
start "Cluster-Agent" cmd /c "cd /d "%ROOT%agent" && title Agent && python main.py"

echo 启动前端 (http://localhost:5173)...
start "Cluster-WebUI" cmd /c "cd /d "%ROOT%web-ui" && title 前端 :5173 && npm run dev"

echo.
echo ========================================
echo   启动完成！
echo.
echo   浏览器打开: http://localhost:5173
echo   账号: admin  密码: admin123
echo.
echo   停止: 关闭窗口 或 双击 stop.bat
echo ========================================
echo.
timeout /t 5 >nul
