[CmdletBinding()]
param(
  [string]$LemonadeBaseUrl = 'http://127.0.0.1:13305/api/v1',
  [string]$LemonadeModel = 'AUTO-DETECT'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 20 or later is required before starting Orivellum Forge Website Factory.' }
$env:LEMONADE_BASE_URL = $LemonadeBaseUrl
$env:LEMONADE_MODEL = $LemonadeModel
Set-Location $root
node .\src\server.mjs
