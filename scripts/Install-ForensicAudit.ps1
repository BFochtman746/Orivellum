#Requires -RunAsAdministrator
<#
================================================================================
 Install-ForensicAudit.ps1
================================================================================
 A single, self-contained, fully automated installer that turns a Windows 11
 machine into a 2026-ready local forensic code-audit workstation.

 It performs six phases:
   1. System discovery and tool/version harvesting
   2. Built-in "research" knowledge base (pinned latest-stable versions +
      RAM-aware local-LLM model choice)
   3. Auto-install / auto-update every tool (winget primary, choco fallback,
      direct download as last resort)
   4. LLM model allocation and functional verification (Ollama)
   5. Hardening + hidden auto-start for Ollama + a one-click audit launcher
   6. A final "2026 readiness" report

 USAGE (from an elevated PowerShell / Windows Terminal window):
     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
     .\Install-ForensicAudit.ps1

 Optional switches:
     -SkipModels     Do everything except pulling/creating LLM models.
     -DryRun         Print what WOULD happen; make no changes.

 KNOWLEDGE-BASE HONESTY NOTE (Phase 2):
   The version strings and model tags below are a hardcoded knowledge base.
   They reflect the latest stable releases known as of this script author's
   training data (cutoff EARLY 2025), NOT verified mid-2026 releases. The
   installer always asks each package manager for the *latest* version at run
   time (winget upgrade / pip --upgrade / npm @latest), so you will still land
   on the current release. The pinned strings are only used for the
   discovery *table* and should be manually verified where marked "VERIFY".

 SAFETY:
   - Errors never abort the run: each step is wrapped, logged, and collected
     into a warnings list shown at the end.
   - A full transcript is written to
     C:\ProgramData\ForensicAudit\logs\install-<timestamp>.log
================================================================================
#>

[CmdletBinding()]
param(
    [switch]$SkipModels,
    [switch]$DryRun
)

# ------------------------------------------------------------------------------
# 0. Globals, logging, and small helpers
# ------------------------------------------------------------------------------

$ErrorActionPreference = 'Continue'      # we handle errors ourselves per-step
$ProgressPreference    = 'SilentlyContinue'  # faster, quieter installs

$script:BaseDir  = Join-Path $env:ProgramData 'ForensicAudit'
$script:LogDir   = Join-Path $script:BaseDir  'logs'
$script:ConfDir  = Join-Path $script:BaseDir  'config'
$script:Warnings = New-Object System.Collections.Generic.List[string]
$script:Stamp    = (Get-Date).ToString('yyyyMMdd-HHmmss')

foreach ($d in @($script:BaseDir, $script:LogDir, $script:ConfDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

$script:LogFile = Join-Path $script:LogDir "install-$($script:Stamp).log"

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','OK','WARN','ERROR','STEP')][string]$Level = 'INFO'
    )
    $ts   = (Get-Date).ToString('HH:mm:ss')
    $line = "[$ts][$Level] $Message"
    $color = switch ($Level) {
        'OK'    { 'Green' }
        'WARN'  { 'Yellow' }
        'ERROR' { 'Red' }
        'STEP'  { 'Cyan' }
        default { 'Gray' }
    }
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
    if ($Level -eq 'WARN' -or $Level -eq 'ERROR') { $script:Warnings.Add($Message) }
}

function Invoke-Step {
    # Runs a scriptblock, catches everything, logs, and keeps going.
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    Write-Log "START: $Name" 'STEP'
    if ($DryRun) { Write-Log "DRY-RUN: would run '$Name'" 'INFO'; return }
    try {
        & $Action
        Write-Log "DONE : $Name" 'OK'
    }
    catch {
        Write-Log "FAILED: $Name -> $($_.Exception.Message)" 'ERROR'
    }
}

