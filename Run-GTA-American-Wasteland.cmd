@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0GTA American Wasteland.exe" (
    start "" "%~dp0GTA American Wasteland.exe"
    exit /b 0
)
if exist "%~dp0runtime\python\pythonw.exe" (
    start "" "%~dp0runtime\python\pythonw.exe" -E -s -B "%~dp0tools\gta\launcher.py"
    exit /b 0
)
if exist "%~dp0PORTABLE_RUNTIME_REQUIRED" (
    echo Extract the complete release ZIP. The bundled runtime is missing.
    echo Rozpakuj cala paczke ZIP. Brakuje dolaczonego srodowiska.
    pause
    exit /b 1
)
where py >nul 2>nul
if errorlevel 1 (
    echo Source checkouts need 64-bit Python 3.12+ with Tk. Download the release ZIP for ready-to-play use.
    pause
    exit /b 1
)
py -3 -E -s -B "%~dp0tools\gta\launcher.py"
exit /b %errorlevel%
