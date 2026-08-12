# orivellum-boot.ps1
# Orivellum Appliance Boot Script
# Place in C:\OrivellumAppliance\
# Run automatically at login via Task Scheduler (see register-boot.ps1)
#
# What this does, in order:
#   1. Starts the Ubuntu WSL distro if it isn't already running
#   2. Checks if ComfyUI (port 8188) is already answering  -  if yes, skips container start
#   3. If not up, starts the podman container using a login shell (fixes rootless-podman visibility)
#   4. Waits for ComfyUI to be healthy (up to 120 s)
#   5. Starts Orivellum via start.ps1 (or uv run ...) in a new window
#   6. Writes a boot log to C:\OrivellumAppliance\logs\

param(
    [string]$OrivellumPath  = "C:\Orivellum-main",
    [string]$WslDistro      = "Ubuntu-24.04",
    [string]$WslUser        = "bfoch",
    [string]$ContainerName  = "orivellum-comfyui",
    [int]   $ComfyPort      = 8188,
    [int]   $ComfyWaitSecs  = 120
)

$LogDir = "C:\OrivellumAppliance\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("boot-" + (Get-Date -Format "yyyy-MM-dd_HH-mm-ss") + ".log")

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Test-PortOpen([string]$host, [int]$port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $ar  = $tcp.BeginConnect($host, $port, $null, $null)
        $ok  = $ar.AsyncWaitHandle.WaitOne(1500, $false)
        $tcp.Close()
        return $ok
    } catch { return $false }
}

# -- Remove old logs (keep last 14) -------------------------------------------
Get-ChildItem $LogDir -Filter "boot-*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 |
    Remove-Item -Force -ErrorAction SilentlyContinue

Log "=== Orivellum Appliance Boot ==="
Log "Orivellum path : $OrivellumPath"
Log "WSL distro     : $WslDistro ($WslUser)"
Log "Container      : $ContainerName"

# -- Step 1: Wake WSL ---------------------------------------------------------
Log "Waking WSL distro..."
wsl -d $WslDistro -u $WslUser -- echo "WSL ready" 2>&1 | Out-Null
Start-Sleep -Seconds 2

# -- Step 2: Check if ComfyUI is already answering ----------------------------
Log "Checking ComfyUI on port $ComfyPort..."
if (Test-PortOpen "127.0.0.1" $ComfyPort) {
    Log "ComfyUI already up on localhost:$ComfyPort  -  skipping container start."
} else {
    # Detect WSL IP dynamically (handles reboots that change the address)
    $WslIp = (wsl -d $WslDistro -u $WslUser -- hostname -I 2>$null).Trim().Split()[0]
    Log "WSL IP: $WslIp"

    if ($WslIp -and (Test-PortOpen $WslIp $ComfyPort)) {
        Log "ComfyUI already up on $WslIp`:$ComfyPort  -  skipping container start."
    } else {
        # Step 3: Start container via login shell so rootless podman env is loaded
        Log "Starting $ContainerName via login shell..."
        $startOut = wsl -d $WslDistro -u $WslUser -- bash -lic "podman start $ContainerName 2>&1"
        Log "podman start: $startOut"

        # Step 4: Wait for ComfyUI to be healthy
        Log "Waiting for ComfyUI (up to ${ComfyWaitSecs}s)..."
        $waited = 0
        $up = $false
        while ($waited -lt $ComfyWaitSecs) {
            Start-Sleep -Seconds 3
            $waited += 3
            $checkIp = (wsl -d $WslDistro -u $WslUser -- hostname -I 2>$null).Trim().Split()[0]
            if (($checkIp -and (Test-PortOpen $checkIp $ComfyPort)) -or
                (Test-PortOpen "127.0.0.1" $ComfyPort)) {
                $up = $true
                break
            }
        }

        if ($up) {
            Log "ComfyUI is up. (${waited}s)"
        } else {
            Log "WARNING: ComfyUI did not respond within ${ComfyWaitSecs}s. Continuing anyway."
        }
    }
}