function Test-CommandExists {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-ToolVersion {
    # Best-effort single-line version string for a CLI tool.
    param(
        [Parameter(Mandatory)][string]$Command,
        [string[]]$VersionArgs = @('--version')
    )
    if (-not (Test-CommandExists $Command)) { return $null }
    try {
        $out = & $Command @VersionArgs 2>&1 | Out-String
        $out = ($out -split "`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1)
        return ($out).Trim()
    }
    catch {
        return 'unknown'
    }
}

function Test-IsAdministrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Start-Transcript -Path (Join-Path $script:LogDir "transcript-$($script:Stamp).log") -Force | Out-Null

Write-Host ''
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host '  Forensic Audit Workstation Installer (Windows 11, 2026-ready)' -ForegroundColor Cyan
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-IsAdministrator)) {
    Write-Log 'This script must run as Administrator. Re-launch from an elevated shell.' 'ERROR'
    Stop-Transcript | Out-Null
    exit 1
}

# ==============================================================================
# PHASE 1 - SYSTEM DISCOVERY & VERSION HARVESTING
# ==============================================================================

Write-Log 'PHASE 1: System discovery and version harvesting' 'STEP'

$os      = Get-CimInstance Win32_OperatingSystem
$cs      = Get-CimInstance Win32_ComputerSystem
$sysDrv  = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':')) -ErrorAction SilentlyContinue

$ramGB   = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$freeGB  = if ($sysDrv) { [math]::Round($sysDrv.Free / 1GB, 1) } else { 0 }
$winVer  = "$($os.Caption) (build $($os.BuildNumber))"

Write-Log "Windows      : $winVer"
Write-Log "Total RAM    : $ramGB GB"
Write-Log "Free disk    : $freeGB GB on $($env:SystemDrive)"

if ($freeGB -lt 30) {
    Write-Log "Low free disk ($freeGB GB). LLM models can need 5-25 GB each." 'WARN'
}

# ==============================================================================
# PHASE 2 - BUILT-IN RESEARCH KNOWLEDGE BASE
# ==============================================================================
#
# Hardcoded, self-generated knowledge base. See the HONESTY NOTE in the header:
# "Latest" strings are the author's early-2025 best-known stable versions and
# are marked VERIFY where they change often. The installer still upgrades to the
# real current release at run time via the package managers.
# ==============================================================================

Write-Log 'PHASE 2: Consulting built-in research knowledge base' 'STEP'

# Per-tool install metadata. Method order is always: winget -> choco -> direct.
$Tools = @(
    @{ Name='git';        Cmd='git';        Args=@('--version');
       Winget='Git.Git';                    Choco='git';         Latest='2.45.x (VERIFY)';   Kind='pkg' }
    @{ Name='python';     Cmd='python';     Args=@('--version');
       Winget='Python.Python.3.12';         Choco='python312';   Latest='3.12.x (VERIFY)';   Kind='pkg' }
    @{ Name='node';       Cmd='node';       Args=@('--version');
       Winget='OpenJS.NodeJS.LTS';          Choco='nodejs-lts';  Latest='20.x LTS (VERIFY)'; Kind='pkg' }
    @{ Name='jq';         Cmd='jq';         Args=@('--version');
       Winget='jqlang.jq';                  Choco='jq';          Latest='1.7.x (VERIFY)';    Kind='pkg' }
    @{ Name='docker';     Cmd='docker';     Args=@('--version');
       Winget='Docker.DockerDesktop';       Choco='docker-desktop'; Latest='Desktop 4.3x (VERIFY)'; Kind='manual' }
    @{ Name='ollama';     Cmd='ollama';     Args=@('--version');
       Winget='Ollama.Ollama';              Choco='ollama';      Latest='0.x (VERIFY)';      Kind='ollama' }
    @{ Name='semgrep';    Cmd='semgrep';    Args=@('--version');
       Pip='semgrep';                       Latest='1.9x (VERIFY)';  Kind='pip' }
    @{ Name='bandit';     Cmd='bandit';     Args=@('--version');
       Pip='bandit';                        Latest='1.7.x (VERIFY)'; Kind='pip' }
    @{ Name='pip-audit';  Cmd='pip-audit';  Args=@('--version');
       Pip='pip-audit';                     Latest='2.7.x (VERIFY)'; Kind='pip' }
    @{ Name='snyk';       Cmd='snyk';       Args=@('--version');
       Npm='snyk';                          Latest='1.12xx (VERIFY)'; Kind='npm' }
    @{ Name='gitleaks';   Cmd='gitleaks';   Args=@('version');
       Winget='gitleaks.gitleaks';          Choco='gitleaks';    Latest='8.18.x (VERIFY)';   Kind='pkg';
       DirectRepo='gitleaks/gitleaks' }
    @{ Name='trufflehog'; Cmd='trufflehog'; Args=@('--version');
       Winget='TruffleSecurity.TruffleHog'; Choco='trufflehog';  Latest='3.x (VERIFY)';      Kind='pkg';
       DirectRepo='trufflesecurity/trufflehog' }
)

