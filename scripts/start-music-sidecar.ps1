#Requires -Version 5.1
<#
.SYNOPSIS
  Start the Orivellum Music & SFX generation sidecar on Windows.

.DESCRIPTION
  Runs the loopback-only music generation engine on 127.0.0.1:9884.
  Uses a dedicated virtual environment (.venv-music) so the heavy
  PyTorch/diffusers stack never touches the main Orivellum environment.

  1. First time: .\scripts\start-music-sidecar.ps1 -Setup
     For AMD GPU (ROCm) speed on Strix Halo, pass the ROCm wheel index:
     .\scripts\start-music-sidecar.ps1 -Setup -TorchIndexUrl https://download.pytorch.org/whl/rocm6.4
  2. Then: .\scripts\start-music-sidecar.ps1
  3. In config.yaml set music_gen_url: "http://127.0.0.1:9884",
     then restart Orivellum.

  Stable Audio Open weights are gated on Hugging Face - accept the license
  at https://huggingface.co/stabilityai/stable-audio-open-1.0 and run
  `huggingface-cli login` (inside .venv-music) before first use.
  MusicGen weights download automatically (no gate) but are CC-BY-NC -
  non-commercial use only; the Orivellum UI asks you to acknowledge this.

.PARAMETER Setup
  Create .venv-music and install dependencies (torch, transformers,
  diffusers, soundfile, fastapi, uvicorn). Safe to re-run.

.PARAMETER TorchIndexUrl
  Optional pip index URL for the torch install (e.g. the ROCm wheel index).
  Without it, the default (CPU) torch is installed - slow but functional.

.PARAMETER Port
  Sidecar port (default 9884). Always binds 127.0.0.1 only.

.EXAMPLE
  .\scripts\start-music-sidecar.ps1 -Setup
  .\scripts\start-music-sidecar.ps1
#>

param(
  [switch]$Setup,
  [string]$TorchIndexUrl = "",
  [int]$Port = $(if ($env:MUSIC_GEN_PORT) { [int]$env:MUSIC_GEN_PORT } else { 9884 })
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
$venvDir = Join-Path $root ".venv-music"
$venvPy  = Join-Path $venvDir "Scripts\python.exe"

Write-Host ""
Write-Host "-----------------------------------------------" -ForegroundColor $Cyan
Write-Host "  Orivellum Music & SFX sidecar" -ForegroundColor $Cyan
Write-Host "-----------------------------------------------" -ForegroundColor $Cyan
Write-Host ""

if ($Setup) {
  # ---- Locate a Python 3.10+ interpreter ------------------------------------
  $basePy = $null
  foreach ($cand in @("python", "python3", "py")) {
    try {
      $v = & $cand --version 2>&1
      if ($v -match "Python 3\.(1[0-9]|[2-9][0-9])") { $basePy = $cand; break }
    } catch {}
  }
  if (-not $basePy) {
    Write-Host "  ERROR: Python 3.10+ not found on PATH." -ForegroundColor $Red
    Write-Host "  Install from https://www.python.org/downloads/ and re-run." -ForegroundColor $Gray
    exit 1
  }

  if (-not (Test-Path $venvPy)) {
    Write-Host "  Creating virtual environment at $venvDir ..." -ForegroundColor $Gray
    & $basePy -m venv $venvDir
  }

  Write-Host "  Upgrading pip ..." -ForegroundColor $Gray
  & $venvPy -m pip install --upgrade pip --quiet

  if ($TorchIndexUrl) {
    Write-Host "  Installing torch from $TorchIndexUrl ..." -ForegroundColor $Gray
    & $venvPy -m pip install torch torchaudio --index-url $TorchIndexUrl
  } else {
    Write-Host "  Installing torch (default/CPU wheels) ..." -ForegroundColor $Gray
    & $venvPy -m pip install torch torchaudio
  }

  Write-Host "  Installing generation stack (transformers, diffusers, soundfile, fastapi) ..." -ForegroundColor $Gray
  & $venvPy -m pip install transformers diffusers accelerate soundfile scipy `
      fastapi "uvicorn[standard]" "huggingface_hub[cli]"

  Write-Host ""
  Write-Host "  Setup complete." -ForegroundColor $Green
  Write-Host "  For Stable Audio Open: accept the license at" -ForegroundColor $Gray
  Write-Host "    https://huggingface.co/stabilityai/stable-audio-open-1.0" -ForegroundColor $Gray
  Write-Host "  then run:  $venvDir\Scripts\huggingface-cli.exe login" -ForegroundColor $Gray
  Write-Host "  Start the sidecar with:  .\scripts\start-music-sidecar.ps1" -ForegroundColor $Gray
  Write-Host ""
  exit 0
}

if (-not (Test-Path $venvPy)) {
  Write-Host "  ERROR: .venv-music not found. Run setup first:" -ForegroundColor $Red
  Write-Host "    .\scripts\start-music-sidecar.ps1 -Setup" -ForegroundColor $Gray
  exit 1
}

$env:MUSIC_GEN_PORT = "$Port"
Write-Host "  Starting on http://127.0.0.1:$Port (loopback only) ..." -ForegroundColor $Green
Write-Host "  First generation downloads model weights - be patient." -ForegroundColor $Gray
Write-Host ""

Set-Location $root
& $venvPy -m sidecars.music_gen.server
