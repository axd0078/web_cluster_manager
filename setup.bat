@echo off
chcp 65001 >nul
echo ========================================
echo   Web 集群管理系统 — 环境初始化
echo ========================================
echo.

set ROOT=%~dp0

REM ── 1. 服务端依赖 ──
echo [1/4] 安装服务端依赖...
cd /d "%ROOT%server"
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo   错误: 服务端依赖安装失败，请检查 Python 和 pip
    pause
    exit /b 1
)
echo   OK

REM ── 2. Agent 依赖 ──
echo [2/4] 安装 Agent 依赖...
cd /d "%ROOT%agent"
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo   错误: Agent 依赖安装失败
    pause
    exit /b 1
)
echo   OK

REM ── 3. 前端依赖 ──
echo [3/4] 安装前端依赖（可能需要几分钟）...
cd /d "%ROOT%web-ui"
call npm install --silent 2>nul
if %errorlevel% neq 0 (
    echo   错误: 前端依赖安装失败，请检查 Node.js 和 npm
    pause
    exit /b 1
)
echo   OK

REM ── 4. 编译前端 ──
echo [4/4] 编译前端...
call npm run build --silent 2>nul
if %errorlevel% neq 0 (
    echo   警告: 前端编译失败，开发模式仍可使用 npm run dev
)
echo   OK

cd /d "%ROOT%"

echo.
echo ========================================
echo   初始化完成！
echo.
echo   启动系统: 双击 start-dev.bat
echo   浏览器打开: http://localhost:5173
echo   账号: admin  密码: admin123
echo ========================================
echo.
pause