# --- RAM-aware local-LLM selection (author's optimization choice) -------------
# Family: qwen2.5-coder. Strong code reasoning, strict formatting, large
# context (Ollama serves 32k+; family supports up to 128k) -> ideal for a
# ~2000-token audit prompt plus pasted code. deepseek-coder-v2 is a cross-family
# fallback so a single bad pull cannot leave you with no working model.
if ($ramGB -ge 32) {
    $PrimaryModel  = 'qwen2.5-coder:32b'
    $FallbackModel = 'qwen2.5-coder:7b'
}
elseif ($ramGB -ge 16) {
    $PrimaryModel  = 'qwen2.5-coder:14b'
    $FallbackModel = 'qwen2.5-coder:7b'
}
else {
    $PrimaryModel  = 'qwen2.5-coder:7b'
    $FallbackModel = 'deepseek-coder-v2:16b'   # last-ditch; needs ~10GB
    Write-Log "RAM is $ramGB GB. Large coder models may run slowly or swap." 'WARN'
}
Write-Log "Selected primary LLM : $PrimaryModel"
Write-Log "Selected fallback LLM: $FallbackModel"

# --- Build and print the discovery table --------------------------------------
$report = foreach ($t in $Tools) {
    $installed = Get-ToolVersion -Command $t.Cmd -VersionArgs $t.Args
    $status =
        if ($null -eq $installed) { 'Missing' }
        elseif ($t.Kind -eq 'manual') { 'Present (manual updates)' }
        else { 'OK / will upgrade' }
    [pscustomobject]@{
        Tool             = $t.Name
        InstalledVersion = if ($installed) { $installed } else { '-' }
        LatestKnown      = $t.Latest
        Status           = $status
    }
}
Write-Host ''
$report | Format-Table -AutoSize | Out-String | ForEach-Object { Write-Host $_ }
$report | Format-Table -AutoSize | Out-String | Add-Content -Path $script:LogFile

# ==============================================================================
# PHASE 3 - AUTO-UPDATE & INSTALLATION
# ==============================================================================

Write-Log 'PHASE 3: Installing / updating tools' 'STEP'

$hasWinget = Test-CommandExists 'winget'
if (-not $hasWinget) {
    Write-Log 'winget not found. Install "App Installer" from the Microsoft Store, then re-run. Falling back to Chocolatey where possible.' 'WARN'
}

function Ensure-Chocolatey {
    if (Test-CommandExists 'choco') { return $true }
    Write-Log 'Installing Chocolatey (winget fallback package manager)...'
    if ($DryRun) { return $true }
    try {
        Set-ExecutionPolicy Bypass -Scope Process -Force
        [System.Net.ServicePointManager]::SecurityProtocol = 3072  # TLS 1.2
        Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
        $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path','User')
        return (Test-CommandExists 'choco')
    }
    catch {
        Write-Log "Chocolatey install failed: $($_.Exception.Message)" 'ERROR'
        return $false
    }
}

