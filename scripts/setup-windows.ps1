#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot Windows setup for Orivellum.
  Downloads and installs all required system tools, then installs Python and Node dependencies.

.DESCRIPTION
  Run this script once before starting Orivellum for the first time on Windows.
  It checks for (and optionally installs) every prerequisite:
    - Python 3.12+
    - Node.js 20+
    - pnpm 9+
    - uv (Python package/env manager)
    - Tesseract OCR  (PDF/image text extraction)
    - Poppler         (PDF rendering)
    - FFmpeg          (audio conversion for TTS)
    - espeak-ng       (text-to-speech synthesis)

  Requires an internet connection. Some steps use winget; others download
  installers directly from official release pages.

.NOTES
  Run as a normal user -- no administrator rights required except where noted.
  If a tool is already installed and on PATH it is skipped automatically.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Green  = "Green"
$Yellow = "Yellow"
$Cyan   = "Cyan"
$Red    = "Red"

function Write-Step { param($msg) Write-Host "  >> $msg" -ForegroundColor $Cyan }
function Write-Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor $Green }
function Write-Warn { param($msg) Write-Host "  [!]  $msg" -ForegroundColor $Yellow }
function Write-Fail { param($msg) Write-Host "  [X]  $msg" -ForegroundColor $Red }

Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host "  Orivellum -- Windows Setup" -ForegroundColor Cyan
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host ""

# -- helpers ------------------------------------------------------------------

