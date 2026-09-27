$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先运行 setup.ps1 创建运行环境。' }
$env:PYTHONUTF8 = '1'
$env:HF_HUB_DISABLE_XET = '1'
& $pythonPath (Join-Path $PSScriptRoot 'prepare_models.py')
if ($LASTEXITCODE -ne 0) { throw '本地模型下载失败。请检查网络后重试，不会把文档发送至模型仓库。' }
