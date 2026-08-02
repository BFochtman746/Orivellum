#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot Windows setup for Orivellum.

.DESCRIPTION
  Installs all prerequisites, then runs uv sync and pnpm install.
  Safe to re-run -- already-installed tools are skipped.

.NOTES
  Run from the project root or the scripts\ subfolder.
  Requires internet access. winget handles most installs; a few tools
  are downloaded directly when winget is unavailable.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---- colour helpers ---------------------------------------------------------
function Write-Step { param($m) Write-Host "  >> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "  [X]  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host "  Orivellum -- Windows Setup" -ForegroundColor Cyan
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host ""

# ---- helpers ----------------------------------------------------------------

function Test-Cmd { param($n) return [bool](Get-Command $n -ErrorAction SilentlyContinue) }

function Get-Ver {
  param($cmd, $arg = "--version")
  try { (& $cmd $arg 2>&1) | Select-Object -First 1 } catch { "" }
}

function Add-UserPath {
  param([string]$dir)
  if (-not (Test-Path $dir)) { return }
  $cur = [Environment]::GetEnvironmentVariable("PATH", "User")
  if ($cur -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$dir;$cur", "User")
    Write-Ok "Added to user PATH: $dir"
  }
  # Also update the current session
  if ($env:PATH -notlike "*$dir*") { $env:PATH = "$dir;$env:PATH" }
}

function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable("PATH", "Machine")
  $u = [Environment]::GetEnvironmentVariable("PATH", "User")
  $env:PATH = "$u;$m"
}

function Download-File {
  param([string]$url, [string]$dest)
  Write-Step "Downloading $(Split-Path $dest -Leaf) ..."
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

function Winget-Install {
  param([string]$id, [string]$name)
  Write-Step "Installing $name via winget ..."
  winget install -e --id $id `
    --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-Null
  Write-Ok "$name installed"
}

# ---- Python -----------------------------------------------------------------
Write-Host "Checking Python ..." -ForegroundColor Yellow
if (Test-Cmd python) {
  Write-Ok "Python: $(Get-Ver python)"
} elseif (Test-Cmd winget) {
  Winget-Install "Python.Python.3.12" "Python 3.12"
  Refresh-Path
} else {
  Write-Warn "Download Python 3.12+ from https://www.python.org/downloads/"
}

# ---- Node.js ----------------------------------------------------------------
Write-Host ""
Write-Host "Checking Node.js ..." -ForegroundColor Yellow
if (Test-Cmd node) {
  Write-Ok "Node.js: $(Get-Ver node)"
} elseif (Test-Cmd winget) {
  Winget-Install "OpenJS.NodeJS.LTS" "Node.js LTS"
  Refresh-Path
} else {
  Write-Warn "Download Node.js 20+ from https://nodejs.org/"
}

# ---- pnpm -------------------------------------------------------------------
Write-Host ""
Write-Host "Checking pnpm ..." -ForegroundColor Yellow
Refresh-Path
if (Test-Cmd pnpm) {
  Write-Ok "pnpm: $(Get-Ver pnpm)"
} elseif (Test-Cmd winget) {
  Winget-Install "pnpm.pnpm" "pnpm"
  Refresh-Path
} else {
  # Standalone installer (no Node.js required)
  Write-Step "Installing pnpm via standalone installer ..."
  $ProgressPreference = "SilentlyContinue"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $pnpmScript = "$env:TEMP\pnpm-install.ps1"
  Invoke-WebRequest -Uri "https://get.pnpm.io/install.ps1" -OutFile $pnpmScript -UseBasicParsing
  & powershell -ExecutionPolicy Bypass -File $pnpmScript
  # pnpm installs to %LOCALAPPDATA%\pnpm on Windows
  Add-UserPath "$env:LOCALAPPDATA\pnpm"
  Refresh-Path
  Write-Ok "pnpm installed"
}

# ---- uv ---------------------------------------------------------------------
Write-Host ""
Write-Host "Checking uv ..." -ForegroundColor Yellow
Refresh-Path
if (Test-Cmd uv) {
  Write-Ok "uv: $(Get-Ver uv)"
} elseif (Test-Cmd winget) {
  Winget-Install "astral-sh.uv" "uv"
  Refresh-Path
} else {
  Write-Step "Installing uv via standalone installer ..."
  $ProgressPreference = "SilentlyContinue"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $uvScript = "$env:TEMP\uv-install.ps1"
  Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvScript -UseBasicParsing
  & powershell -ExecutionPolicy Bypass -File $uvScript
  # uv installs to %USERPROFILE%\.local\bin on Windows
  Add-UserPath "$env:USERPROFILE\.local\bin"
  # Also check AppData\Roaming location used by some versions
  Add-UserPath "$env:APPDATA\uv\bin"
  Refresh-Path
  Write-Ok "uv installed"
}

# ---- Tesseract OCR ----------------------------------------------------------
Write-Host ""
Write-Host "Checking Tesseract OCR ..." -ForegroundColor Yellow
$tessExe = "C:\Program Files\Tesseract-OCR\tesseract.exe"
if ((Test-Cmd tesseract) -or (Test-Path $tessExe)) {
  Write-Ok "Tesseract found"
} elseif (Test-Cmd winget) {
  try { Winget-Install "UB-Mannheim.TesseractOCR" "Tesseract OCR"; Refresh-Path }
  catch {
    Write-Warn "Tesseract winget install failed. Download from https://github.com/UB-Mannheim/tesseract/wiki"
  }
} else {
  try {
    $tessInst = "$env:TEMP\tesseract-setup.exe"
    Download-File "https://github.com/UB-Mannheim/tesseract/releases/download/v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe" $tessInst
    Start-Process -FilePath $tessInst -ArgumentList "/SILENT /NORESTART" -Wait
    Add-UserPath "C:\Program Files\Tesseract-OCR"
    Write-Ok "Tesseract installed"
  } catch { Write-Warn "Tesseract install failed. OCR will be unavailable." }
}

# ---- Poppler ----------------------------------------------------------------
Write-Host ""
Write-Host "Checking Poppler (PDF tools) ..." -ForegroundColor Yellow
if (Test-Cmd pdfinfo) {
  Write-Ok "Poppler found"
} else {
  try {
    $popplerZip  = "$env:TEMP\poppler.zip"
    $popplerDest = "$env:LOCALAPPDATA\poppler"
    Download-File "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip" $popplerZip
    if (Test-Path $popplerDest) { Remove-Item $popplerDest -Recurse -Force }
    $ProgressPreference = "SilentlyContinue"
    Expand-Archive $popplerZip $popplerDest -Force
    $inner = (Get-ChildItem $popplerDest -Directory | Select-Object -First 1).FullName
    Add-UserPath "$inner\Library\bin"
    Write-Ok "Poppler installed"
  } catch { Write-Warn "Poppler install failed. PDF rendering may be limited." }
}

# ---- FFmpeg -----------------------------------------------------------------
Write-Host ""
Write-Host "Checking FFmpeg ..." -ForegroundColor Yellow
if (Test-Cmd ffmpeg) {
  Write-Ok "FFmpeg found"
} elseif (Test-Cmd winget) {
  try { Winget-Install "Gyan.FFmpeg" "FFmpeg"; Refresh-Path }
  catch { Write-Warn "FFmpeg install failed. Audio conversion will be unavailable." }
} else {
  try {
    $ffmpegZip  = "$env:TEMP\ffmpeg.zip"
    $ffmpegDest = "$env:LOCALAPPDATA\ffmpeg"
    Download-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $ffmpegZip
    if (Test-Path $ffmpegDest) { Remove-Item $ffmpegDest -Recurse -Force }
    $ProgressPreference = "SilentlyContinue"
    Expand-Archive $ffmpegZip $ffmpegDest -Force
    $inner = (Get-ChildItem $ffmpegDest -Directory | Select-Object -First 1).FullName
    Add-UserPath "$inner\bin"
    Write-Ok "FFmpeg installed"
  } catch { Write-Warn "FFmpeg install failed. Audio conversion will be unavailable." }
}

# ---- espeak-ng (optional TTS) -----------------------------------------------
Write-Host ""
Write-Host "Checking espeak-ng (optional TTS) ..." -ForegroundColor Yellow
$espeakExe = "C:\Program Files\eSpeak NG\espeak-ng.exe"
if ((Test-Cmd espeak-ng) -or (Test-Path $espeakExe)) {
  Write-Ok "espeak-ng found"
  Add-UserPath "C:\Program Files\eSpeak NG"
} else {
  # Not on winget — download the MSI directly from GitHub releases
  try {
    $espeakMsi = "$env:TEMP\espeak-ng-setup.msi"
    Download-File "https://github.com/espeak-ng/espeak-ng/releases/download/1.51.1/espeak-ng-20240117-win64.msi" $espeakMsi
    Write-Step "Installing espeak-ng (silent) ..."
    Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$espeakMsi`" /quiet /norestart" -Wait
    Add-UserPath "C:\Program Files\eSpeak NG"
    Refresh-Path
    Write-Ok "espeak-ng installed — Text-to-speech enabled"
  } catch {
    Write-Warn "espeak-ng install failed. TTS will be disabled. Manual install: https://github.com/espeak-ng/espeak-ng/releases"
  }
}