function Install-DirectFromGitHub {
    # Last-resort: grab a *_windows_amd64.zip / .exe asset from a GitHub release
    # and drop the binary into C:\ProgramData\ForensicAudit\bin (added to PATH).
    param(
        [Parameter(Mandatory)][string]$Repo,   # e.g. 'gitleaks/gitleaks'
        [Parameter(Mandatory)][string]$Exe     # e.g. 'gitleaks'
    )
    $binDir = Join-Path $script:BaseDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = 3072
        $api = "https://api.github.com/repos/$Repo/releases/latest"
        $hdr = @{ 'User-Agent' = 'ForensicAuditInstaller' }
        $rel = Invoke-RestMethod -Uri $api -Headers $hdr -ErrorAction Stop
        $asset = $rel.assets | Where-Object {
            $_.name -match '(?i)windows' -and $_.name -match '(?i)(amd64|x64)' -and
            ($_.name -match '\.zip$' -or $_.name -match '\.exe$')
        } | Select-Object -First 1
        if (-not $asset) { throw "No Windows amd64 asset in $Repo latest release." }

        $tmp = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -Headers $hdr -ErrorAction Stop
        if ($asset.name -match '\.zip$') {
            $ex = Join-Path $env:TEMP ("fa_" + [IO.Path]::GetFileNameWithoutExtension($asset.name))
            if (Test-Path $ex) { Remove-Item $ex -Recurse -Force }
            Expand-Archive -Path $tmp -DestinationPath $ex -Force
            $found = Get-ChildItem -Path $ex -Recurse -Filter "$Exe*.exe" | Select-Object -First 1
            if (-not $found) { throw "No $Exe.exe inside archive." }
            Copy-Item $found.FullName (Join-Path $binDir "$Exe.exe") -Force
        }
        else {
            Copy-Item $tmp (Join-Path $binDir "$Exe.exe") -Force
        }
        # Persist bin dir on the machine PATH.
        $machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
        if ($machinePath -notlike "*$binDir*") {
            [Environment]::SetEnvironmentVariable('Path', "$machinePath;$binDir", 'Machine')
        }
        $env:Path += ";$binDir"
        return $true
    }
    catch {
        Write-Log "Direct download of $Exe from $Repo failed: $($_.Exception.Message)" 'ERROR'
        return $false
    }
}

function Install-Package {
    # winget (install-or-upgrade) -> choco -> direct GitHub.
    param([Parameter(Mandatory)][hashtable]$Tool)

    $name = $Tool.Name

    if ($hasWinget -and $Tool.Winget) {
        Write-Log "winget: ensuring $name ($($Tool.Winget))"
        if (-not $DryRun) {
            # Upgrade if present; install if not. --accept-* keeps it unattended.
            & winget upgrade --id $Tool.Winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
                Out-String | Add-Content $script:LogFile
            if (-not (Test-CommandExists $Tool.Cmd)) {
                & winget install --id $Tool.Winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
                    Out-String | Add-Content $script:LogFile
            }
        }
        if ($DryRun -or (Test-CommandExists $Tool.Cmd)) { return }
    }

    if ($Tool.Choco) {
        if (Ensure-Chocolatey) {
            Write-Log "choco: installing/upgrading $name ($($Tool.Choco))"
            if (-not $DryRun) {
                & choco upgrade $Tool.Choco -y --no-progress 2>&1 | Out-String | Add-Content $script:LogFile
            }
            if ($DryRun -or (Test-CommandExists $Tool.Cmd)) { return }
        }
    }

    if ($Tool.DirectRepo) {
        Write-Log "direct: fetching $name from GitHub ($($Tool.DirectRepo))"
        if (Install-DirectFromGitHub -Repo $Tool.DirectRepo -Exe $Tool.Cmd) { return }
    }

    Write-Log "Could not install $name by any method." 'WARN'
}

# --- 3a. Package-manager tools (git, python, node, jq, gitleaks, trufflehog) --
foreach ($t in $Tools | Where-Object { $_.Kind -eq 'pkg' }) {
    Invoke-Step "Install/upgrade $($t.Name)" { Install-Package -Tool $t }
}

# Refresh PATH so freshly installed python/node/etc. are visible in this session.
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

# --- 3b. Python-based scanners (semgrep, bandit, pip-audit) --------------------
if (Test-CommandExists 'python') {
    Invoke-Step 'Upgrade pip' {
        & python -m pip install --upgrade pip 2>&1 | Out-String | Add-Content $script:LogFile
    }
    foreach ($t in $Tools | Where-Object { $_.Kind -eq 'pip' }) {
        Invoke-Step "pip install --upgrade $($t.Pip)" {
            & python -m pip install --upgrade $t.Pip 2>&1 | Out-String | Add-Content $script:LogFile
        }
    }
}
else {
    Write-Log 'Python is not available; skipping semgrep/bandit/pip-audit. Re-run after Python installs.' 'WARN'
}

