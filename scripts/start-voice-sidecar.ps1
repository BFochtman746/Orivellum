#Requires -Version 5.1
<#
.SYNOPSIS
  Start the Orivellum Premium TTS sidecar (Chatterbox) on Windows.

.DESCRIPTION
  Runs the loopback-only neural narration engine on 127.0.0.1:9883.
  Uses a dedicated virtual environment (.venv-tts) so the heavy
  PyTorch/Chatterbox stack never touches the main Orivellum environment.

  1. First time: .\scripts\start-voice-sidecar.ps1 -Setup
     For AMD GPU (ROCm) speed on Strix Halo, pass the ROCm wheel index:
     .\scripts\start-voice-sidecar.ps1 -Setup -TorchIndexUrl https://download.pytorch.org/whl/rocm6.4
  2. Then: .\scripts\start-voice-sidecar.ps1
  3. In config.yaml set tts_premium_url: "http://127.0.0.1:9883"
     and tts_premium_ack_license: true, then restart Orivellum.

.PARAMETER Setup
  Create .venv-tts and install dependencies (torch, chatterbox-tts,
  fastapi, uvicorn, python-multipart). Safe to re-run.

.PARAMETER TorchIndexUrl
  Optional pip index URL for the torch install (e.g. the ROCm wheel index).
  Without it, the default (CPU) torch is installed - slow but functional.

.PARAMETER Port
  Sidecar port (default 9883). Always binds 127.0.0.1 only.

.EXAMPLE
  .\scripts\start-voice-sidecar.ps1 -Setup
  .\scripts\start-voice-sidecar.ps1
#>

param(
  [switch]$Setup,
  [string]$TorchIndexUrl = "",
  [int]$Port = $(if ($env:PREMIUM_TTS_PORT) { [int]$env:PREMIUM_TTS_PORT } else { 9883 })
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

$root    = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { Get-Location }
$venvDir = Join-Path $root ".venv-tts"
$venvPy  = Join-Path $venvDir "Scripts\python.exe"

Write-Host ""
Write-Host "-----------------------------------------------" -ForegroundColor $Cyan
Write-Host "  Orivellum Premium TTS sidecar (Chatterbox)" -ForegroundColor $Cyan
Write-Host "-----------------------------------------------" -ForegroundColor $Cyan
Write-Host ""

# ---- Locate a Python interpreter for venv creation --------------------------
function Find-Python {
  foreach ($name in @("python", "python3", "py")) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
  }
  return $null
}

# ---- Setup mode --------------------------------------------------------------
if ($Setup) {
  $py = Find-Python
  if (-not $py) {
    Write-Host "[err]  Python not found on PATH. Install Python 3.11+ first." -ForegroundColor $Red
    exit 1
  }
  if (-not (Test-Path $venvPy)) {
    Write-Host "[venv] Creating $venvDir ..." -ForegroundColor $Cyan
    & $py -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
      Write-Host "[err]  venv creation failed." -ForegroundColor $Red
      exit 1
    }
  } else {
    Write-Host "[venv] Reusing existing $venvDir" -ForegroundColor $Gray
  }

  Write-Host "[pip]  Upgrading pip ..." -ForegroundColor $Cyan
  & $venvPy -m pip install --upgrade pip --quiet

  if ($TorchIndexUrl) {
    Write-Host "[pip]  Installing torch + torchaudio from $TorchIndexUrl ..." -ForegroundColor $Cyan
    & $venvPy -m pip install torch torchaudio --index-url $TorchIndexUrl
  } else {
    Write-Host "[pip]  Installing torch + torchaudio (default index - CPU build) ..." -ForegroundColor $Cyan
    Write-Host "       Tip: pass -TorchIndexUrl for the ROCm GPU build." -ForegroundColor $Gray
    & $venvPy -m pip install torch torchaudio
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[err]  torch install failed." -ForegroundColor $Red
    exit 1
  }

  Write-Host "[pip]  Installing chatterbox-tts + server deps ..." -ForegroundColor $Cyan
  & $venvPy -m pip install chatterbox-tts fastapi "uvicorn[standard]" python-multipart
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[err]  dependency install failed." -ForegroundColor $Red
    exit 1
  }

  Write-Host ""
  Write-Host "[ok]   Setup complete. Start the sidecar with:" -ForegroundColor $Green
  Write-Host "       .\scripts\start-voice-sidecar.ps1" -ForegroundColor $Gray
  exit 0
}

# ---- Run mode ----------------------------------------------------------------
if (-not (Test-Path $venvPy)) {
  Write-Host "[err]  .venv-tts not found. Run setup first:" -ForegroundColor $Red
  Write-Host "       .\scripts\start-voice-sidecar.ps1 -Setup" -ForegroundColor $Gray
  exit 1
}

# ffmpeg is required for MP3 encoding (already installed by setup-windows.ps1).
$ffmpeg = Get-Command "ffmpeg" -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  Write-Host "[warn] ffmpeg not found on PATH - synthesis will fail until it is installed." -ForegroundColor $Red
}

# Free the port if a PREVIOUS SIDECAR run is stuck. Only python processes are
# killed - if something else owns the port, we warn and let bind fail loudly.
try {
  $lines = netstat -ano 2>$null | Select-String ":$Port\s"
  foreach ($line in $lines) {
    if ($line -match 'LISTENING') {
      $owningPid = ($line.ToString().Trim() -split '\s+')[-1]
      if ($owningPid -match '^\d+$' -and [int]$owningPid -gt 0) {
        $owner = Get-Process -Id ([int]$owningPid) -ErrorAction SilentlyContinue
        if ($owner -and $owner.ProcessName -match '^python') {
          Write-Host "[port] Stopping stale sidecar (PID $owningPid) ..." -ForegroundColor $Gray
          taskkill /PID $owningPid /F 2>$null | Out-Null
        } elseif ($owner) {
          Write-Host "[warn] Port $Port is in use by '$($owner.ProcessName)' (PID $owningPid) - not killing it." -ForegroundColor $Red
          Write-Host "       Pick another port with -Port or stop that process manually." -ForegroundColor $Gray
        }
      }
    }
  }
} catch {}

$env:PREMIUM_TTS_PORT = "$Port"

Write-Host "[run]  Starting sidecar on http://127.0.0.1:$Port (loopback only)" -ForegroundColor $Cyan
Write-Host "       First synthesis downloads model weights (~2 GB) - be patient." -ForegroundColor $Gray
Write-Host "       Stop with Ctrl+C." -ForegroundColor $Gray
Write-Host ""

Set-Location $root
& $venvPy -m sidecars.premium_tts.server
