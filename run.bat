@echo off
rem Start E2 on Windows (http://localhost:8700 by default; set E2_PORT to change). Restarts if already running.
setlocal
cd /d "%~dp0"
if "%E2_PORT%"=="" set E2_PORT=8700
rem 0.0.0.0 = reachable from other devices on the LAN; 127.0.0.1 = this PC only
if "%E2_HOST%"=="" set E2_HOST=0.0.0.0
rem UTF-8 for E2's own files/logs and for the tools it runs (Windows defaults to a legacy code page)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%E2_PORT% .*LISTENING"') do (
  echo Stopping running E2 ^(pid %%p^)...
  taskkill /pid %%p /f >nul 2>&1
)

where uv >nul 2>&1
if %errorlevel%==0 (
  echo Starting E2 on http://localhost:%E2_PORT%
  uv run e2
  goto :end
)

rem No uv: fall back to a plain venv (needs Python 3.12+ from python.org, "Add to PATH" ticked)
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.12+ is not installed. Install uv ^(recommended^):
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo or Python from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  python -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q -e . || (pause & exit /b 1)
echo Starting E2 on http://localhost:%E2_PORT%
".venv\Scripts\python.exe" -m e2.main

:end
endlocal