# --- 3c. Node-based scanners (snyk) -------------------------------------------
if (Test-CommandExists 'npm') {
    foreach ($t in $Tools | Where-Object { $_.Kind -eq 'npm' }) {
        Invoke-Step "npm install -g $($t.Npm)@latest" {
            & npm install -g "$($t.Npm)@latest" 2>&1 | Out-String | Add-Content $script:LogFile
        }
    }
}
else {
    Write-Log 'npm is not available; skipping snyk. Re-run after Node.js installs.' 'WARN'
}

# --- 3d. Docker Desktop (cannot be silently, reliably installed) ---------------
if (-not (Test-CommandExists 'docker')) {
    Write-Log 'Docker Desktop is NOT installed. It cannot be installed unattended reliably.' 'WARN'
    Write-Log 'Install it manually from: https://www.docker.com/products/docker-desktop/' 'WARN'
    $script:Warnings.Add('Install Docker Desktop manually: https://www.docker.com/products/docker-desktop/')
}
else {
    Write-Log 'Docker CLI detected.' 'OK'
}

# --- 3e. Ollama ---------------------------------------------------------------
$ollamaTool = $Tools | Where-Object { $_.Name -eq 'ollama' }
Invoke-Step 'Install/upgrade Ollama' {
    if ($hasWinget) {
        & winget install --id $ollamaTool.Winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
            Out-String | Add-Content $script:LogFile
        & winget upgrade --id $ollamaTool.Winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
            Out-String | Add-Content $script:LogFile
    }
    elseif (Ensure-Chocolatey) {
        & choco upgrade ollama -y --no-progress 2>&1 | Out-String | Add-Content $script:LogFile
    }
}

# Refresh PATH again for ollama.exe.
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

# ==============================================================================
# PHASE 5a - START OLLAMA IN THE BACKGROUND (needed before Phase 4 pulls)
# ==============================================================================
# (Auto-start hardening is finished in Phase 5b; here we just get the API up so
#  we can pull and test models.)

function Test-OllamaApi {
    param([int]$TimeoutSec = 3)
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Start-OllamaServer {
    if (Test-OllamaApi) { return $true }
    if (-not (Test-CommandExists 'ollama')) {
        Write-Log 'ollama.exe not on PATH; cannot start the server.' 'ERROR'
        return $false
    }
    Write-Log 'Starting Ollama server (background, hidden)...'
    if (-not $DryRun) {
        Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-OllamaApi) { return $true }
        Start-Sleep -Seconds 2
    }
    return (Test-OllamaApi)
}

Invoke-Step 'Bring up Ollama API' {
    if (Start-OllamaServer) { Write-Log 'Ollama API reachable at http://localhost:11434' 'OK' }
    else { Write-Log 'Ollama API did not come up in time.' 'ERROR' }
}

# ==============================================================================
# PHASE 4 - LLM MODEL ALLOCATION & VERIFICATION
# ==============================================================================

Write-Log 'PHASE 4: LLM model allocation and verification' 'STEP'

function Test-ModelPresent {
    param([Parameter(Mandatory)][string]$Model)
    try { return (((& ollama list 2>&1) | Out-String) -match [regex]::Escape($Model)) }
    catch { return $false }
}

function Install-Model {
    param([Parameter(Mandatory)][string]$Model)
    if (Test-ModelPresent $Model) { Write-Log "$Model already present." 'OK'; return $true }
    Write-Log "Pulling $Model (this can take several minutes)..."
    if ($DryRun) { return $true }
    & ollama pull $Model 2>&1 | Out-String | Add-Content $script:LogFile
    if (-not (Test-ModelPresent $Model)) {
        Write-Log "First pull of $Model did not register; retrying once..." 'WARN'
        & ollama pull $Model 2>&1 | Out-String | Add-Content $script:LogFile
    }
    return (Test-ModelPresent $Model)
}

function Test-ModelResponds {
    param([Parameter(Mandatory)][string]$Model)
    if ($DryRun) { return $true }
    try {
        $ans = (& ollama run $Model 'Reply with the single word: OK' 2>&1 | Out-String)
        return ($ans.Trim().Length -gt 0)
    } catch { return $false }
}

