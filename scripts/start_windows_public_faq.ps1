$ErrorActionPreference = "Stop"
$env:CS_PUBLIC_FAQ_ENABLED = "true"
try {
    & "$PSScriptRoot\start_windows.ps1"
} finally {
    Remove-Item Env:CS_PUBLIC_FAQ_ENABLED -ErrorAction SilentlyContinue
}
