#Requires -Version 5.1
<#
.SYNOPSIS
  Start Orivellum on Windows  -  builds the UI then starts the API (production mode).

.DESCRIPTION
  Equivalent of ./start.sh for Windows  -  production/headless mode.
  1. Builds the Vite UI bundle (pnpm build)  -  skipped with -SkipBuild.
  2. Starts the FastAPI backend, which serves BOTH the API (/api/*) and the
     built UI (/orivellum-ui/*) from a single process.
  No Vite dev server is launched. After boot, open:
    http://<host>:<ApiPort>/orivellum-ui/   in Safari -> Add to Home Screen.

.PARAMETER SkipBuild
  Skip the UI build step (use existing dist/public). Useful for fast restarts
  when the UI source hasn't changed.

.PARAMETER Mobile
  Also start the Expo React Native dev server (still needs pnpm).

.PARAMETER ApiPort
  API server port (default 8080).

.EXAMPLE
  .\scripts\start.ps1
  .\scripts\start.ps1 -SkipBuild
  .\scripts\start.ps1 -Mobile
  .\scripts\start.ps1 -ApiPort 9000
#>

param(
  [switch]$SkipBuild,
  [switch]$Mobile,
  [int]$ApiPort = $(if ($env:API_PORT) { [int]$env:API_PORT } else { 8080 })
)

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
Write-Host "  Orivellum  -  starting (production)" -ForegroundColor $Cyan
Write-Host "---------------------------------------" -ForegroundColor $Cyan
Write-Host ""

# ---- Locate executables ----------------------------------------------------
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

# ---- Kill any leftover process on our API port -----------------------------
function Clear-Port {
  param([int]$port)
  try {
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

# ---- Ensure log dir exists -------------------------------------------------
$root = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { Get-Location }
$logsDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# ---- Track child processes -------------------------------------------------
$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Stop-All {
  foreach ($p in $children) {
    if ($p -ne $null -and -not $p.HasExited) {
      Write-Host "  Stopping PID $($p.Id) ..." -ForegroundColor $Gray
      try { $p.Kill($true) } catch {}
    }
  }
  Clear-Port $ApiPort
}

# ---- Step 1: Build the UI --------------------------------------------------
$uiDir   = Join-Path $root "artifacts\orivellum-ui"
$uiDist  = Join-Path $uiDir "dist\public"

if ($SkipBuild -and (Test-Path $uiDist)) {
  Write-Host "[ui]   Skipping build (dist/public already exists, -SkipBuild set)" -ForegroundColor $Gray
} else {
  Write-Host "[ui]   Building production UI bundle ..." -ForegroundColor $Cyan
  $buildLog = Join-Path $logsDir "ui-build.log"
  # pnpm build runs predev (generate-build-info) then vite build
  $buildProc = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-Command", "& '$pnpmExe' --filter '@workspace/orivellum-ui' build" `
    -WorkingDirectory $root `
    -PassThru -NoNewWindow `
    -RedirectStandardOutput $buildLog `
    -RedirectStandardError  (Join-Path $logsDir "ui-build-err.log") `
    -Wait

  if ($buildProc.ExitCode -ne 0) {
    Write-Host "[ui]   ERROR: UI build failed (exit $($buildProc.ExitCode)). See logs\ui-build.log" -ForegroundColor $Red
    exit 1
  }

  if (-not (Test-Path (Join-Path $uiDist "sw.js"))) {
    Write-Host "[ui]   ERROR: Build succeeded but sw.js not found in dist/public." -ForegroundColor $Red
    exit 1
  }
  Write-Host "[ui]   Build complete [OK]" -ForegroundColor $Green
}

# ---- Lemonade Server (AMD Ryzen AI Max+ 395) --------------------------------
# Lemonade Server manages NPU / iGPU acceleration automatically  -  no manual
# ROCm / HSA environment variables are needed.  Just ensure Lemonade is running
# before starting Orivellum; it starts automatically on login after install.
#
# Install guide : scripts\windows\lemonade-setup.md
# Lemonade docs : https://lemonade-server.ai
#
# Verify Lemonade is up:
#   Invoke-WebRequest http://127.0.0.1:13305/api/v1/models -UseBasicParsing
Write-Host "[lmnd] Lemonade Server should be running on http://127.0.0.1:13305" -ForegroundColor DarkCyan

# ---- Step 2: Start API (serves both /api/* and /orivellum-ui/*) -----------
Write-Host "[api]  Starting API server on port $ApiPort ..." -ForegroundColor $Cyan
$env:PORT = "$ApiPort"
$apiProc = Start-Process -FilePath $uvExe `
  -ArgumentList "run python -m orivellum.api.main" `
  -PassThru -NoNewWindow `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $logsDir "api.log") `
  -RedirectStandardError  (Join-Path $logsDir "api-err.log")
$children.Add($apiProc)

# ---- Step 3: Wait for health check -----------------------------------------
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

# ---- Step 4: Mobile (optional) ---------------------------------------------
$mobileHost = $null
if ($Mobile) {
  Write-Host "[mob]  Starting Expo ..." -ForegroundColor $Cyan
  $mobDir = Join-Path $root "artifacts\mobile"

  # Pick the address the phone should connect to: prefer the Tailscale IP
  # (reachable from anywhere), else the LAN IP (same-WiFi only).
  try {
    $mobileHost = (& tailscale ip -4 2>$null | Select-Object -First 1)
    if ($mobileHost) { $mobileHost = $mobileHost.Trim() }
  } catch { $mobileHost = $null }
  if (-not $mobileHost) {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
      Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
      Select-Object -First 1 -ExpandProperty IPAddress
    $mobileHost = $lanIp
  }

  # Expo advertises this hostname to the phone (inherited by the child window).
  if ($mobileHost) { $env:REACT_NATIVE_PACKAGER_HOSTNAME = $mobileHost }

  # dev:win avoids the POSIX-style env prefixes in the default dev script,
  # which do not work on Windows, and binds to the LAN instead of localhost.
  $mobProc = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-Command", "& '$pnpmExe' run dev:win" `
    -WorkingDirectory $mobDir `
    -PassThru
  $children.Add($mobProc)
}

Write-Host ""
Write-Host "  App  -> http://localhost:$ApiPort/orivellum-ui/" -ForegroundColor White
Write-Host "         Open in Safari on your iPhone and tap Share -> Add to Home Screen" -ForegroundColor $Gray
Write-Host "  API  -> http://localhost:$ApiPort/api/" -ForegroundColor White
if ($Mobile) {
  if ($mobileHost) {
    Write-Host "  Expo -> exp://${mobileHost}:19000" -ForegroundColor White
    Write-Host "         On your phone: install 'Expo Go', open it, and enter the exp:// address above." -ForegroundColor $Gray
    Write-Host "         In the app, set Server address to http://${mobileHost}:$ApiPort and enter your API key." -ForegroundColor $Gray
  } else {
    Write-Host "  Expo -> http://localhost:19000 (no LAN/Tailscale IP found - phone may not reach it)" -ForegroundColor White
  }
}
Write-Host ""
Write-Host "  Use -SkipBuild to restart without rebuilding the UI." -ForegroundColor $Gray
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor $Gray
Write-Host "---------------------------------------" -ForegroundColor $Cyan

# ---- Keep alive  -  monitor API process only ---------------------------------
try {
  while ($true) {
    if ($apiProc.HasExited) {
      Write-Host "  API stopped unexpectedly. Shutting down." -ForegroundColor $Red
      Stop-All; exit 1
    }
    Start-Sleep -Seconds 5
  }
} finally {
  Write-Host ""
  Write-Host "  Stopping all services ..." -ForegroundColor $Gray
  Stop-All
}
