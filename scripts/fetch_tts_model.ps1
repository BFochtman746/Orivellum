# Fetch the Kokoro neural TTS model assets (one-time setup, ~340 MB total).
#
# These are NOT bundled in git (the ONNX model is too large and Git LFS proved
# unreliable — clones used to receive a 134-byte pointer file and silently lose
# neural TTS). Run this once from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\fetch_tts_model.ps1
$ErrorActionPreference = "Stop"

$BaseUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files"
$DestDir = Split-Path -Parent $PSScriptRoot   # repo root

function Fetch-Asset([string]$Name, [long]$MinBytes) {
    $dest = Join-Path $DestDir $Name
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -ge $MinBytes)) {
        Write-Host ("OK  {0} already present ({1:N0} MB)" -f $Name, ((Get-Item $dest).Length / 1MB))
        return
    }
    Write-Host "Downloading $Name ..."
    $tmp = "$dest.part"
    Invoke-WebRequest -Uri "$BaseUrl/$Name" -OutFile $tmp -UseBasicParsing
    $size = (Get-Item $tmp).Length
    if ($size -lt $MinBytes) {
        Remove-Item $tmp -Force
        throw "$Name downloaded only $size bytes (expected >= $MinBytes) - aborting."
    }
    Move-Item $tmp $dest -Force
    Write-Host ("OK  {0} downloaded ({1:N0} MB)" -f $Name, ($size / 1MB))
}

# Sizes are sanity floors, not exact: model ~325 MB, voices ~27 MB.
Fetch-Asset "kokoro-v0_19.onnx" 300000000
Fetch-Asset "voices.bin"        20000000

Write-Host "Done. Neural TTS assets are in place."
