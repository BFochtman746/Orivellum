#!/usr/bin/env pwsh
# inspect-a01.ps1 — Forge Phase 0 Authority Inventory (Windows / PowerShell)
# Records actual hardware, OS, WSL, LM Studio, and network facts.
# Unknown items are labeled "unknown", never inferred.
#
# Usage: .\scripts\inspect-a01.ps1 -OutputDir forge-jobs\PHASE0
#        .\scripts\inspect-a01.ps1 -OutputDir forge-jobs\JOB-20260807-001

param(
    [Parameter(Mandatory=$true)]
    [string]$OutputDir,
    [string]$LMStudioUrl = "http://127.0.0.1:8080"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$timestamp = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
$inventory = [ordered]@{}

Write-Host "Forge A-01 Authority Inventory" -ForegroundColor Cyan
Write-Host "Output: $OutputDir" -ForegroundColor Gray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Operating System
# ---------------------------------------------------------------------------
Write-Host "[1/8] Operating system..." -ForegroundColor Yellow
try {
    $osInfo = Get-CimInstance Win32_OperatingSystem
    $inventory["os"] = [ordered]@{
        caption     = $osInfo.Caption
        version     = $osInfo.Version
        build       = $osInfo.BuildNumber
        arch        = $osInfo.OSArchitecture
        install_date = $osInfo.InstallDate?.ToString("yyyy-MM-dd")
    }
} catch {
    $inventory["os"] = @{ error = $_.Exception.Message }
}

# ---------------------------------------------------------------------------
# 2. Hardware
# ---------------------------------------------------------------------------
Write-Host "[2/8] Hardware..." -ForegroundColor Yellow
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $ram = Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum
    $disk = Get-PSDrive C | Select-Object Used, Free
    $inventory["hardware"] = [ordered]@{
        cpu_name   = $cpu.Name
        cpu_cores  = $cpu.NumberOfCores
        cpu_threads = $cpu.NumberOfLogicalProcessors
        ram_gb     = [math]::Round($ram.Sum / 1GB, 1)
        disk_c_used_gb = [math]::Round($disk.Used / 1GB, 1)
        disk_c_free_gb = [math]::Round($disk.Free / 1GB, 1)
    }
} catch {
    $inventory["hardware"] = @{ error = $_.Exception.Message }
}

# ---------------------------------------------------------------------------
# 3. GPU / NPU
# ---------------------------------------------------------------------------
Write-Host "[3/8] GPU/NPU..." -ForegroundColor Yellow
try {
    $gpus = Get-CimInstance Win32_VideoController | ForEach-Object {
        [ordered]@{
            name             = $_.Name
            driver_version   = $_.DriverVersion
            driver_date      = $_.DriverDate?.ToString("yyyy-MM-dd")
            vram_mb          = [math]::Round($_.AdapterRAM / 1MB, 0)
        }
    }
    $inventory["gpu"] = $gpus
} catch {
    $inventory["gpu"] = @{ error = $_.Exception.Message }
}

# ---------------------------------------------------------------------------
# 4. WSL
# ---------------------------------------------------------------------------
Write-Host "[4/8] WSL..." -ForegroundColor Yellow
try {
    $wslVersion = (wsl --version 2>&1) -join " "
    $wslList = (wsl --list --verbose 2>&1) -join "`n"
    $inventory["wsl"] = [ordered]@{
        version_output = $wslVersion
        distributions  = $wslList
    }
} catch {
    $inventory["wsl"] = @{ available = $false; error = $_.Exception.Message }
}

# ---------------------------------------------------------------------------
# 5. LM Studio
# ---------------------------------------------------------------------------
Write-Host "[5/8] LM Studio ($LMStudioUrl)..." -ForegroundColor Yellow
$lmStudio = [ordered]@{ url = $LMStudioUrl }

try {
    $healthResp = Invoke-WebRequest -Uri "$LMStudioUrl/v1/models" -TimeoutSec 5 -ErrorAction Stop
    $models = ($healthResp.Content | ConvertFrom-Json).data
    $lmStudio["reachable"] = $true
    $lmStudio["http_status"] = $healthResp.StatusCode
    $lmStudio["models_loaded"] = $models.Count
    $lmStudio["models"] = $models | ForEach-Object {
        [ordered]@{ id = $_.id; object = $_.object }
    }
} catch {
    $lmStudio["reachable"] = $false
    $lmStudio["error"] = $_.Exception.Message
    Write-Host "  WARNING: LM Studio not reachable. Is it running with a model loaded?" -ForegroundColor Red
}

# LM Studio process
$lmProcess = Get-Process -Name "LM Studio" -ErrorAction SilentlyContinue
$lmStudio["process_running"] = ($null -ne $lmProcess)
$inventory["lm_studio"] = $lmStudio

# ---------------------------------------------------------------------------
# 6. Network
# ---------------------------------------------------------------------------
Write-Host "[6/8] Network..." -ForegroundColor Yellow
try {
    $adapters = Get-NetAdapter | Where-Object Status -eq "Up" | ForEach-Object {
        [ordered]@{ name = $_.Name; description = $_.InterfaceDescription; link_speed = $_.LinkSpeed }
    }
    $inventory["network"] = [ordered]@{
        adapters     = $adapters
        tailscale_ip = (tailscale ip --4 2>&1) -join ""
    }
} catch {
    $inventory["network"] = @{ error = $_.Exception.Message }
}

# ---------------------------------------------------------------------------
# 7. Disk — WSL filesystem
# ---------------------------------------------------------------------------
Write-Host "[7/8] WSL disk..." -ForegroundColor Yellow
try {
    $wslDisk = wsl df -h /home 2>&1
    $inventory["wsl_disk"] = $wslDisk -join "`n"
} catch {
    $inventory["wsl_disk"] = "unknown"
}

# ---------------------------------------------------------------------------
# 8. Environment completeness check
# ---------------------------------------------------------------------------
Write-Host "[8/8] Completeness check..." -ForegroundColor Yellow
$complete = $true
$issues = @()

if (-not $inventory["lm_studio"]["reachable"]) {
    $complete = $false
    $issues += "LM Studio not reachable on $LMStudioUrl"
}
if ($inventory["hardware"]["ram_gb"] -lt 64) {
    $issues += "WARNING: Less than 64 GB RAM detected — large models may not load"
}

$inventory["completeness"] = [ordered]@{
    complete       = $complete
    issues         = $issues
    inventory_at   = $timestamp
    inspector      = "inspect-a01.ps1 v0.1.0"
}

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
$outputPath = Join-Path $OutputDir "authority-inventory.json"
$inventory | ConvertTo-Json -Depth 10 | Set-Content -Path $outputPath -Encoding UTF8

Write-Host ""
if ($complete) {
    Write-Host "COMPLETE — authority inventory written to $outputPath" -ForegroundColor Green
} else {
    Write-Host "INCOMPLETE — issues found:" -ForegroundColor Red
    $issues | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "Inventory written to $outputPath (review before proceeding)" -ForegroundColor Yellow
}
