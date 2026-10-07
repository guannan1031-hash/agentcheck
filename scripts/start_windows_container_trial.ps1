param(
    [ValidatePattern('^[a-z][a-z0-9-]{1,62}$')]
    [string]$TenantId = "local-trial",
    [ValidateRange(1024, 65535)]
    [int]$Port = 18878
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop 尚未就绪，请启动后重试。"
}

$servicePassword = Read-Host "设置本次容器试点客服口令（至少12位）" -AsSecureString
$leadPassword = Read-Host "设置本次容器试点主管口令（至少12位）" -AsSecureString
$databasePassword = Read-Host "设置本次容器 PostgreSQL 口令（至少12位）" -AsSecureString
$pointers = @()
$accountFile = $null
try {
    foreach ($secret in @($servicePassword, $leadPassword, $databasePassword)) {
        $pointers += [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    }
    $servicePlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointers[0])
    $leadPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointers[1])
    $databasePlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointers[2])
    if ($servicePlain.Length -lt 12 -or $leadPlain.Length -lt 12 -or $databasePlain.Length -lt 12) {
        throw "三项口令都至少需要12位；未启动容器。"
    }
    $accountFile = Join-Path $env:TEMP ("cs-auth-" + [guid]::NewGuid().ToString("N") + ".json")
    @{ service = @{ role = "客服"; tenant_id = $TenantId; password = $servicePlain }; lead = @{ role = "主管"; tenant_id = $TenantId; password = $leadPlain } } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $accountFile -Encoding utf8 -NoNewline
    $env:POSTGRES_PASSWORD = $databasePlain
    $env:CS_DEFAULT_TENANT_ID = $TenantId
    $env:CS_AUTH_ACCOUNTS_FILE = $accountFile
    $env:CS_APP_PORT = "$Port"
    $env:CS_DEPLOYMENT_MODE = "preproduction"
    $env:CS_COOKIE_SECURE = "false"
    $env:DOCKER_BUILDKIT = "0"
    Write-Host "正在启动本机容器试点：http://127.0.0.1:$Port"
    Write-Host "仅用于受控预发布验证；按 Ctrl+C 后将停止并清理容器与试点数据库。"
    docker compose -p cs-local-trial up --build
} finally {
    docker compose -p cs-local-trial down -v --remove-orphans 2>$null
    if ($accountFile) { Remove-Item -LiteralPath $accountFile -Force -ErrorAction SilentlyContinue }
    foreach ($pointer in $pointers) { if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) } }
    Remove-Item Env:POSTGRES_PASSWORD, Env:CS_DEFAULT_TENANT_ID, Env:CS_AUTH_ACCOUNTS_FILE, Env:CS_APP_PORT, Env:CS_DEPLOYMENT_MODE, Env:CS_COOKIE_SECURE, Env:DOCKER_BUILDKIT -ErrorAction SilentlyContinue
    Remove-Variable servicePlain, leadPlain, databasePlain -ErrorAction SilentlyContinue
}
