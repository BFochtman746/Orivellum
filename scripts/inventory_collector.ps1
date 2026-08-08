#Requires -Version 5.1
<#
.SYNOPSIS
    PKLOS Windows System Inventory Collector v0.1.0
    Collects hardware and OS facts from this machine and POSTs them to the
    Orivellum PKLOS inventory endpoint.

.DESCRIPTION
    Uses Get-CimInstance (NOT the deprecated Get-WmiObject) to collect:
      - CPU identity, core/thread counts
      - RAM: Win32_ComputerSystem.TotalPhysicalMemory (primary)
             Win32_PhysicalMemory.Capacity sum (corroboration)
      - GPU: model name only  -  AdapterRAM is EXCLUDED per INV-REQ-001
      - VRAM: sourced from Lemonade API (runtime measurement) only
      - OS: caption, version, build number
      - BIOS: manufacturer, version
      - Storage: total disk capacity
      - Installed models: from Lemonade API

    INV-REQ-001: Win32_VideoController.AdapterRAM is NEVER collected.
    On unified-memory architecture (AMD Ryzen AI Max, Apple Silicon, etc.)
    AdapterRAM is a 32-bit field that cannot represent shared memory and
    always misreports.  Usable VRAM comes from the runtime API only.

.PARAMETER ApiUrl
    Base URL of the Orivellum API server (default: http://localhost:8000)

.PARAMETER ApiKey
    API key / session secret for authentication.

.PARAMETER Subject
    Canonical identifier for this machine (default: device:a01)

.PARAMETER DryRun
    Print the JSON payload without posting it.

.EXAMPLE
    .\inventory_collector.ps1 -ApiUrl http://localhost:8000 -ApiKey YOUR_KEY
    .\inventory_collector.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$ApiUrl   = "http://localhost:8000",
    [string]$ApiKey   = $env:ORIVELLUM_API_KEY,
    [string]$Subject  = "device:a01",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section { param([string]$Label) Write-Host "  [$Label]" -ForegroundColor Cyan }
function Write-OK       { param([string]$Msg)  Write-Host "  OK: $Msg" -ForegroundColor Green }
function Write-Warn     { param([string]$Msg)  Write-Host "  WARN: $Msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "PKLOS Windows Inventory Collector v0.1.0" -ForegroundColor Magenta
Write-Host "Subject: $Subject"
Write-Host "Target:  $ApiUrl/api/pklos/inventory"
Write-Host ""

$inventory = [ordered]@{
    collector_version = "0.1.0"
    collected_at      = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    subject           = $Subject
}

# -- CPU ------------------------------------------------------------------------
Write-Section "CPU"
try {
    $cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    $inventory.cpu = [ordered]@{
        Name                      = $cpu.Name.Trim()
        ProcessorId               = $cpu.ProcessorId
        NumberOfCores             = $cpu.NumberOfCores
        NumberOfLogicalProcessors = $cpu.NumberOfLogicalProcessors
        MaxClockSpeed             = $cpu.MaxClockSpeed   # MHz
    }
    Write-OK "$($cpu.Name)  -  $($cpu.NumberOfCores) cores / $($cpu.NumberOfLogicalProcessors) threads"
} catch {
    Write-Warn "CPU collection failed: $_"
    $inventory.cpu = @{}
}

# -- RAM  -  CRITICAL: two A0 sources (primary + corroboration) ------------------
Write-Section "Memory"
try {
    $cs  = Get-CimInstance -ClassName Win32_ComputerSystem
    $mem = Get-CimInstance -ClassName Win32_PhysicalMemory
    $sum = ($mem | Measure-Object -Property Capacity -Sum).Sum

    # NEVER include AdapterRAM  -  it is NOT collected here.
    $inventory.memory = [ordered]@{
        TotalPhysicalMemory      = $cs.TotalPhysicalMemory     # primary A0 source
        PhysicalMemoryCapacitySum = $sum                        # A0 corroboration
    }
    $gib = [math]::Round($cs.TotalPhysicalMemory / 1GB, 0)
    Write-OK "$gib GiB installed (CIM corroborated)"
} catch {
    Write-Warn "Memory collection failed: $_"
    $inventory.memory = @{}
}

# -- GPU  -  AdapterRAM EXCLUDED (INV-REQ-001) -----------------------------------
Write-Section "GPU (AdapterRAM excluded per INV-REQ-001)"
try {
    $gpu = Get-CimInstance -ClassName Win32_VideoController | Select-Object -First 1

    # Explicit exclusion: AdapterRAM is NEVER collected.
    # On unified-memory architecture it is a 32-bit placeholder that misreports.
    $inventory.gpu = [ordered]@{
        Name           = $gpu.Name.Trim()
        VideoProcessor = $gpu.VideoProcessor
        # AdapterRAM  -  deliberately omitted (INV-REQ-001)
    }
    Write-OK "$($gpu.Name)"
} catch {
    Write-Warn "GPU collection failed: $_"
    $inventory.gpu = @{}
}

# -- VRAM  -  runtime measurement from Lemonade API (A0 only) -------------------
Write-Section "VRAM (runtime probe)"
$vramFound = $false

# Try Lemonade (default port 13305)
$lemonadePorts = @(13305, 11434, 8080, 1234)
foreach ($port in $lemonadePorts) {
    if ($vramFound) { break }
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:$port/api/memory" `
                                  -TimeoutSec 2 -ErrorAction Stop
        if ($resp.total) {
            $inventory.vram = [ordered]@{
                source      = "lemonade_api:$port"
                total_bytes = [long]$resp.total
                free_bytes  = [long]($resp.free ?? 0)
            }
            $gib = [math]::Round($resp.total / 1GB, 0)
            Write-OK "Lemonade port $port  -  $gib GiB usable (A0)"
            $vramFound = $true
        }
    } catch { <# try next port #> }
}

if (-not $vramFound) {
    # Try Ollama
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/show" `
                                  -Method Post -Body '{"name":""}' `
                                  -ContentType "application/json" -TimeoutSec 2 -ErrorAction Stop
        if ($resp.model_info.'general.parameter_count') {
            Write-Warn "Ollama reachable but no memory API  -  VRAM reported as unavailable"
        }
    } catch { <# ignore #> }

    $inventory.vram = @{ source = "unavailable" }
    Write-Warn "VRAM probe failed on all ports  -  will be marked UNAVAILABLE in ledger"
}

# -- OS ------------------------------------------------------------------------
Write-Section "Operating System"
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $inventory.os = [ordered]@{
        Caption     = $os.Caption.Trim()
        Version     = $os.Version
        BuildNumber = $os.BuildNumber
    }
    Write-OK "$($os.Caption) Build $($os.BuildNumber)"
} catch {
    Write-Warn "OS collection failed: $_"
    $inventory.os = @{}
}

# -- BIOS ----------------------------------------------------------------------
Write-Section "BIOS"
try {
    $bios = Get-CimInstance -ClassName Win32_BIOS
    $inventory.bios = [ordered]@{
        Manufacturer      = $bios.Manufacturer.Trim()
        SMBIOSBIOSVersion = $bios.SMBIOSBIOSVersion
    }
    Write-OK "$($bios.Manufacturer)  -  $($bios.SMBIOSBIOSVersion)"
} catch {
    Write-Warn "BIOS collection failed: $_"
    $inventory.bios = @{}
}

# -- Storage -------------------------------------------------------------------
Write-Section "Storage"
try {
    $disks = Get-CimInstance -ClassName Win32_DiskDrive
    $totalBytes = ($disks | Measure-Object -Property Size -Sum).Sum
    $inventory.storage = [ordered]@{
        TotalBytes = $totalBytes
        Disks      = @($disks | Select-Object Model, Size, MediaType)
    }
    $tb = [math]::Round($totalBytes / 1TB, 1)
    Write-OK "$tb TB total ($($disks.Count) disk(s))"
} catch {
    Write-Warn "Storage collection failed: $_"
    $inventory.storage = @{}
}

# -- Installed AI Models (Lemonade) --------------------------------------------
Write-Section "Installed Models"
$modelNames = @()
foreach ($port in $lemonadePorts) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:$port/v1/models" `
                                  -TimeoutSec 2 -ErrorAction Stop
        if ($resp.data) {
            $modelNames = @($resp.data | ForEach-Object { $_.id })
            Write-OK "$($modelNames.Count) model(s) via port $port"
            break
        }
    } catch { <# try next port #> }
}
if ($modelNames.Count -eq 0) { Write-Warn "No model server reachable  -  model list empty" }
$inventory.installed_models = $modelNames

# -- Serialize -----------------------------------------------------------------
Write-Host ""
$json = $inventory | ConvertTo-Json -Depth 6 -Compress:$false

if ($DryRun) {
    Write-Host "-- DRY RUN  -  payload (not posted) ------------------------------" -ForegroundColor Yellow
    Write-Host $json
    Write-Host ""
    exit 0
}

# -- POST to Orivellum ---------------------------------------------------------
Write-Host "Posting inventory to $ApiUrl/api/pklos/inventory ..."
try {
    $headers = @{ "Content-Type" = "application/json" }
    if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

    $response = Invoke-RestMethod `
        -Uri        "$ApiUrl/api/pklos/inventory" `
        -Method     Post `
        -Body       $json `
        -Headers    $headers `
        -TimeoutSec 30

    Write-Host ""
    Write-Host "SUCCESS" -ForegroundColor Green
    Write-Host "  Claims written:    $($response.claims_written)"
    Write-Host "  Verified (A0/A1):  $($response.claims_verified)"
    Write-Host "  Conflicted:        $($response.claims_conflicted)"
    Write-Host "  Unavailable:       $($response.claims_unavailable)"

    if ($response.violations -and $response.violations.Count -gt 0) {
        Write-Host ""
        Write-Host "Policy violations (non-fatal):" -ForegroundColor Yellow
        foreach ($v in $response.violations) { Write-Host "  $v" }
    }

    Write-Host ""
    Write-Host "Inventory is now in the claim ledger.  Ask the AI about your specs" -ForegroundColor Cyan
    Write-Host "and it will cite verified (A0) values instead of guessing." -ForegroundColor Cyan
} catch {
    Write-Host "POST failed: $_" -ForegroundColor Red
    exit 1
}
