param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "未找到 Python Launcher。请先安装 Python 3.12，并勾选 Add Python to PATH。"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 虚拟环境失败。" }
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw "安装 Python 依赖失败。" }

if (-not $SkipFrontendBuild) {
    if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
        & npm.cmd --prefix frontend ci
        if ($LASTEXITCODE -ne 0) { throw "安装前端依赖失败。" }
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
    } elseif (-not (Test-Path "frontend\dist\index.html")) {
        throw "未找到 npm，也没有已构建前端。请安装 Node.js LTS 后重新运行。"
    } else {
        Write-Host "未找到 npm；将使用发布包中已构建的前端。"
    }
}

New-Item -ItemType Directory -Force -Path "state\local" | Out-Null
Write-Host "环境准备完成。运行 .\scripts\start_windows.ps1 启动本地演示。"
