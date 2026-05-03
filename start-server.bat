@echo off
chcp 65001 >nul
title 服务端 :8000
cd /d "%~dp0server"
echo 启动服务端 http://localhost:8000
echo OpenAPI 文档: http://localhost:8000/docs
echo.
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
