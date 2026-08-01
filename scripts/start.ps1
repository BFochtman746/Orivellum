#Requires -Version 5.1
<#
.SYNOPSIS
  Start Orivellum on Windows -- API server + web UI (optionally + Expo mobile).

.DESCRIPTION
  Equivalent of ./start.sh for Windows.
  Starts the FastAPI backend, waits for it to pass its health check, then
  launches the Vite frontend. Both processes are stopped when you press Ctrl+C.

.PARAMETER Mobile
  Also start the Expo React Native dev server.

.PARAMETER ApiPort
  API server port (default 8080).

.PARAMETER WebPort
  Vite dev-server port (default 5173).

.EXAMPLE
  .\scripts\start.ps1
  .\scripts\start.ps1 -Mobile
  .\scripts\start.ps1 -ApiPort 9000 -WebPort 4000
#>

param(
  [switch]$Mobile,
  [int]$ApiPort = $(if ($env:API_PORT) { [int]$env:API_PORT } else { 8080 }),
  [int]$WebPort = $(if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 5173 })
)

# Strict mode OFF -- makes null/missing property checks much simpler
$ErrorActionPreference = "Stop"

$Cyan  = "Cyan"
$Green = "Green"
$Red   = "Red"
$Gray  = "Gray"

# ---- Refresh PATH from registry so newly-installed tools are visible --------
$machinePath = [Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath    = [Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH    = "$userPath;$machinePath"

Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor $Cyan
Write-Host "  Orivellum -- starting services" -ForegroundColor $Cyan
Write-Host "---------------------------------------" -ForegroundColor $Cyan
Write-Host ""

# ---- Locate executables (handles tools not yet on session PATH) -------------
function Find-Exe {
  param([string]$name, [string[]]$candidates)
  $found = Get-Command $name -ErrorAction SilentlyContinue
  if ($found) { return $found.Source }
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
  }
  return $null
}

$uvExe = Find-Exe "uv" @(
  "$env:USERPROFILE\.local\bin\uv.exe",
  "$env:APPDATA\uv\bin\uv.exe",
  "C:\Program Files\uv\uv.exe"
)

$pnpmExe = Find-Exe "pnpm" @(
  "$env:LOCALAPPDATA\pnpm\pnpm.exe",
  "$env:LOCALAPPDATA\pnpm\pnpm.cmd",
  "C:\Program Files\pnpm\pnpm.exe",
  "C:\Program Files (x86)\pnpm\pnpm.exe"
)

if (-not $uvExe) {
  Write-Host "[err]  uv not found. Run setup-windows.ps1 or restart your terminal." -ForegroundColor $Red
  exit 1
}
if (-not $pnpmExe) {
  Write-Host "[err]  pnpm not found. Run setup-windows.ps1 or restart your terminal." -ForegroundColor $Red
  exit 1
}

# ---- Kill any leftover processes on our ports ------------------------------
function Clear-Port {
  param([int]$port)
  try {
    # netstat works without admin rights unlike Get-NetTCPConnection
    $lines = netstat -ano 2>$null | Select-String ":$port\s"
    foreach ($line in $lines) {
      if ($line -match 'LISTENING') {
        $owningPid = ($line.ToString().Trim() -split '\s+')[-1]
        if ($owningPid -match '^\d+$' -and [int]$owningPid -gt 0) {
          taskkill /PID $owningPid /F 2>$null | Out-Null
        }
      }
    }
  } catch {}
}
Clear-Port $ApiPort
Clear-Port $WebPort

# ---- Ensure log dir exists --------------------------------------------------
$root = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { Get-Location }
$logsDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# ---- Track child processes --------------------------------------------------
$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Stop-All {
  foreach ($p in $children) {
    if ($p -ne $null -and -not $p.HasExited) {
      Write-Host "  Stopping PID $($p.Id) ..." -ForegroundColor $Gray
      try { $p.Kill($true) } catch {}
    }
  }
}

# Launch an executable (or .cmd/.bat wrapper) via a temp batch file so that
# paths with spaces and shim wrappers work reliably with Start-Process.
function Start-Via-Batch {
  param(
    [string]$Exe,
    [string]$CmdArgs,
    [string]$WorkDir,
    [string]$OutLog,
    [string]$ErrLog
  )
  $tmp = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.cmd'
  $batch = "@echo off`r`ncd /d `"$WorkDir`"`r`n`"$Exe`" $CmdArgs`r`n"
  [System.IO.File]::WriteAllText($tmp, $batch, [System.Text.Encoding]::ASCII)
  return Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c `"$tmp`"" `
    -PassThru -NoNewWindow `
    -WorkingDirectory $WorkDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog
}

# ---- API server -------------------------------------------------------------
Write-Host "[api]  Starting API server on port $ApiPort ..." -ForegroundColor $Cyan
$env:PORT = "$ApiPort"
$apiProc = Start-Process -FilePath $uvExe `
  -ArgumentList "run python -m orivellum.api.main" `
  -PassThru -NoNewWindow `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $logsDir "api.log") `
  -RedirectStandardError  (Join-Path $logsDir "api-err.log")
$children.Add($apiProc)

# ---- Wait for health check --------------------------------------------------
Write-Host "[api]  Waiting for API to be ready ..." -ForegroundColor $Cyan
$maxWait = 60
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWait) {
  if ($apiProc.HasExited) {
    Write-Host "[api]  ERROR: API exited unexpectedly. Check logs\api-err.log" -ForegroundColor $Red
    Stop-All; exit 1
  }
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/healthz" `
      -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $healthy = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
  $elapsed++
}
if (-not $healthy) {
  Write-Host "[api]  ERROR: API not healthy after ${maxWait}s. Check logs\api-err.log" -ForegroundColor $Red
  Stop-All; exit 1
}
Write-Host "[api]  Ready [OK]" -ForegroundColor $Green

