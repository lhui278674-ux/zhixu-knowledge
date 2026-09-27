$ErrorActionPreference = 'Stop'
function Find-KbPython {
    $candidates = @()
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    throw '需要 Python 3.12 或更新版本，请安装后重试。'
}
function Find-KbNode {
    $candidates = @()
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -e 'process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    throw '需要 Node.js 20 或更新版本，请安装后重试。'
}
function Find-KbNpm($NodePath) {
    $parent = Split-Path -Parent $NodePath
    $candidates = @((Join-Path $parent 'node_modules\npm\bin\npm-cli.js'), (Join-Path (Split-Path -Parent $parent) 'node_modules\npm\bin\npm-cli.js'))
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) { $candidates += (Join-Path (Split-Path -Parent $npm.Source) 'node_modules\npm\bin\npm-cli.js') }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    throw '未找到 npm，请使用含 npm 的 Node.js 安装。'
}
