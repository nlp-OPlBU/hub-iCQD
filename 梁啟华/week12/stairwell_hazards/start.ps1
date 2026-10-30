$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env，请先填写 OPENAI_BASE_URL 与 OPENAI_API_KEY。" -ForegroundColor Yellow
}

python app.py
