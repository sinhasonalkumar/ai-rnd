# Windows one-time setup.  Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1 "C:\path\Vedabase Original.zip"
param([string]$Zip = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path raw\index.json)) {
  if (-not $Zip) { Write-Host 'Usage: setup.ps1 "C:\path\Vedabase Original.zip"'; exit 1 }
  Expand-Archive -Force $Zip raw
  if (-not (Test-Path raw\index.json)) {
    $inner = (Get-ChildItem raw -Recurse -Filter index.json | Select-Object -First 1).DirectoryName
    Get-ChildItem $inner | Move-Item -Destination raw
  }
}
if (-not (Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\pip install -q --upgrade pip
.\.venv\Scripts\pip install -q -r backend\requirements.txt
Push-Location frontend; npm install --silent; npm run build --silent; Pop-Location
if (Get-Command ollama -ErrorAction SilentlyContinue) { ollama pull qwen2.5:1.5b } else { Write-Host "Install Ollama from https://ollama.com for AI answers." }
Push-Location backend; ..\.venv\Scripts\python indexer.py; Pop-Location
