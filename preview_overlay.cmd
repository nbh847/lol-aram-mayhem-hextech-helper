@echo off
setlocal
set "HEXTECH_PROJECT_ROOT=%~dp0"

if not exist "%HEXTECH_PROJECT_ROOT%.venv\Scripts\python.exe" (
    echo [ERROR] .venv was not found. Create the project environment first.
    exit /b 1
)

"%HEXTECH_PROJECT_ROOT%.venv\Scripts\python.exe" "%HEXTECH_PROJECT_ROOT%scripts\preview_overlay.py"
exit /b %ERRORLEVEL%