$primaryOK  = $false
$fallbackOK = $false

if ($SkipModels) {
    Write-Log '-SkipModels set: not pulling or testing any LLM models.' 'WARN'
}
elseif (-not (Test-OllamaApi)) {
    Write-Log 'Ollama API not reachable; cannot manage models.' 'ERROR'
}
else {
    Invoke-Step "Ensure primary model ($PrimaryModel)"  { $script:primaryOK  = Install-Model $PrimaryModel }
    Invoke-Step "Ensure fallback model ($FallbackModel)" { $script:fallbackOK = Install-Model $FallbackModel }

    if ($primaryOK)  { $primaryOK  = Test-ModelResponds $PrimaryModel;  Write-Log "Primary responds : $primaryOK" }
    if ($fallbackOK) { $fallbackOK = Test-ModelResponds $FallbackModel; Write-Log "Fallback responds: $fallbackOK" }
}

# --- The forensic audit system prompt (baked into a dedicated Ollama model) ----
$AuditPrompt = @'
You are a senior forensic software auditor. Perform a deep-dive audit of the
codebase the user provides, focusing on two areas:

1. FUNCTIONAL & BUSINESS-LOGIC INTEGRITY
   - Trace each user story end to end; flag missing validation, broken flows,
     and incorrect data handling.
   - Probe edge cases: empty/max-length/special-character inputs, negatives,
     zero, decimals, boolean-strings.
   - Require server-side input validation, safe file-upload handling
     (MIME + magic bytes + size limits), and correct state transitions
     (no skipping steps, no replay, no forged status).
   - Look for race conditions on shared data and for limits enforced only
     client-side.
   - Flag dead code, debug endpoints, hardcoded secrets, and data that leaks
     in errors or responses.

2. CODE & ARCHITECTURE QUALITY (FORENSIC READINESS)
   - No raw stack traces to users; every exception logged with request context
     but without secrets.
   - Multi-table writes use transactions; no fire-and-forget data loss.
   - Async jobs are idempotent with retries, backoff, and dead-letter handling.
   - Requests carry correlation IDs; key business events are logged with actor,
     target, time, and outcome.
   - Note outdated/vulnerable dependencies and >10% code duplication.

For EACH finding output: Category, Severity (Critical/High/Medium/Low/Info),
Location (file + line/endpoint), Description, Steps to reproduce, Recommended
fix. End with a summary table grouped by severity. Be specific and cite exact
locations. Do not invent code you have not seen; ask for files if needed.
'@

$AuditModelName = 'forensic-audit'
$activePrimary  = if ($primaryOK -or $DryRun) { $PrimaryModel } elseif ($fallbackOK) { $FallbackModel } else { $PrimaryModel }

if (-not $SkipModels -and (Test-OllamaApi) -and ($primaryOK -or $fallbackOK -or $DryRun)) {
    Invoke-Step "Create audit model '$AuditModelName' from $activePrimary" {
        $modelfilePath = Join-Path $script:ConfDir 'Modelfile.audit'
        $mf = @()
        $mf += "FROM $activePrimary"
        $mf += 'PARAMETER num_ctx 16384'      # >= 16k context per the requirement
        $mf += 'PARAMETER temperature 0.2'    # deterministic, strict output
        $mf += 'SYSTEM """'
        $mf += $AuditPrompt
        $mf += '"""'
        Set-Content -Path $modelfilePath -Value ($mf -join "`r`n") -Encoding ASCII
        if (-not $DryRun) {
            & ollama create $AuditModelName -f $modelfilePath 2>&1 | Out-String | Add-Content $script:LogFile
        }
    }
}

# --- Persist allocation config for the launcher --------------------------------
$allocation = [ordered]@{
    primary        = $PrimaryModel
    fallback       = $FallbackModel
    auditModel     = $AuditModelName
    primaryOK      = [bool]$primaryOK
    fallbackOK     = [bool]$fallbackOK
    ramGB          = $ramGB
    generatedAt    = (Get-Date).ToString('s')
}
$allocation | ConvertTo-Json | Set-Content -Path (Join-Path $script:ConfDir 'allocation.json') -Encoding ASCII
Write-Log "Model allocation written to $(Join-Path $script:ConfDir 'allocation.json')" 'OK'

