@echo off
setlocal EnableExtensions

set "SERVICE_ROOT=%~dp0"
if "%SERVICE_ROOT:~-1%"=="\" set "SERVICE_ROOT=%SERVICE_ROOT:~0,-1%"
for %%I in ("%SERVICE_ROOT%\..") do set "PROJECT_ROOT=%%~fI"
set "CONFIG_PATH=%SERVICE_ROOT%\config.json"
if not exist "%CONFIG_PATH%" set "CONFIG_PATH=%PROJECT_ROOT%\config.json"
set "LOG_DIR=%SERVICE_ROOT%\logs"
if not exist "%SERVICE_ROOT%\config.json" set "LOG_DIR=%PROJECT_ROOT%\logs"
set "SERVER_SCRIPT=%SERVICE_ROOT%\mail_bridge_server.py"
set "PYTHON_EXE="

if defined PYTHON_EXE_OVERRIDE if exist "%PYTHON_EXE_OVERRIDE%" set "PYTHON_EXE=%PYTHON_EXE_OVERRIDE%"
if not defined PYTHON_EXE if exist "%SERVICE_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%SERVICE_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where.exe python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"

if not exist "%PYTHON_EXE%" (
  echo Missing Python interpreter. Checked: 1>&2
  echo   1. PYTHON_EXE_OVERRIDE 1>&2
  echo   2. %SERVICE_ROOT%\.venv\Scripts\python.exe 1>&2
  echo   3. %PROJECT_ROOT%\.venv\Scripts\python.exe 1>&2
  echo   4. python from PATH 1>&2
  exit /b 1
)

if not exist "%SERVER_SCRIPT%" (
  echo Missing mail bridge server: %SERVER_SCRIPT% 1>&2
  exit /b 1
)

if not exist "%CONFIG_PATH%" (
  echo Missing config file: %CONFIG_PATH% 1>&2
  exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

echo Starting mail bridge on 0.0.0.0:8880
echo Python: %PYTHON_EXE%
echo Config: %CONFIG_PATH%
echo Logs:   %LOG_DIR%
echo.

"%PYTHON_EXE%" "%SERVER_SCRIPT%" --host 0.0.0.0 --port 8880 --config "%CONFIG_PATH%" --log-dir "%LOG_DIR%"

exit /b %errorlevel%
