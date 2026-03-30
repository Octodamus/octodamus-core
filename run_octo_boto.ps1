# run_octo_boto.ps1 — OctoBoto Launcher
# Add to octo_unlock.ps1 boot sequence as step 5.5
#
# Prerequisites:
#   - OCTOBOTO_TELEGRAM_TOKEN in Bitwarden as:
#     "AGENT - Octodamus - OctoBoto - Telegram Token" (Password field)
#   - ANTHROPIC_API_KEY already in .octo_secrets from main bot unlock
#   - pip install python-telegram-bot anthropic requests numpy

$ErrorActionPreference = "Stop"
$OctoDir = "C:\Users\walli\octodamus"

Write-Host "[OctoBoto] Starting launcher..." -ForegroundColor Cyan

# Load secrets from cache file (written by octo_unlock.ps1)
$SecretsFile = Join-Path $OctoDir ".octo_secrets"
if (Test-Path $SecretsFile) {
    $secrets = Get-Content $SecretsFile -Raw | ConvertFrom-Json

    if ($secrets.ANTHROPIC_API_KEY) {
        $env:ANTHROPIC_API_KEY = $secrets.ANTHROPIC_API_KEY
        Write-Host "[OctoBoto] ANTHROPIC_API_KEY loaded from .octo_secrets"
    }

    if ($secrets.OCTOBOTO_TELEGRAM_TOKEN) {
        $env:OCTOBOTO_TELEGRAM_TOKEN = $secrets.OCTOBOTO_TELEGRAM_TOKEN
        Write-Host "[OctoBoto] OCTOBOTO_TELEGRAM_TOKEN loaded from .octo_secrets"
    } else {
        Write-Host "[OctoBoto] WARNING: OCTOBOTO_TELEGRAM_TOKEN not in .octo_secrets" -ForegroundColor Yellow
        Write-Host "[OctoBoto] Add it to Bitwarden and re-run octo_unlock.ps1" -ForegroundColor Yellow
    }
} else {
    Write-Host "[OctoBoto] WARNING: .octo_secrets not found. Run octo_unlock.ps1 first." -ForegroundColor Yellow
}

# Launch bot
Write-Host "[OctoBoto] Launching bot..."
Start-Process -FilePath "C:\Python314\python.exe" `
    -ArgumentList "$OctoDir\octo_boto.py" `
    -WorkingDirectory $OctoDir `
    -WindowStyle Hidden

Write-Host "[OctoBoto] Bot launched in background." -ForegroundColor Green