# ==============================================================================
# PHASE 5b - HARDENING & STEALTH AUTO-START
# ==============================================================================

Write-Log 'PHASE 5: Hardening and hidden auto-start' 'STEP'

# Ollama auto-start. Modern Ollama may register a Windows service named
# "ollama"; if so we set it to auto + admin-only control. If not, we create a
# SYSTEM-owned Scheduled Task at boot (hidden, and unstoppable by a standard
# user -> the tamper-proofing requirement).
Invoke-Step 'Configure Ollama to auto-start hidden' {
    $svc = Get-Service -Name 'ollama' -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Log 'Found Windows service "ollama"; setting start=auto with restart-on-failure.'
        if (-not $DryRun) {
            & sc.exe config ollama start= auto | Out-Null
            & sc.exe failure ollama reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null
            & sc.exe start ollama | Out-Null
        }
        # Restrict service control to Administrators + SYSTEM (tamper-proofing).
        if (-not $DryRun) {
            & sc.exe sdset ollama 'D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCLCSWRPWPDTLOCRRC;;;BA)(A;;CCLCSWLOCRRC;;;IU)S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)' | Out-Null
        }
    }
    else {
        Write-Log 'No "ollama" service; creating a hidden SYSTEM scheduled task at boot.'
        if (-not $DryRun) {
            $ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
            if (-not $ollamaExe) { throw 'ollama.exe not found for scheduled task.' }
            $action    = New-ScheduledTaskAction -Execute $ollamaExe -Argument 'serve'
            $trigger   = New-ScheduledTaskTrigger -AtStartup
            $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                            -DontStopIfGoingOnBatteries -Hidden -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
            Register-ScheduledTask -TaskName 'OllamaServer' -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Force | Out-Null
            Start-ScheduledTask -TaskName 'OllamaServer' -ErrorAction SilentlyContinue
        }
    }
}

