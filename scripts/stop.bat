@echo off
chcp 65001 >nul
echo Stopping Web Cluster Manager...
taskkill /fi "WINDOWTITLE eq Server-8000*" /t /f 2>nul
taskkill /fi "WINDOWTITLE eq Agent*" /t /f 2>nul
taskkill /fi "WINDOWTITLE eq WebUI-5173*" /t /f 2>nul
echo Done.
timeout /t 2 >nul
