@echo off
chcp 65001 >nul
title 前端 :5173
cd /d "%~dp0web-ui"
echo 启动前端 http://localhost:5173
echo.
npm run dev
