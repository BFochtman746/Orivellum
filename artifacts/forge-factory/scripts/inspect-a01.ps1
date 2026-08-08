[CmdletBinding()]
param(
  [string]$OutputPath = (Join-Path $PSScriptRoot '..\data\a01-authority-inventory.json'),
  [string]$LemonadeBaseUrl = 'http://127.0.0.1:13305/api/v1'
)

$ErrorActionPreference = 'Stop'
$directory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $directory | Out-Null

function Get-CommandVersion([string]$Name, [string[]]$Arguments = @('--version')) {
  try {
    $command = Get-Command $Name -ErrorAction Stop
    $result = & $command.Source @Arguments 2>&1 | Select-Object -First 1
    return @{ available = $true; path = $command.Source; version = "$result" }
  } catch { return @{ available = $false; error = $_.Exception.Message } }
}

$lemonade = @{ available = $false }
try {
  $models = Invoke-RestMethod -Uri "$LemonadeBaseUrl/models" -Headers @{ Authorization = 'Bearer lemonade' } -TimeoutSec 10
  $lemonade = @{ available = $true; baseUrl = $LemonadeBaseUrl; models = @($models.data); checkedAt = (Get-Date).ToUniversalTime().ToString('o') }
} catch { $lemonade = @{ available = $false; baseUrl = $LemonadeBaseUrl; error = $_.Exception.Message; checkedAt = (Get-Date).ToUniversalTime().ToString('o') } }

$inventory = [ordered]@{
  schema = 'orivellum-forge.a01-authority-inventory.v1'
  capturedAt = (Get-Date).ToUniversalTime().ToString('o')
  computer = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, TotalPhysicalMemory, NumberOfLogicalProcessors
  operatingSystem = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime
  processors = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed)
  graphics = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM, CurrentHorizontalResolution, CurrentVerticalResolution)
  disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName, Size, FreeSpace, FileSystem)
  network = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object InterfaceAlias, IPAddress, PrefixLength)
  tools = [ordered]@{ node = Get-CommandVersion 'node'; npm = Get-CommandVersion 'npm'; git = Get-CommandVersion 'git'; semgrep = Get-CommandVersion 'semgrep'; gitleaks = Get-CommandVersion 'gitleaks'; osvScanner = Get-CommandVersion 'osv-scanner' }
  lemonade = $lemonade
}

$inventory | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputPath
Write-Host "Authority inventory written to $OutputPath"
if (-not $lemonade.available) { Write-Warning "Lemonade was not verified. Do not start a build job until its local endpoint and a model are healthy." }
