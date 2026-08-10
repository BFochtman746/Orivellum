# ============================================================================
# WRITING_ARCHITECT installer  (Windows PowerShell)
# Zero third-party dependencies. Works offline.
# Run in PowerShell:   powershell -ExecutionPolicy Bypass -File .\install.ps1
# ============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "WRITING_ARCHITECT installer"
Write-Host "==========================="

# 1) Find Python 3.9+
$py = $null
foreach ($cand in @("python", "py -3")) {
    try {
        $ver = & cmd /c "$cand --version" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match "Python 3\.(9|1[0-9])") { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Error "Python 3.9+ is required. Install from https://www.python.org/downloads/ and re-run."
    exit 1
}
Write-Host "Using Python: $(& cmd /c "$py --version")"

# 2) Try pip install (offline-safe: no deps).
$installed = $false
try {
    & cmd /c "$py -m pip --version" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Installing via pip (user site)..."
        & cmd /c "$py -m pip install --user ." 
        if ($LASTEXITCODE -eq 0) { $installed = $true }
    }
} catch {}

# 3) Fallback: create a wa.cmd launcher on the user PATH.
if (-not $installed) {
    $binDir = Join-Path $env:USERPROFILE ".wa\bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $launcher = Join-Path $binDir "wa.cmd"
    @"
@echo off
set PYTHONPATH=$ScriptDir;%PYTHONPATH%
$py -m writing_architect %*
"@ | Set-Content -Encoding ASCII $launcher
    Write-Host "Installed launcher at: $launcher"
    Write-Host "NOTE: add this folder to PATH:  $binDir"
}

# 4) Verify
Write-Host ""
Write-Host "Verifying installation..."
$env:PYTHONPATH = "$ScriptDir;$env:PYTHONPATH"
& cmd /c "$py -m writing_architect doctor"

Write-Host ""
Write-Host "Done. Next step:  wa forensics WRITING_ARCHITECT.zip --out wr00_baseline"
