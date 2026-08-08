# register-boot.ps1
# Run this ONCE (as Administrator) to register the Orivellum boot task.
# After registration, orivellum-boot.ps1 runs automatically at every login.
#
# Usage:
#   Right-click PowerShell -> Run as Administrator
#   cd C:\OrivellumAppliance
#   .\register-boot.ps1

param(
    [string]$BootScript = "C:\OrivellumAppliance\orivellum-boot.ps1",
    [string]$TaskName   = "OrivellumAppliance"
)

if (-not (Test-Path $BootScript)) {
    Write-Error "Boot script not found: $BootScript`nCopy orivellum-boot.ps1 there first."
    exit 1
}

# Wrap in a hidden VBScript launcher so no console window flashes at login
$VbsPath = "C:\OrivellumAppliance\orivellum-boot.vbs"
@"
Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File ""$BootScript""", 0, False
"@ | Set-Content -Path $VbsPath -Encoding UTF8

# Register the scheduled task
$action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# Run as current user (required for WSL / rootless podman)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ""
Write-Host "OK Task '$TaskName' registered successfully." -ForegroundColor Green
Write-Host ""
Write-Host "What happens now:"
Write-Host "  - On every login, orivellum-boot.ps1 runs silently in the background"
Write-Host "  - ComfyUI starts, Orivellum starts  -  all automatic"
Write-Host "  - Boot log: C:\OrivellumAppliance\logs\"
Write-Host ""
Write-Host "To enable auto-login (so this works after a power cut with no keyboard):"
Write-Host "  1. Win+R -> netplwiz"
Write-Host "  2. Uncheck 'Users must enter a user name and password'"
Write-Host "  3. Enter your password when prompted"
Write-Host "  4. OK  -  Windows will log in automatically from now on"
Write-Host ""
Write-Host "To test right now without rebooting:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
