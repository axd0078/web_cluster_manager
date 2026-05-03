@echo off
chcp 65001 >nul
title Agent
cd /d "%~dp0agent"
echo 启动 Agent — 连接 ws://localhost:8000
echo.
python main.py
