@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Prefer the project virtual environment so Live Audio uses the installed
rem faster-whisper dependency instead of an unrelated system Python.
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

echo ============================================================
echo   ClinicalScribe AI - Phone Server Launcher
echo ============================================================
echo.

rem --- 1. Start the backend server in its own window ---
set "URL=http://127.0.0.1:8000"
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo [OK] Backend already running on port 8000.
) else (
  echo [..] Starting backend server...
  start "ClinicalScribe AI Backend" /min cmd /c "cd /d "%~dp0" && "%PYTHON%" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
  
  rem Wait for the server to become ready
  set "READY=0"
  for /l %%i in (1,1,30) do (
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 (
      set "READY=1"
      goto :server_ready
    )
    ping 127.0.0.1 -n 2 >nul
  )
  echo [ERROR] Backend failed to start. Check Python and dependencies.
  pause
  exit /b 1
)

:server_ready
echo [OK] Backend is running at http://localhost:8000
echo.

rem --- 2. Find cloudflared ---
set "CLOUDFLARED="
where cloudflared >nul 2>&1
if %errorlevel%==0 (
  set "CLOUDFLARED=cloudflared"
) else (
  if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" (
    set "CLOUDFLARED=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
  ) else (
    if exist "%ProgramFiles%\cloudflared\cloudflared.exe" (
      set "CLOUDFLARED=%ProgramFiles%\cloudflared\cloudflared.exe"
    ) else (
      if exist "%LOCALAPPDATA%\cloudflared\cloudflared.exe" (
        set "CLOUDFLARED=%LOCALAPPDATA%\cloudflared\cloudflared.exe"
      ) else (
        echo [ERROR] cloudflared is not installed.
        echo.
        echo Please install it with:
        echo   winget install --id Cloudflare.cloudflared
        echo.
        pause
        exit /b 1
      )
    )
  )
)

rem --- 3. Kill any existing tunnel ---
taskkill /f /im cloudflared.exe >nul 2>&1

rem --- 4. Start the tunnel in its own window ---
echo [..] Creating public tunnel link...
set "TUNNEL_LOG=%TEMP%\clinical_scribe_tunnel.log"
if exist "%TUNNEL_LOG%" del "%TUNNEL_LOG%"

start "ClinicalScribe AI Tunnel" /min cmd /c ""!CLOUDFLARED!" tunnel --url http://127.0.0.1:8000 --no-autoupdate --protocol http2 > "%TUNNEL_LOG%" 2>&1"

rem --- 5. Wait for the tunnel URL ---
set "PUBLIC_URL="
for /l %%i in (1,1,30) do (
  ping 127.0.0.1 -n 2 >nul
  for /f "tokens=*" %%a in ('findstr /C:"https://" "%TUNNEL_LOG%" 2^>nul') do (
    set "LINE=%%a"
    rem Extract the https:// URL from the cloudflared log line
    for /f "tokens=4 delims= " %%b in ("!LINE!") do (
      if "!PUBLIC_URL!"=="" set "PUBLIC_URL=%%b"
    )
  )
  if not "!PUBLIC_URL!"=="" goto :tunnel_ready
)

echo [ERROR] Could not establish tunnel. Check internet connection.
pause
exit /b 1

:tunnel_ready
echo.
echo ============================================================
echo   SCAN THIS LINK ON YOUR PHONE:
echo ============================================================
echo.
echo   !PUBLIC_URL!
echo.
echo ============================================================
echo.
echo   - Open this link on any phone (same Wi-Fi or mobile data)
echo   - The link is temporary and will stop when this window closes
echo   - Keep this window open to keep the link active
echo   - The backend and tunnel run in separate minimized windows
echo.
echo   Press any key to stop the tunnel and close...
pause >nul

rem Clean up - kill the tunnel process
taskkill /f /im cloudflared.exe >nul 2>&1
echo Tunnel stopped. Goodbye!
exit /b 0