# ---- Web UI -----------------------------------------------------------------
Write-Host "[web]  Starting web UI on port $WebPort ..." -ForegroundColor $Cyan
$env:PORT              = "$WebPort"
$env:BASE_PATH         = "/"
$env:ORIVELLUM_API_URL = "http://127.0.0.1:$ApiPort"
# Use cmd.exe /c so shims, .cmd wrappers, and .exe files all work
$uiDir  = Join-Path $root "artifacts\orivellum-ui"
$webTmp = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.cmd'
$webBatch  = "@echo off`r`n"
$webBatch += "echo [web-batch] Starting pnpm run dev ...`r`n"
$webBatch += "cd /d `"$uiDir`"`r`n"
$webBatch += "echo [web-batch] cwd=%CD%`r`n"
$webBatch += "`"$pnpmExe`" run dev`r`n"
$webBatch += "echo [web-batch] pnpm exited with %ERRORLEVEL%`r`n"
[System.IO.File]::WriteAllText($webTmp, $webBatch, [System.Text.Encoding]::ASCII)

$webProc = Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c `"$webTmp`"" `
  -PassThru -NoNewWindow `
  -WorkingDirectory $uiDir `
  -RedirectStandardOutput (Join-Path $logsDir "web.log") `
  -RedirectStandardError  (Join-Path $logsDir "web-err.log")
$children.Add($webProc)

# ---- Mobile (optional) ------------------------------------------------------
if ($Mobile) {
  Write-Host "[mob]  Starting Expo ..." -ForegroundColor $Cyan
  $mobTmp = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.cmd'
  [System.IO.File]::WriteAllText($mobTmp, "@echo off`r`ncd /d `"$root`"`r`n`"$pnpmExe`" --filter @workspace/mobile run dev`r`n", [System.Text.Encoding]::ASCII)
  $mobProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$mobTmp`"" -PassThru -NoNewWindow -WorkingDirectory $root
  $children.Add($mobProc)
}

Write-Host ""
Write-Host "  API  -> http://localhost:$ApiPort" -ForegroundColor White
Write-Host "  Web  -> http://localhost:$WebPort" -ForegroundColor White
if ($Mobile) { Write-Host "  Expo -> http://localhost:19000" -ForegroundColor White }
Write-Host ""
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor $Gray
Write-Host "---------------------------------------" -ForegroundColor $Cyan

# ---- Keep alive; clean up on Ctrl+C ----------------------------------------
try {
  while ($true) {
    foreach ($p in $children) {
      if ($p -ne $null -and $p.HasExited) {
        Write-Host "  A service stopped unexpectedly (PID $($p.Id)). Shutting down." -ForegroundColor $Red
        Stop-All; exit 1
      }
    }
    Start-Sleep -Seconds 2
  }
} finally {
  Write-Host ""
  Write-Host "  Stopping all services ..." -ForegroundColor $Gray
  Stop-All
}