function Test-CommandExists { param($name) return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-Version {
  param($cmd, $arg = "--version")
  try { (& $cmd $arg 2>&1) | Select-Object -First 1 } catch { "" }
}

function Add-ToUserPath {
  param([string]$dir)
  $cur = [Environment]::GetEnvironmentVariable("PATH", "User")
  if ($cur -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$dir;$cur", "User")
    $env:PATH = "$dir;$env:PATH"
    Write-Ok "Added $dir to user PATH"
  }
}

function Download-File {
  param([string]$url, [string]$dest)
  Write-Step "Downloading $(Split-Path $dest -Leaf) ..."
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

# -- Python -------------------------------------------------------------------

Write-Host "Checking Python ..." -ForegroundColor $Yellow
if (Test-CommandExists python) {
  $pyver = python --version 2>&1
  Write-Ok "Python found: $pyver"
} elseif (Test-CommandExists winget) {
  Write-Step "Installing Python 3.12 via winget ..."
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  Write-Ok "Python installed -- restart your terminal to pick up the new PATH"
} else {
  Write-Warn "winget not available. Download Python 3.12+ from https://www.python.org/downloads/"
}

# -- Node.js ------------------------------------------------------------------

Write-Host ""
Write-Host "Checking Node.js ..." -ForegroundColor $Yellow
if (Test-CommandExists node) {
  Write-Ok "Node found: $(Get-Version node)"
} elseif (Test-CommandExists winget) {
  Write-Step "Installing Node.js 20 LTS via winget ..."
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  Write-Ok "Node.js installed"
} else {
  Write-Warn "Download Node.js 20+ from https://nodejs.org/"
}

# -- pnpm ---------------------------------------------------------------------

Write-Host ""
Write-Host "Checking pnpm ..." -ForegroundColor $Yellow
if (Test-CommandExists pnpm) {
  Write-Ok "pnpm found: $(Get-Version pnpm)"
} else {
  Write-Step "Installing pnpm via standalone installer ..."
  $pnpmInstall = "$env:TEMP\pnpm-install.ps1"
  (New-Object System.Net.WebClient).DownloadFile("https://get.pnpm.io/install.ps1", $pnpmInstall)
  powershell -ExecutionPolicy Bypass -File $pnpmInstall
  $pnpmBin = "$env:LOCALAPPDATA\pnpm"
  Add-ToUserPath $pnpmBin
  Write-Ok "pnpm installed"
}

# -- uv -----------------------------------------------------------------------

Write-Host ""
Write-Host "Checking uv ..." -ForegroundColor $Yellow
if (Test-CommandExists uv) {
  Write-Ok "uv found: $(Get-Version uv)"
} else {
  Write-Step "Installing uv ..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $uvBin = "$env:USERPROFILE\.local\bin"
  Add-ToUserPath $uvBin
  Write-Ok "uv installed"
}

# -- Tesseract OCR ------------------------------------------------------------

Write-Host ""
Write-Host "Checking Tesseract OCR ..." -ForegroundColor $Yellow
$tessPath = "C:\Program Files\Tesseract-OCR\tesseract.exe"
if ((Test-CommandExists tesseract) -or (Test-Path $tessPath)) {
  Write-Ok "Tesseract found"
} else {
  Write-Step "Downloading Tesseract 5.x installer ..."
  $tessInstaller = "$env:TEMP\tesseract-installer.exe"
  $tessUrl = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe"
  Download-File $tessUrl $tessInstaller
  Write-Step "Running Tesseract installer (may require UAC elevation) ..."
  Start-Process -FilePath $tessInstaller -ArgumentList "/SILENT /NORESTART" -Wait
  Add-ToUserPath "C:\Program Files\Tesseract-OCR"
  Write-Ok "Tesseract installed to C:\Program Files\Tesseract-OCR"
}

# -- Poppler ------------------------------------------------------------------

Write-Host ""
Write-Host "Checking Poppler ..." -ForegroundColor $Yellow
if (Test-CommandExists pdfinfo) {
  Write-Ok "Poppler found: $(Get-Version pdfinfo)"
} else {
  Write-Step "Downloading Poppler for Windows ..."
  $popplerZip  = "$env:TEMP\poppler-windows.zip"
  $popplerDest = "$env:LOCALAPPDATA\poppler"
  $popplerUrl  = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
  Download-File $popplerUrl $popplerZip
  Write-Step "Extracting Poppler ..."
  if (Test-Path $popplerDest) { Remove-Item $popplerDest -Recurse -Force }
  Expand-Archive $popplerZip $popplerDest
  $inner = (Get-ChildItem $popplerDest -Directory | Select-Object -First 1).FullName
  $popplerBin = "$inner\Library\bin"
  Add-ToUserPath $popplerBin
  Write-Ok "Poppler installed -- bin/ added to user PATH"
}

# -- FFmpeg -------------------------------------------------------------------

Write-Host ""
Write-Host "Checking FFmpeg ..." -ForegroundColor $Yellow
if (Test-CommandExists ffmpeg) {
  Write-Ok "FFmpeg found: $(Get-Version ffmpeg)"
} elseif (Test-CommandExists winget) {
  Write-Step "Installing FFmpeg via winget ..."
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  Write-Ok "FFmpeg installed"
} else {
  $ffmpegZip  = "$env:TEMP\ffmpeg.zip"
  $ffmpegDest = "$env:LOCALAPPDATA\ffmpeg"
  $ffmpegUrl  = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
  Download-File $ffmpegUrl $ffmpegZip
  Write-Step "Extracting FFmpeg ..."
  if (Test-Path $ffmpegDest) { Remove-Item $ffmpegDest -Recurse -Force }
  Expand-Archive $ffmpegZip $ffmpegDest
  $inner = (Get-ChildItem $ffmpegDest -Directory | Select-Object -First 1).FullName
  Add-ToUserPath "$inner\bin"
  Write-Ok "FFmpeg installed"
}

# -- espeak-ng (optional -- only needed for text-to-speech) ------------------

Write-Host ""
Write-Host "Checking espeak-ng (optional TTS) ..." -ForegroundColor $Yellow
if (Test-CommandExists espeak-ng) {
  Write-Ok "espeak-ng found: $(Get-Version espeak-ng)"
} elseif (Test-CommandExists winget) {
  try {
    Write-Step "Installing espeak-ng via winget ..."
    winget install -e --id eSpeak.eSpeakNG --accept-package-agreements --accept-source-agreements
    Add-ToUserPath "C:\Program Files\eSpeak NG"
    Write-Ok "espeak-ng installed"
  } catch {
    Write-Warn "espeak-ng install failed (TTS will be unavailable). Install manually from https://github.com/espeak-ng/espeak-ng/releases"
  }
} else {
  try {
    Write-Step "Downloading espeak-ng installer ..."
    $espeakInstaller = "$env:TEMP\espeak-ng-setup.msi"
    $espeakUrl = "https://github.com/espeak-ng/espeak-ng/releases/download/1.52.0/espeak-ng-20230428-b702bcd-x64.msi"
    Download-File $espeakUrl $espeakInstaller
    Write-Step "Running espeak-ng installer (may require UAC elevation) ..."
    Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$espeakInstaller`" /quiet /norestart" -Wait
    Add-ToUserPath "C:\Program Files\eSpeak NG"
    Write-Ok "espeak-ng installed"
  } catch {
    Write-Warn "espeak-ng install failed (TTS will be unavailable). Install manually from https://github.com/espeak-ng/espeak-ng/releases"
  }
}

# -- Python + Node dependencies -----------------------------------------------

Write-Host ""
Write-Host "Installing Python dependencies ..." -ForegroundColor $Yellow
Write-Step "Running: uv sync"
Push-Location (Split-Path $PSScriptRoot -Parent)
uv sync
Write-Ok "Python dependencies installed"

Write-Host ""
Write-Host "Installing Node dependencies ..." -ForegroundColor $Yellow
Write-Step "Running: pnpm install"
pnpm install
Write-Ok "Node dependencies installed"
Pop-Location

# -- summary ------------------------------------------------------------------

Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Install a local AI server:" -ForegroundColor White
Write-Host "       Download Ollama from https://ollama.com/download" -ForegroundColor Gray
Write-Host "       Then run:  ollama pull llama3.2" -ForegroundColor Gray
Write-Host ""
Write-Host "    2. Start Orivellum (from the project root):" -ForegroundColor White
Write-Host "       .\scripts\start.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "    3. Open http://localhost:8000 in your browser" -ForegroundColor White
Write-Host "---------------------------------------" -ForegroundColor Green
Write-Host ""
