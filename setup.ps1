param([switch]$SkipModels)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts\runtime.ps1')
$env:PYTHONUTF8 = '1'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) {
    $systemPython = Find-KbPython
    & $systemPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 虚拟环境创建失败。' }
}
& $pythonPath -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
$nodePath = Find-KbNode
$npmPath = Find-KbNpm $nodePath
$previousPath = $env:Path
$env:Path = (Split-Path -Parent $nodePath) + ';' + $env:Path
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
try {
    & $nodePath $npmPath ci
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    & $nodePath $npmPath run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
} finally { Pop-Location; $env:Path = $previousPath }
if (!$SkipModels) { & (Join-Path $PSScriptRoot 'scripts\prepare-models.ps1') }
Write-Host '环境准备完成。运行 .\start.ps1，首次进入页面创建管理员。'
