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

New-Item -ItemType Directory -Force -Path "state\local" | Out-Null
Write-Host "正在启动：http://127.0.0.1:8878"
Write-Host "此模式只监听本机；按 Ctrl+C 停止。"
& $Python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8878 --workers 1 --no-access-log
