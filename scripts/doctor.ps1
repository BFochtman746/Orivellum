#Requires -Version 5.1
<#
.SYNOPSIS
  Orivellum health doctor -- verifies the whole Windows install is working
  and running optimally. Safe to run any time; changes nothing.

.DESCRIPTION
  Checks, in order:
    1. Core tools        (uv, node, pnpm, git) + optional tools (tesseract, ffmpeg)
    2. Disk space        (LLM models live in the Hugging Face cache and are large)
    3. Lemonade Server   reachable on http://127.0.0.1:13305 (its default port),
                         required models pulled, config.json tuning hints
    4. Orivellum API     /api/healthz on the API port (default 8080)
    5. UI build          dist/public bundle present

  Every failed check prints the exact fix, with links to the current
  official documentation (verified Aug 2026):
    Lemonade install : https://lemonade-server.ai/docs/guide/install/
    Lemonade config  : https://lemonade-server.ai/docs/guide/configuration/
    NPU driver       : https://lemonade-server.ai/driver_install
    Lemonade FAQ     : https://lemonade-server.ai/docs/guide/faq/

  For a deep application-level report (database, pipeline, knowledge health),
  run:  uv run python scripts\run_diagnostics.py
  That report is designed to be pasted into any AI chat for evaluation.

.PARAMETER ApiPort
  Orivellum API port to probe (default: API_PORT env var, else 8080).

.EXAMPLE
  .\scripts\doctor.ps1
#>

param(
  # Accept anything; validated below so a bad API_PORT env var cannot crash
  # the doctor before it reports a single check.
  $ApiPort = $env:API_PORT
)

$ErrorActionPreference = "SilentlyContinue"

$script:BadApiPort = $null
$parsedPort = 0
if ($ApiPort -and [int]::TryParse("$ApiPort", [ref]$parsedPort) -and $parsedPort -gt 0 -and $parsedPort -lt 65536) {
  $ApiPort = $parsedPort
} else {
  if ($ApiPort) { $script:BadApiPort = "$ApiPort" }
  $ApiPort = 8080
}

$script:PassCount = 0
$script:WarnCount = 0
$script:FailCount = 0

