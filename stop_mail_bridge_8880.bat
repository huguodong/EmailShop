@echo off
setlocal EnableExtensions

set "TARGET_PORT=8880"

echo Stopping mail bridge on port %TARGET_PORT% ...

powershell -NoProfile -ExecutionPolicy Bypass ^
  "$port=%TARGET_PORT%; " ^
  "$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; " ^
  "if (-not $conns) { Write-Host 'No listening process found on port' $port; exit 0 }; " ^
  "$owningPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "foreach ($procId in $owningPids) { " ^
  "  try { " ^
  "    $proc = Get-Process -Id $procId -ErrorAction Stop; " ^
  "    Stop-Process -Id $procId -Force -ErrorAction Stop; " ^
  "    Write-Host ('Stopped PID={0} NAME={1}' -f $proc.Id, $proc.ProcessName); " ^
  "  } catch { " ^
  "    Write-Host ('Failed to stop PID={0}: {1}' -f $procId, $_.Exception.Message); " ^
  "    exit 1; " ^
  "  } " ^
  "}"

set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo Failed to stop mail bridge. 1>&2
  exit /b %EXIT_CODE%
)

echo Mail bridge stopped.
exit /b 0
