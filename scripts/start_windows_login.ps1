param(
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

$Arguments = @("scripts/start_with_login.py")
foreach ($Value in $Account) {
    $Arguments += @("--account", $Value)
}
Write-Host "请为每个本地演示账号设置本次运行口令。口令不会写入磁盘。"
& $Python @Arguments
