param([switch]$NoBrowser, [switch]$Prepare)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($Prepare -or !(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend\dist\index.html'))) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
$env:PYTHONUTF8 = '1'
# Read only in this process; never print the key, write it to disk, or expose it to frontend tools.
$userKey = [Environment]::GetEnvironmentVariable('AIHUBMIX_API_KEY', 'User')
if (![string]::IsNullOrWhiteSpace($userKey)) { $env:AIHUBMIX_API_KEY = $userKey }
$userKey = $null
Write-Host '知序本机版: http://127.0.0.1:8000 · 按 Ctrl+C 停止服务'
if ([string]::IsNullOrWhiteSpace($env:AIHUBMIX_API_KEY)) {
    Write-Host '未配置生成模型密钥。可使用本地证据摘录，AI 问答会显示明确状态。'
} else { Write-Host '后端已读取生成模型密钥（不显示密钥值）。' }
if (!$NoBrowser) { Start-Process 'http://127.0.0.1:8000' }
& $pythonPath -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
if ($LASTEXITCODE -ne 0) { throw '服务启动失败，请检查端口是否已被占用。' }
