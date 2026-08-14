@echo off
setlocal
pushd "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv was not found. Create the project environment first.
    set "EXIT_CODE=1"
    goto :finish
)

".venv\Scripts\python.exe" "scripts\maintain_data.py"
set "EXIT_CODE=%ERRORLEVEL%"

:finish
popd
exit /b %EXIT_CODE%
