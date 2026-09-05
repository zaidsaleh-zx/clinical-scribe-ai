@echo off
setlocal
cd /d "%~dp0"
set "URL=http://127.0.0.1:8000"

rem Reuse the existing app when it is already running.
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  start "" "%URL%"
  exit /b 0
)

if not exist "backend\main.py" (
  echo Could not find backend\main.py. Run this file from the project folder.
  pause
  exit /b 1
)

rem Prefer the project virtual environment so faster-whisper and the other
rem installed dependencies are the same ones used during development.
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

rem Start the FastAPI app in a separate minimized window.
start "ClinicalScribe AI server" /min "%PYTHON%" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

rem Give Uvicorn a short moment to bind, then open the dashboard.
for /l %%i in (1,1,20) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
  if not errorlevel 1 (
    start "" "%URL%"
    exit /b 0
  )
  ping 127.0.0.1 -n 2 >nul
)

echo The server did not become ready. Check that Python and the project requirements are installed.
pause
exit /b 1
