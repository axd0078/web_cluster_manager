@echo off
chcp 65001 >nul
title Agent
set "ROOT=%~dp0.."

where python >nul 2>&1 && (set PYTHON=python) || (
    if exist "D:\Myanaconda\anaconda3\python.exe" (set PYTHON=D:\Myanaconda\anaconda3\python.exe
    ) else if exist "%USERPROFILE%\anaconda3\python.exe" (set PYTHON=%USERPROFILE%\anaconda3\python.exe
    ) else (echo Python not found & pause & exit /b 1)
)

if "%WCM_SERVER_URL%"=="" set "WCM_SERVER_URL=ws://localhost:8000/ws/agent"
if "%WCM_API_URL%"=="" set "WCM_API_URL=http://localhost:8000/api/v2"
set "WCM_CA_CERT_ARG="
if not "%WCM_CA_CERT%"=="" set "WCM_CA_CERT_ARG=--ca-cert "%WCM_CA_CERT%""
if not exist "%ROOT%\agent\agent_data\agent_config.json" if "%WCM_ENROLLMENT_TOKEN%"=="" (
    echo Agent is not enrolled. Set WCM_ENROLLMENT_TOKEN from System Settings.
    pause
    exit /b 1
)
echo Agent connecting to %WCM_SERVER_URL%
echo.
cd /d "%ROOT%\agent"
"%PYTHON%" main.py --server "%WCM_SERVER_URL%" --api "%WCM_API_URL%" %WCM_CA_CERT_ARG%
pause
