$ErrorActionPreference = 'Stop'
$secret = Read-Host '设置 Windows 当前用户的 AIHUBMIX_API_KEY（输入不会显示）' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw '密钥不能为空。' }
    [Environment]::SetEnvironmentVariable('AIHUBMIX_API_KEY', $plain, 'User')
    Write-Host '已设置用户级环境变量。重启 start.ps1 后生效。'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $plain = $null
    $secret.Dispose()
}
