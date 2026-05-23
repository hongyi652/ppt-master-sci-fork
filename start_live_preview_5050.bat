@echo off
setlocal

chcp 65001 >nul

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PROJECT_PATH=%~1"

if not defined PROJECT_PATH (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$latest = Get-ChildItem -LiteralPath 'projects' -Directory | Where-Object { $_.Name -notmatch '^[._]' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName; if ($latest) { $latest }"`) do set "PROJECT_PATH=%%I"
)

if not defined PROJECT_PATH (
    echo [ERROR] 未找到可预览的项目。
    echo 用法: %~nx0 projects\your_project_name
    exit /b 1
)

echo [INFO] 使用首选端口 5050 启动 Live Preview: "%PROJECT_PATH%"
py -3.11 skills\ppt-master\scripts\start_live_preview.py "%PROJECT_PATH%" --port 5050 --wait 20 --no-browser
exit /b %ERRORLEVEL%