@echo off
chcp 65001 >nul
title 仓库记账系统 - Ctrl+C 停止

echo 清理旧进程和缓存...

REM 杀旧进程
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1

REM 清 Python 缓存
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

echo 启动: http://127.0.0.1:5000
echo 关闭: 按 Ctrl+C
echo.

.venv\Scripts\python.exe app.py

pause
