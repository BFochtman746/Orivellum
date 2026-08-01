#Requires -Version 5.1
<#
.SYNOPSIS
  Start Orivellum on Windows — API server + web UI (optionally + Expo mobile).

.DESCRIPTION
  Equivalent of ./start.sh for Windows.
  Starts the FastAPI backend, waits for it to pass its health check, then
  launches the Vite frontend. Both processes are stopped when you close the
  terminal or press Ctrl+C.

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
  [int]$ApiPort = $env:API_PORT ?? 8080,
  [int]$WebPort = $env:WEB_PORT ?? 5173
)

Set-StrictMode -Version Latest

$Cyan  = "Cyan"
$Green = "Green"
$Red   = "Red"

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor $Cyan
Write-Host "  Orivellum — starting services" -ForegroundColor $Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor $Cyan
Write-Host ""

# Track child processes for clean teardown
$children = @()

function Stop-All {
  foreach ($p in $children) {
    if ($p -and !$p.HasExited) {
      Write-Host "  Stopping PID $($p.Id) …" -ForegroundColor Gray
      try { $p.Kill($true) } catch {}
    }
  }
}

# ── API server ────────────────────────────────────────────────────────────────
Write-Host "[api]  Starting API server on port $ApiPort …" -ForegroundColor $Cyan
$apiEnv  = @{ PORT = "$ApiPort" }
$apiProc = Start-Process -FilePath "uv" `
  -ArgumentList "run python -m orivellum.api.main" `
  -PassThru -NoNewWindow `
  -RedirectStandardOutput "logs\api.log" `
  -RedirectStandardError  "logs\api-err.log"
$children += $apiProc

# Ensure log dir exists
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# ── wait for health check ─────────────────────────────────────────────────────
Write-Host "[api]  Waiting for API to be ready …" -ForegroundColor $Cyan
$maxWait = 30
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWait) {
  if ($apiProc.HasExited) {
    Write-Host "[api]  ERROR: API process exited unexpectedly. Check logs\api-err.log" -ForegroundColor $Red
    Stop-All; exit 1
  }
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/healthz" `
      -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $healthy = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
  $elapsed++
}
if (!$healthy) {
  Write-Host "[api]  ERROR: API did not become healthy within ${maxWait}s." -ForegroundColor $Red
  Stop-All; exit 1
}
Write-Host "[api]  Ready ✓" -ForegroundColor $Green

# ── web UI ────────────────────────────────────────────────────────────────────
Write-Host "[web]  Starting web UI on port $WebPort …" -ForegroundColor $Cyan
$env:PORT               = "$WebPort"
$env:ORIVELLUM_API_URL  = "http://127.0.0.1:$ApiPort"
$webProc = Start-Process -FilePath "pnpm" `
  -ArgumentList "--filter @workspace/orivellum-ui run dev" `
  -PassThru -NoNewWindow `
  -RedirectStandardOutput "logs\web.log" `
  -RedirectStandardError  "logs\web-err.log"
$children += $webProc

# ── mobile (optional) ─────────────────────────────────────────────────────────
if ($Mobile) {
  Write-Host "[mob]  Starting Expo …" -ForegroundColor $Cyan
  $mobProc = Start-Process -FilePath "pnpm" `
    -ArgumentList "--filter @workspace/mobile run dev" `
    -PassThru -NoNewWindow
  $children += $mobProc
}

Write-Host ""
Write-Host "  API  → http://localhost:$ApiPort" -ForegroundColor White
Write-Host "  Web  → http://localhost:$WebPort" -ForegroundColor White
if ($Mobile) { Write-Host "  Expo → http://localhost:19000" -ForegroundColor White }
Write-Host ""
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor Gray
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor $Cyan

# Keep running; clean up on Ctrl+C
try {
  while ($true) {
    foreach ($p in $children) {
      if ($p.HasExited) {
        Write-Host "  A service exited unexpectedly (PID $($p.Id)). Shutting down." -ForegroundColor $Red
        Stop-All; exit 1
      }
    }
    Start-Sleep -Seconds 2
  }
} finally {
  Write-Host ""
  Write-Host "  Stopping all services…" -ForegroundColor Gray
  Stop-All
}