# -- Step 4b: Wire localhost:8188 -> WSL IP (optional portproxy) ---------------
# This makes localhost:8188 always reach ComfyUI so you never need to update
# the Orivellum System Settings URL when the WSL IP changes after a reboot.
# Requires the script to run with elevated rights OR to skip silently if not.
try {
    $currentIp = (wsl -d $WslDistro -u $WslUser -- hostname -I 2>$null).Trim().Split()[0]
    if ($currentIp) {
        # Remove old rule first (ignore errors if it didn't exist)
        netsh interface portproxy delete v4tov4 listenport=$ComfyPort listenaddress=127.0.0.1 2>$null | Out-Null
        $fwdResult = netsh interface portproxy add v4tov4 `
            listenport=$ComfyPort listenaddress=127.0.0.1 `
            connectport=$ComfyPort connectaddress=$currentIp protocol=tcp 2>&1
        Log "Port-forward localhost:$ComfyPort -> $currentIp`:$ComfyPort ($fwdResult)"
    }
} catch {
    Log "Port-forward skipped (needs elevation or not available): $_"
}

# -- Step 5: Auto-update Orivellum from GitHub (if it's a git repo) -----------
if (Test-Path (Join-Path $OrivellumPath ".git")) {
    Log "Updating Orivellum from GitHub..."
    try {
        Push-Location $OrivellumPath

        $before = git rev-parse HEAD 2>&1
        git fetch origin main 2>&1 | ForEach-Object { Log "git: $_" }
        git merge --ff-only origin/main 2>&1 | ForEach-Object { Log "git: $_" }
        $after = git rev-parse HEAD 2>&1

        if ($before -ne $after) {
            Log "Code updated ($before -> $after). Re-syncing dependencies..."

            # Re-sync Python deps in case pyproject.toml changed
            $uvExe = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" }
                     elseif (Test-Path "$env:USERPROFILE\.local\bin\uv.exe") { "$env:USERPROFILE\.local\bin\uv.exe" }
                     elseif (Test-Path "$env:APPDATA\uv\bin\uv.exe") { "$env:APPDATA\uv\bin\uv.exe" }
                     else { $null }
            if ($uvExe) {
                & $uvExe sync --python 3.12 2>&1 | ForEach-Object { Log "uv: $_" }
                Log "Python dependencies synced."
            } else {
                Log "WARNING: uv not found -- Python dependencies NOT synced. Run setup-windows.ps1 if the app fails to start."
            }

            # Rebuild the production UI bundle in case frontend changed
            $pnpmExe = if (Get-Command pnpm -ErrorAction SilentlyContinue) { "pnpm" }
                       elseif (Test-Path "$env:LOCALAPPDATA\pnpm\pnpm.exe") { "$env:LOCALAPPDATA\pnpm\pnpm.exe" }
                       elseif (Test-Path "$env:LOCALAPPDATA\pnpm\pnpm.cmd") { "$env:LOCALAPPDATA\pnpm\pnpm.cmd" }
                       else { $null }
            if ($pnpmExe) {
                & $pnpmExe install --frozen-lockfile 2>&1 | ForEach-Object { Log "pnpm: $_" }
                Log "Node dependencies synced. start.ps1 will rebuild the UI bundle on next launch."
            } else {
                Log "WARNING: pnpm not found -- Node dependencies NOT synced. Run setup-windows.ps1 if the UI fails to load."
            }
        } else {
            Log "Already up to date (no merge)."
        }

        Pop-Location
    } catch {
        Log "git update failed (non-fatal): $_"
    }
} else {
    Log "Skipping git update  -  not a git repo (tip: clone instead of unzipping)"
}

# -- Step 6: Start Orivellum ---------------------------------------------------
Log "Starting Orivellum..."
if (Test-Path (Join-Path $OrivellumPath "start.ps1")) {
    $startCmd = "Set-Location '$OrivellumPath'; .\start.ps1"
} else {
    # Fallback: invoke uv directly
    $startCmd = "Set-Location '$OrivellumPath'; uv run python -m orivellum.api.main"
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", $startCmd `
    -WorkingDirectory $OrivellumPath

Log "Orivellum window launched."
Log "=== Boot complete ==="
