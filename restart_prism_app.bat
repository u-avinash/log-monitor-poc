@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM Prism restart helper for Windows
REM Stops existing UI/Ingestion dev servers started from this repository
REM and starts fresh instances in separate command windows.

set "ROOT_DIR=%~dp0"
set "OTEL_DIR=C:\Users\321404\Softwares\otelcol-contrib_0.153.0_windows_386"
set "OTEL_CONFIG=C:\Users\321404\Softwares\otelcol-contrib_0.153.0_windows_386\otel-collector-config.yaml"
cd /d "%ROOT_DIR%"

echo [1/4] Stopping existing Prism Python processes for ui.server and ingestion.api...
for /f "tokens=2 delims==" %%P in ('wmic process where "(Name='python.exe' or Name='pythonw.exe') and (CommandLine like '%%ingestion.api:app%%' or CommandLine like '%%ui.server:app%%' or CommandLine like '%%ingestion/api.py%%' or CommandLine like '%%ui/server.py%%')" get ProcessId /value 2^>nul ^| findstr /b "ProcessId="') do (
    echo Stopping PID %%P
    taskkill /pid %%P /f >nul 2>&1
)

echo [2/4] Starting OpenTelemetry collector...
start "Prism OpenTelemetry Collector" cmd /k "cd /d "%OTEL_DIR%" && otelcol-contrib.exe --config "%OTEL_CONFIG%""

echo [3/4] Starting ingestion API...
start "Prism Ingestion API" cmd /k "cd /d "%ROOT_DIR%" && python -m uvicorn ingestion.api:app --host 0.0.0.0 --port 8000 --reload"

echo [4/4] Starting UI server...
start "Prism UI" cmd /k "cd /d "%ROOT_DIR%" && python -m uvicorn ui.server:app --host 0.0.0.0 --port 8080 --reload"

echo Prism restart sequence completed.
exit /b 0
