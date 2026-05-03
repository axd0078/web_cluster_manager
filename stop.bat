@echo off
chcp 65001 >nul
echo 正在停止 Web 集群管理系统...

REM 停止服务端（按窗口标题匹配）
taskkill /fi "WINDOWTITLE eq 服务端*" /f 2>nul

REM 停止 Agent
taskkill /fi "WINDOWTITLE eq Agent*" /f 2>nul

REM 停止前端
taskkill /fi "WINDOWTITLE eq 前端*" /f 2>nul

REM 备用：按进程名
taskkill /f /im uvicorn.exe 2>nul
taskkill /f /im python.exe /fi "WINDOWTITLE eq Agent" 2>nul
taskkill /f /im node.exe /fi "WINDOWTITLE eq *5173*" 2>nul

echo 已停止。
timeout /t 2 >nul