# ---- Final PATH refresh before dependency installs --------------------------
Write-Host ""
Write-Step "Refreshing PATH ..."
Refresh-Path
Write-Ok "PATH refreshed"

# Resolve executables -- scan known locations if not on PATH
function Find-Exe {
  param([string]$name, [string[]]$candidates)
  if (Test-Cmd $name) { return $name }
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

# ---- Project root -----------------------------------------------------------
$root = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { Get-Location }

# ---- Python dependencies ----------------------------------------------------
Write-Host ""
Write-Host "Installing Python dependencies (uv sync) ..." -ForegroundColor Yellow
if ($null -eq $uvExe) {
  Write-Fail "uv not found. Please restart your terminal and run: uv sync"
} else {
  Push-Location $root
  Write-Step "Ensuring Python 3.12 is available ..."
  & $uvExe python install 3.12
  Write-Step "Running: uv sync --python 3.12"
  & $uvExe sync --python 3.12
  Pop-Location
  Write-Ok "Python dependencies installed"
}

# ---- Node dependencies ------------------------------------------------------
Write-Host ""
Write-Host "Installing Node dependencies (pnpm install) ..." -ForegroundColor Yellow
if ($null -eq $pnpmExe) {
  Write-Fail "pnpm not found. Please restart your terminal and run: pnpm install"
} else {
  Push-Location $root
  & $pnpmExe install
  Pop-Location
  Write-Ok "Node dependencies installed"
}

# ---- Done -------------------------------------------------------------------
Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Install Ollama for AI:  https://ollama.com/download" -ForegroundColor Gray
Write-Host "       Then run:  ollama pull llama3.2" -ForegroundColor Gray
Write-Host ""
Write-Host "    2. Start Orivellum (from the project root):" -ForegroundColor White
Write-Host "       .\scripts\start.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "    3. Open http://localhost:8000 in your browser" -ForegroundColor White
Write-Host "---------------------------------------" -ForegroundColor Green
Write-Host ""
