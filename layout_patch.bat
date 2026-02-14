@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title ColorOS Layout Patch Tool

set "TOOL_DIR=%~dp0"
set "TOOL_DIR=%TOOL_DIR:~0,-1%"

python "%TOOL_DIR%\layout_patch_main.py" %*
if errorlevel 1 (
    echo.
    pause >nul
)