function Write-Pass { param($m) $script:PassCount++; Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Warn2 { param($m) $script:WarnCount++; Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Fail2 { param($m) $script:FailCount++; Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Fix  { param($m) Write-Host "         fix: $m" -ForegroundColor Gray }
function Write-Sect { param($m) Write-Host ""; Write-Host "-- $m " -ForegroundColor Cyan }

$root = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { Get-Location }

Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host "  Orivellum Doctor" -ForegroundColor Cyan
Write-Host "---------------------------------------" -ForegroundColor Cyan

# ---- Refresh PATH so tools installed by setup-windows.ps1 are visible -------
$machinePath = [Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath    = [Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH    = "$userPath;$machinePath"

function Find-Tool {
  param([string]$name, [string[]]$candidates = @())
  $found = Get-Command $name -ErrorAction SilentlyContinue
  if ($found) { return $found.Source }
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  return $null
}

function Get-ToolVersion {
  param($exe, $arg = "--version")
  try { ((& $exe $arg 2>&1) | Select-Object -First 1).ToString().Trim() } catch { "" }
}

# ---- 1. Core tools -----------------------------------------------------------
Write-Sect "Core tools"

$uvExe = Find-Tool "uv" @("$env:USERPROFILE\.local\bin\uv.exe", "$env:APPDATA\uv\bin\uv.exe")
if ($uvExe) { Write-Pass ("uv       " + (Get-ToolVersion $uvExe)) }
else {
  Write-Fail2 "uv not found"
  Write-Fix "run scripts\setup-windows.ps1, or: winget install astral-sh.uv  (docs: https://docs.astral.sh/uv/getting-started/installation/)"
}

$nodeExe = Find-Tool "node"
if ($nodeExe) {
  $nodeVer = Get-ToolVersion $nodeExe
  Write-Pass "node     $nodeVer"
  $major = 0
  if ($nodeVer -match "v(\d+)") { $major = [int]$Matches[1] }
  if ($major -gt 0 -and $major -lt 20) {
    Write-Warn2 "Node $nodeVer is older than v20 -- pnpm 10+ requires Node 18.12+, v20 LTS or newer recommended"
    Write-Fix "winget upgrade OpenJS.NodeJS.LTS"
  }
} else {
  Write-Fail2 "node not found"
  Write-Fix "run scripts\setup-windows.ps1, or: winget install OpenJS.NodeJS.LTS"
}

$pnpmExe = Find-Tool "pnpm" @("$env:LOCALAPPDATA\pnpm\pnpm.exe", "$env:LOCALAPPDATA\pnpm\pnpm.cmd")
if ($pnpmExe) { Write-Pass ("pnpm     " + (Get-ToolVersion $pnpmExe)) }
else {
  Write-Fail2 "pnpm not found"
  Write-Fix "run scripts\setup-windows.ps1, or see https://pnpm.io/installation"
}

$gitExe = Find-Tool "git"
if ($gitExe) { Write-Pass ("git      " + (Get-ToolVersion $gitExe)) }
else { Write-Warn2 "git not found (needed for updates: git pull)"; Write-Fix "winget install Git.Git" }

# Optional capability tools
$tessExe = Find-Tool "tesseract" @("C:\Program Files\Tesseract-OCR\tesseract.exe")
if ($tessExe) { Write-Pass "tesseract (classic OCR fallback) present" }
else { Write-Warn2 "tesseract not found -- OCR falls back to the vision model only" }

$ffmpegExe = Find-Tool "ffmpeg"
if ($ffmpegExe) { Write-Pass "ffmpeg (audio pipeline) present" }
else { Write-Warn2 "ffmpeg not found -- audiobook/audio features will fail"; Write-Fix "winget install Gyan.FFmpeg" }

# ---- 2. Disk space -----------------------------------------------------------
Write-Sect "Disk space"

# Models live in the Hugging Face cache under the user profile by default
# (https://lemonade-server.ai/docs/guide/faq/ -> 'Where are models stored').
$diskChecked = $false
try {
  $profileDrive = (Get-Item $env:USERPROFILE -ErrorAction Stop).PSDrive.Name
  $drive = Get-PSDrive -Name $profileDrive -ErrorAction Stop
  if ($null -ne $drive.Free) {
    $diskChecked = $true
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt 20) {
      Write-Fail2 "Only $freeGB GB free on $($profileDrive): -- model pulls and uploads will fail"
      Write-Fix "free space, or move the model cache with the HF_HOME env var (Lemonade FAQ, 'Where are models stored')"
    } elseif ($freeGB -lt 80) {
      Write-Warn2 "$freeGB GB free on $($profileDrive): -- enough to run, but a large model pull (gpt-oss-120b is ~63 GB) may not fit"
    } else {
      Write-Pass "$freeGB GB free on $($profileDrive):"
    }
  }
} catch {}
if (-not $diskChecked) {
  Write-Warn2 "Could not determine free disk space for the user profile drive -- check it manually (models need tens of GB)"
}

$hfCache = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
if ($env:HF_HOME) { $hfCache = Join-Path $env:HF_HOME "hub" }
if (Test-Path $hfCache) {
  Write-Pass "Model cache present: $hfCache"
} else {
  Write-Warn2 "Model cache not found at $hfCache -- no models pulled yet?"
}

# ---- 3. Lemonade Server --------------------------------------------------------
Write-Sect "Lemonade Server (LLM backend)"

# Read base_url + required model names straight from config.yaml so this
# check never drifts from what Orivellum actually uses.
# Line-anchored parser: handles double-quoted, single-quoted, and unquoted
# scalars, strips inline comments, and never matches commented-out lines.
function Get-YamlScalar {
  param([string[]]$lines, [string]$key)
  foreach ($line in $lines) {
    if ($line -match "^\s*$key\s*:\s*(.*)$") {
      $v = $Matches[1].Trim()
      if ($v.StartsWith('"')) {
        if ($v -match '^"([^"]*)"') { return $Matches[1] }
      } elseif ($v.StartsWith("'")) {
        if ($v -match "^'([^']*)'") { return $Matches[1] }
      } else {
        # Unquoted: cut inline comment, then trim
        $v = ($v -split '\s+#', 2)[0].Trim()
        if ($v -eq "#") { $v = "" }
        return $v
      }
      return ""
    }
  }
  return $null
}

$configYaml = Join-Path $root "config.yaml"
$lemonadeBase = "http://127.0.0.1:13305/api/v1"
$requiredModels = @()
$configParsed = $false
if (Test-Path $configYaml) {
  $yamlLines = @(Get-Content $configYaml)
  $bu = Get-YamlScalar $yamlLines "base_url"
  if ($bu) { $lemonadeBase = $bu.TrimEnd("/"); $configParsed = $true }
  foreach ($key in @("workhorse_model", "reasoner_model", "coder_model", "embedder_model")) {
    $val = Get-YamlScalar $yamlLines $key
    if ($null -ne $val) { $configParsed = $true }
    if ($val -and $requiredModels -notcontains $val) { $requiredModels += $val }
  }
  if (-not $configParsed) {
    Write-Warn2 "config.yaml found but no serving keys could be read -- checking the default Lemonade URL only"
  } elseif ($requiredModels.Count -eq 0) {
    Write-Warn2 "config.yaml has no model names set -- cannot verify which models must be pulled"
  }
} else {
  Write-Warn2 "config.yaml not found at $configYaml -- checking the default Lemonade URL only"
}

$modelsUrl = "$lemonadeBase/models"
$lemonadeUp = $false
$servedIds = @()
try {
  $resp = Invoke-WebRequest -Uri $modelsUrl -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
  if ($resp.StatusCode -eq 200) {
    $lemonadeUp = $true
    $json = $resp.Content | ConvertFrom-Json
    if ($json.data) { $servedIds = @($json.data | ForEach-Object { $_.id }) }
  }
} catch {}

if ($lemonadeUp) {
  Write-Pass "Lemonade answering at $modelsUrl ($($servedIds.Count) models available)"
  foreach ($m in $requiredModels) {
    if ($servedIds -contains $m) {
      Write-Pass "model pulled: $m"
    } else {
      Write-Fail2 "model MISSING: $m"
      Write-Fix "lemonade pull $m   (names must match the catalog exactly -- 'lemonade list' shows what you have)"
    }
  }
} else {
  Write-Fail2 "Lemonade Server not answering at $modelsUrl"
  Write-Fix "1) Run 'lemonade status' in a terminal; if stopped, launch Lemonade from the Start menu (lemon tray icon appears when it is up)"
  Write-Fix "2) Not installed? Get the MSI: https://lemonade-server.ai/docs/guide/install/"
  Write-Fix "3) NPU errors on Ryzen AI? Update the NPU driver (min 32.0.203.280): https://lemonade-server.ai/driver_install"
  Write-Fix "4) Wrong port? Lemonade's config.json is at %USERPROFILE%\.cache\lemonade\config.json ('port' key, default 13305)"
}

# Tuning hints from Lemonade's own config
# (https://lemonade-server.ai/docs/guide/configuration/)
$lemonadeCfgPath = Join-Path $env:USERPROFILE ".cache\lemonade\config.json"
if (Test-Path $lemonadeCfgPath) {
  try {
    $lc = Get-Content $lemonadeCfgPath -Raw | ConvertFrom-Json
    $mlm = $lc.max_loaded_models
    if ($null -ne $mlm) {
      if ([int]$mlm -lt 2) {
        Write-Warn2 "Lemonade max_loaded_models=$mlm -- chat + embeddings will evict each other and reload constantly"
        Write-Fix "lemonade config set max_loaded_models=2   (then restart Lemonade; it is per model type)"
      } else {
        Write-Pass "Lemonade max_loaded_models=$mlm"
      }
    }
    if ($null -ne $lc.log_level -and "$($lc.log_level)" -eq "debug") {
      Write-Warn2 "Lemonade log_level=debug slows inference -- set it back to info when not troubleshooting"
    }
  } catch {
    Write-Warn2 "Could not parse $lemonadeCfgPath (not fatal)"
  }
} elseif ($lemonadeUp) {
  Write-Warn2 "Lemonade config.json not found at $lemonadeCfgPath (created on first run -- fine if Lemonade just installed)"
}

# ---- 4. Orivellum API ----------------------------------------------------------
Write-Sect "Orivellum API"

$apiUp = $false
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
  if ($r.StatusCode -eq 200) { $apiUp = $true }
} catch {}

if ($script:BadApiPort) {
  Write-Warn2 "API_PORT env var is set to '$($script:BadApiPort)' which is not a valid port -- using default 8080"
}
if ($apiUp) {
  Write-Pass "API healthy on port $ApiPort"
} else {
  Write-Warn2 "API not running on port $ApiPort (fine if you have not started it yet)"
  Write-Fix "start it with: .\scripts\start.ps1   (logs land in logs\api-err.log)"
}

# ---- 5. UI build ---------------------------------------------------------------
Write-Sect "UI build"

$uiDist = Join-Path $root "artifacts\orivellum-ui\dist\public"
if (Test-Path (Join-Path $uiDist "sw.js")) {
  Write-Pass "Production UI bundle present"
} else {
  Write-Warn2 "No production UI bundle yet -- start.ps1 builds it on first run"
}

# ---- Summary --------------------------------------------------------------------
Write-Host ""
Write-Host "---------------------------------------" -ForegroundColor Cyan
$color = "Green"
if ($script:FailCount -gt 0) { $color = "Red" } elseif ($script:WarnCount -gt 0) { $color = "Yellow" }
Write-Host "  $($script:PassCount) OK / $($script:WarnCount) warnings / $($script:FailCount) failures" -ForegroundColor $color
if ($script:FailCount -eq 0 -and $script:WarnCount -eq 0) {
  Write-Host "  Everything looks healthy. You are running optimally." -ForegroundColor Green
}
Write-Host ""
Write-Host "  Deep application report (paste into any AI chat for evaluation):" -ForegroundColor Gray
Write-Host "    uv run python scripts\run_diagnostics.py" -ForegroundColor DarkCyan
Write-Host "---------------------------------------" -ForegroundColor Cyan
Write-Host ""

if ($script:FailCount -gt 0) { exit 1 } else { exit 0 }
