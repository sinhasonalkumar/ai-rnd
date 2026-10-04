# Windows: start the app at http://localhost:8000
Set-Location "$PSScriptRoot\backend"
Write-Host "Open http://localhost:8000"
..\.venv\Scripts\uvicorn app:app --host 127.0.0.1 --port 8000
