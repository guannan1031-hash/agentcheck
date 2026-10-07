$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "尚未准备环境。请先运行 .\scripts\setup_windows.ps1。"
}

& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "后端测试失败。" }

if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
} elseif (-not (Test-Path "frontend\dist\index.html")) {
    throw "没有 npm，也没有已构建前端。"
}

Write-Host "验证完成。"
