$ErrorActionPreference = "Stop"
$secret = Read-Host "请输入智谱 API Key（仅本次进程使用）" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    Set-Item -Path "Env:CS_MODEL_API_KEY" -Value ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
    $env:CS_MODEL_PROVIDER = "zhipu"
    $env:CS_MODEL_NAME = "glm-4.7-flash"
    & "$PSScriptRoot\start_windows.ps1"
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Item Env:CS_MODEL_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:CS_MODEL_PROVIDER -ErrorAction SilentlyContinue
    Remove-Item Env:CS_MODEL_NAME -ErrorAction SilentlyContinue
}
