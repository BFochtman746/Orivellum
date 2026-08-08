[CmdletBinding(SupportsShouldProcess)]
param(
  [switch]$Install,
  [switch]$InstallPlaywrightBrowsers
)

$ErrorActionPreference = 'Stop'
Write-Host 'This script never installs automatically unless -Install is supplied.'
$checks = @('node', 'npm', 'git', 'python', 'pipx', 'semgrep', 'gitleaks', 'osv-scanner') | ForEach-Object {
  $item = Get-Command $_ -ErrorAction SilentlyContinue
  [PSCustomObject]@{ Command = $_; Available = [bool]$item; Path = if ($item) { $item.Source } else { '' } }
}
$checks | Format-Table -AutoSize
if (-not $Install) { return }

if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) { throw 'Install pipx first, then re-run this script with -Install.' }
if ($PSCmdlet.ShouldProcess('Semgrep CE', 'Install')) { pipx install semgrep }
if (-not (Get-Command gitleaks -ErrorAction SilentlyContinue)) { Write-Warning 'Install Gitleaks from its signed official release or an approved package manager, then re-run this check.' }
if (-not (Get-Command osv-scanner -ErrorAction SilentlyContinue)) { Write-Warning 'Install OSV-Scanner from its official release or an approved package manager, then re-run this check.' }
if ($InstallPlaywrightBrowsers) { Write-Warning 'Run the following only within an approved disposable website worktree: npm install --save-dev @playwright/test ; npx playwright install' }
