# Orivellum backup script - snapshots live data to C:\Back_Up.
#
# Usage (from the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Destination D:\SomewhereElse
#
# Safe while the server is running: SQLite databases are copied with
# "VACUUM INTO" (online backup), never raw file copies of a live DB.
param(
    [string]$Destination = "C:\Back_Up",
    [int]$Keep = 8
)
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataDir  = Join-Path $RepoRoot "data"
if (-not (Test-Path $DataDir)) { throw "data\ directory not found at $DataDir - run from the repo root." }

$Stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$Target  = Join-Path $Destination "orivellum-$Stamp"
New-Item -ItemType Directory -Path (Join-Path $Target "data") -Force | Out-Null

# 1) Consistent SQLite snapshots via the project's Python (uv) and VACUUM INTO.
$DbFiles = @("orivellum.db", "atelier.db", "press.db")
foreach ($db in $DbFiles) {
    $src = Join-Path $DataDir $db
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path (Join-Path $Target "data") $db
    $srcPy = $src -replace '\\', '/'
    $dstPy = $dst -replace '\\', '/'
    $py = "import sqlite3; c = sqlite3.connect(r'$srcPy'); c.execute(""VACUUM INTO '$dstPy'""); c.close()"
    & uv run python -c $py
    if ($LASTEXITCODE -ne 0) { throw "SQLite backup failed for $db" }
    Write-Host "OK  $db snapshotted"
}

# 2) Library files + voice assets + config.
foreach ($dirName in @("library", "premium-voices")) {
    $src = Join-Path $DataDir $dirName
    if (Test-Path $src) {
        Copy-Item $src -Destination (Join-Path $Target "data") -Recurse
        Write-Host "OK  data\$dirName copied"
    }
}
$cfg = Join-Path $RepoRoot "config.yaml"
if (Test-Path $cfg) { Copy-Item $cfg -Destination $Target; Write-Host "OK  config.yaml copied" }

# 3) Rotation - keep the newest $Keep snapshots.
$old = Get-ChildItem $Destination -Directory -Filter "orivellum-*" |
       Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($o in $old) {
    Remove-Item $o.FullName -Recurse -Force
    Write-Host "Rotated out old snapshot: $($o.Name)"
}

Write-Host "Backup complete: $Target"
Write-Host "Reminder: secrets (SESSION_SECRET / ORIVELLUM_LOGIN_KEY) belong in a password manager, not in this folder."
