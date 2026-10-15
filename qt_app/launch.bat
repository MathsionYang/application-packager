@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\DesktopPackager\DesktopPackager.exe" (
    start "" "%~dp0dist\DesktopPackager\DesktopPackager.exe" %*
    exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" %*
    exit /b 0
)
py -3.13 "%~dp0main.py" %*
if errorlevel 1 (
    echo Application launch failed. See README.md for dependency setup.
    pause
    exit /b 1
)
