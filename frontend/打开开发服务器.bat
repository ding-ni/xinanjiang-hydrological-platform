@echo off
setlocal
set "APPDIR=%~dp0"
set "URL=http://127.0.0.1:5173/"

if not exist "%APPDIR%\package.json" (
  echo [ERROR] package.json was not found.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  echo https://nodejs.org/
  pause
  exit /b 1
)

call :port_ready
if not errorlevel 1 goto open_browser

if not exist "%APPDIR%\node_modules\" (
  echo First run: installing frontend dependencies...
  pushd "%APPDIR%"
  call npm install
  if errorlevel 1 (
    popd
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
  )
  popd
)

echo Starting the hydrological forecasting platform...
start "XAJ Hydrological Forecast Service" /min /D "%APPDIR%" cmd /k "npm run dev -- --host 127.0.0.1 --port 5173"

set /a WAIT_COUNT=0
:wait_ready
set /a WAIT_COUNT+=1
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%';if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){exit 0};exit 1}catch{exit 1}" >nul 2>nul
if not errorlevel 1 goto open_browser
if %WAIT_COUNT% geq 30 goto start_failed
timeout /t 1 /nobreak >nul
goto wait_ready

:open_browser
echo Platform ready: %URL%
start "" "%URL%"
exit /b 0

:start_failed
echo [ERROR] Startup timed out. Check the service window for details.
pause
exit /b 1

:port_ready
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient;try{$c.Connect('127.0.0.1',5173);exit 0}catch{exit 1}finally{$c.Dispose()}" >nul 2>nul
exit /b %ERRORLEVEL%
