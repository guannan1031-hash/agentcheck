param(
    [switch]$EnablePublicFaq,
    [switch]$WithZhipu,
    [string[]]$Account = @("owner:主管", "service:客服")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "尚未准备环境。请先运行 .\scripts\setup_windows.ps1。"
}
if (-not (Test-Path "frontend\dist\index.html")) {
    throw "前端尚未构建。请重新运行 .\scripts\setup_windows.ps1。"
}

$pointer = [IntPtr]::Zero
try {
    if ($EnablePublicFaq) {
        $env:CS_PUBLIC_FAQ_ENABLED = "true"
    }
    if ($WithZhipu) {
        $secret = Read-Host "请输入智谱 API Key（仅本次进程使用）" -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        Set-Item -Path "Env:CS_MODEL_API_KEY" -Value ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
        $env:CS_MODEL_PROVIDER = "zhipu"
        $env:CS_MODEL_NAME = "glm-4.7-flash"
    }
    $Arguments = @("scripts/start_with_login.py")
    foreach ($Value in $Account) {
        $Arguments += @("--account", $Value)
    }
    Write-Host "正在启动单店试点：http://127.0.0.1:8878"
    Write-Host "将为每个本地账号设置本次运行口令；按 Ctrl+C 停止。"
    & $Python @Arguments
} finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    Remove-Item Env:CS_PUBLIC_FAQ_ENABLED -ErrorAction SilentlyContinue
    Remove-Item Env:CS_MODEL_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:CS_MODEL_PROVIDER -ErrorAction SilentlyContinue
    Remove-Item Env:CS_MODEL_NAME -ErrorAction SilentlyContinue
    Remove-Variable secret -ErrorAction SilentlyContinue
}

