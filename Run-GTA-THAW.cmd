@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\python\python.exe" goto bundled
if exist "%~dp0PORTABLE_RUNTIME_REQUIRED" goto missing_runtime
where py >nul 2>nul
if errorlevel 1 goto missing_source_python
py -3 -E -s -B "%~dp0tools\gta\launch_playtest.py" %*
goto finished
:bundled
"%~dp0runtime\python\python.exe" -E -s -B "%~dp0tools\gta\launch_playtest.py" %*
:finished
set "launcher_result=%errorlevel%"
if not "%launcher_result%"=="0" pause
exit /b %launcher_result%
:missing_runtime
echo The bundled runtime is missing. Extract the complete GTA THAW integration package again.
pause
exit /b 1
:missing_source_python
echo This source checkout needs 64-bit Python 3.12+ with Tk. The integration package includes it.
pause
exit /b 1
