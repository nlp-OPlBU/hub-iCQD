# API development on Windows; Linux Worker requires flock/process-group support.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$PythonExe = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
Push-Location (Join-Path $ProjectRoot 'backend')
try { & $PythonExe -m app.main; exit $LASTEXITCODE } finally { Pop-Location }
