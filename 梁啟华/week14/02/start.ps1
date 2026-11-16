# ============================================
# 多模态 RAG 问答系统 - Windows 启动脚本
# ============================================
#
# 前提条件：
#   1. Python 3.10+ 已安装
#   2. Kafka 已启动（localhost:9092）
#   3. 环境变量 DASHSCOPE_API_KEY 已设置
#      （或者在 config/config.yaml 里直接填入）
#
# 启动步骤：
#   双击运行，或在 PowerShell 中执行：
#       .\start.ps1
#

$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  多模态 RAG 问答系统 - 启动脚本" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -------- 检查 Python --------
Write-Host "[1/4] 检查 Python 环境..." -ForegroundColor Yellow
$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $pyVer = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = $cmd
            Write-Host "    ✓ $pyVer" -ForegroundColor Green
            break
        }
    } catch { }
}
if (-not $pythonCmd) {
    Write-Host "    ✗ 未找到 Python" -ForegroundColor Red
    Write-Host "    请安装 Python 3.10+ 并添加到 PATH" -ForegroundColor Red
    exit 1
}

# -------- 安装依赖 --------
Write-Host ""
Write-Host "[2/4] 检查依赖..." -ForegroundColor Yellow
$backendDir = Join-Path $projectRoot "backend"
$reqFile = Join-Path $backendDir "requirements.txt"

$venvDir = Join-Path $projectRoot "venv"
$useVenv = $true

if (Test-Path $venvDir) {
    Write-Host "    使用已有虚拟环境" -ForegroundColor Green
    $activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        . $activateScript
    }
} else {
    Write-Host "    创建虚拟环境..." -ForegroundColor Gray
    & $pythonCmd -m venv $venvDir 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
    $activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        . $activateScript
    }
    $pythonCmd = "python"
}

# 安装 pip 依赖
Write-Host "    安装 pip 依赖（首次运行可能较慢）..." -ForegroundColor Gray
& $pythonCmd -m pip install --upgrade pip 2>&1 | Out-Null
& $pythonCmd -m pip install -r $reqFile 2>&1 | ForEach-Object {
    # 只显示关键信息
    if ($_ -match "Successfully|Requirement already") {
        Write-Host "    $_" -ForegroundColor Green
    } elseif ($_ -match "ERROR") {
        Write-Host "    $_" -ForegroundColor Red
    }
}

# -------- 环境变量提示 --------
Write-Host ""
Write-Host "[3/4] 检查配置..." -ForegroundColor Yellow
if (-not $env:DASHSCOPE_API_KEY) {
    Write-Host "    ⚠ DASHSCOPE_API_KEY 未设置" -ForegroundColor Yellow
    Write-Host "    MinerU 解析仍可用（降级到 PyMuPDF）" -ForegroundColor Gray
    Write-Host "    LLM 问答需要有效的 API Key" -ForegroundColor Gray
} else {
    Write-Host "    ✓ API Key 已配置" -ForegroundColor Green
}

# -------- 启动服务 --------
Write-Host ""
Write-Host "[4/4] 启动后端服务..." -ForegroundColor Yellow
Write-Host ""
Write-Host "    后端:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "    API:   http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "    按 Ctrl+C 停止服务" -ForegroundColor Gray
Write-Host ""

Set-Location $backendDir
& $pythonCmd run.py