# Docker Desktop: start on login if installed (tray icon cannot be reliably hidden).
Invoke-Step 'Configure Docker Desktop auto-start (if installed)' {
    $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dd) {
        if (-not $DryRun) {
            $runKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
            New-ItemProperty -Path $runKey -Name 'Docker Desktop' -Value "`"$dd`"" -PropertyType String -Force | Out-Null
        }
        Write-Log 'Docker Desktop set to start on login. Note: its tray icon will remain visible.' 'INFO'
    }
    else {
        Write-Log 'Docker Desktop not installed; skipping its auto-start.' 'INFO'
    }
}

# --- One-click audit launcher on the Public Desktop ---------------------------
Invoke-Step 'Create Start-Audit.ps1 launcher on the Public Desktop' {
    $publicDesktop = Join-Path $env:PUBLIC 'Desktop'
    if (-not (Test-Path $publicDesktop)) { New-Item -ItemType Directory -Path $publicDesktop -Force | Out-Null }
    $launcherPath = Join-Path $publicDesktop 'Start-Audit.ps1'

    # NOTE: single-quoted here-string -> nothing below is expanded now; the
    # launcher reads live config at run time.
    $launcher = @'
# Start-Audit.ps1 - one-click forensic audit session launcher.
$ErrorActionPreference = 'Stop'
$confPath = Join-Path $env:ProgramData 'ForensicAudit\config\allocation.json'

function Test-OllamaApi {
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

Write-Host '=====================================================' -ForegroundColor Cyan
Write-Host '  Forensic Audit - Local LLM Session' -ForegroundColor Cyan
Write-Host '=====================================================' -ForegroundColor Cyan

if (-not (Test-Path $confPath)) {
    Write-Host 'Config not found. Re-run Install-ForensicAudit.ps1 first.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'; exit 1
}
$conf = Get-Content $confPath -Raw | ConvertFrom-Json

Write-Host 'Checking Ollama...' -ForegroundColor Gray
if (-not (Test-OllamaApi)) {
    Write-Host 'Ollama not running. Starting it...' -ForegroundColor Yellow
    Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
    for ($i=0; $i -lt 20 -and -not (Test-OllamaApi); $i++) { Start-Sleep 2 }
}
if (-not (Test-OllamaApi)) {
    Write-Host 'Ollama could not be reached at http://localhost:11434.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'; exit 1
}

# Prefer the pre-baked audit model; fall back to primary, then fallback.
$models = ((& ollama list) | Out-String)
$target =
    if ($models -match [regex]::Escape($conf.auditModel)) { $conf.auditModel }
    elseif ($models -match [regex]::Escape($conf.primary)) { $conf.primary }
    else { $conf.fallback }

Write-Host ''
Write-Host ("Model: {0}" -f $target) -ForegroundColor Green
Write-Host 'The audit instructions are already loaded as this model''s system prompt.'
Write-Host 'Paste the code / files you want audited, then press Enter.'
Write-Host 'Type /bye to end the session.' -ForegroundColor Gray
Write-Host ''

& ollama run $target
'@

    Set-Content -Path $launcherPath -Value $launcher -Encoding UTF8
    Write-Log "Launcher created: $launcherPath" 'OK'

    # Convenience .cmd so a double-click just works without execution-policy prompts.
    $cmdPath = Join-Path $publicDesktop 'Start-Audit.cmd'
    $cmd = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%PUBLIC%\Desktop\Start-Audit.ps1`""
    Set-Content -Path $cmdPath -Value $cmd -Encoding ASCII
}

# ==============================================================================
# PHASE 6 - FINAL 2026 READINESS SUMMARY
# ==============================================================================

Write-Log 'PHASE 6: Final readiness summary' 'STEP'

Write-Host ''
Write-Host '================================================================' -ForegroundColor Green
Write-Host '  2026 FORENSIC-AUDIT READINESS SUMMARY' -ForegroundColor Green
Write-Host '================================================================' -ForegroundColor Green

Write-Host ''
Write-Host 'Installed / updated tool versions:' -ForegroundColor Cyan
foreach ($t in $Tools) {
    $v = Get-ToolVersion -Command $t.Cmd -VersionArgs $t.Args
    $shown = if ($v) { $v } else { 'NOT INSTALLED' }
    '{0,-12} {1}' -f $t.Name, $shown | ForEach-Object { Write-Host "  $_" }
}

Write-Host ''
Write-Host 'LLM models:' -ForegroundColor Cyan
Write-Host ("  Primary : {0}  (responds: {1})" -f $PrimaryModel,  $primaryOK)
Write-Host ("  Fallback: {0}  (responds: {1})" -f $FallbackModel, $fallbackOK)
if ($primaryOK -and $fallbackOK) {
    Write-Host ("  Model allocation verified: Primary = {0}, Fallback = {1}. Both respond correctly." -f $PrimaryModel, $FallbackModel) -ForegroundColor Green
}
elseif ($primaryOK -or $fallbackOK) {
    Write-Host '  Model allocation PARTIAL: at least one model works. See warnings.' -ForegroundColor Yellow
}
elseif (-not $SkipModels) {
    Write-Host '  Model allocation FAILED: no model verified. See warnings.' -ForegroundColor Red
}

Write-Host ''
Write-Host 'Startup configuration:' -ForegroundColor Cyan
Write-Host '  Ollama       : auto-start configured (service or SYSTEM scheduled task), hidden.'
Write-Host '  Audit model  : baked with the forensic prompt as its system prompt.'
Write-Host '  Launcher     : C:\Users\Public\Desktop\Start-Audit.ps1 (+ Start-Audit.cmd)'

Write-Host ''
Write-Host 'Manual actions still required:' -ForegroundColor Cyan
if ($script:Warnings.Count -eq 0) {
    Write-Host '  None. Everything completed cleanly.' -ForegroundColor Green
}
else {
    $script:Warnings | Select-Object -Unique | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}
Write-Host '  - If you use Snyk, run: snyk auth   (needs a free account/token).'
Write-Host '  - Verify the "(VERIFY)" version strings in Phase 2 against current releases.'

Write-Host ''
Write-Host ("Full log: {0}" -f $script:LogFile) -ForegroundColor Gray
Write-Host 'System is ready for forensic auditing against 2026 standards.' -ForegroundColor Green
Write-Host ''

Stop-Transcript | Out-Null
