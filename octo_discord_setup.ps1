# octo_discord_setup.ps1
# Octodamus — Discord Webhook Setup
#
# Run once to store your Discord webhook URL in Bitwarden.
#
# Before running:
#   1. Open Discord → go to your server (or create one e.g. "Octodamus HQ")
#   2. Create a channel e.g. #x-posts
#   3. Channel Settings → Integrations → Webhooks → New Webhook
#   4. Copy the webhook URL
#   5. Run this script and paste it when prompted
#
# After running:
#   Run octo_unlock.ps1 again to refresh the secrets cache with the Discord webhook.

$PROJECT_DIR = "C:\Users\walli\octodamus"
$PYTHON      = "C:\Python314\python.exe"
$BW          = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"

Write-Host ""
Write-Host "🦑 OCTODAMUS DISCORD SETUP" -ForegroundColor Cyan
Write-Host "===========================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Paste your Discord webhook URL:" -ForegroundColor Yellow
$webhookUrl = Read-Host "Webhook URL"

if (-not $webhookUrl.StartsWith("https://discord.com/api/webhooks/")) {
    Write-Host "That doesn't look like a Discord webhook URL." -ForegroundColor Red
    Write-Host "Expected: https://discord.com/api/webhooks/..." -ForegroundColor Gray
    exit 1
}

# Test the webhook first
Write-Host ""
Write-Host "Testing webhook..." -ForegroundColor Yellow
$testPayload = '{"content": "🦑 Octodamus Discord connection test — working!"}'
try {
    $response = Invoke-RestMethod -Uri $webhookUrl -Method Post -ContentType "application/json" -Body $testPayload
    Write-Host "  ✓ Test message sent to Discord" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Webhook test failed: $_" -ForegroundColor Red
    exit 1
}

# Unlock Bitwarden and store webhook
Write-Host ""
Write-Host "Unlocking Bitwarden to store webhook..." -ForegroundColor Yellow
$env:BW_SESSION = (& $BW unlock --raw 2>&1)

if ($LASTEXITCODE -ne 0) {
    Write-Host "Bitwarden unlock failed." -ForegroundColor Red
    exit 1
}

# Create or update the Bitwarden item
$itemJson = @{
    type = 1
    name = "AGENT - Octodamus - Social - Discord"
    login = @{
        username = "octodamus-discord-webhook"
        password = $webhookUrl
    }
    notes = "Octodamus Discord webhook URL for post notifications and alerts"
} | ConvertTo-Json -Compress

$existingItem = & $BW get item "AGENT - Octodamus - Social - Discord" --session $env:BW_SESSION 2>$null
if ($existingItem) {
    $itemId = ($existingItem | ConvertFrom-Json).id
    Write-Host "  Updating existing Bitwarden item..." -ForegroundColor Gray
    $itemJson | & $BW encode | & $BW edit item $itemId --session $env:BW_SESSION | Out-Null
} else {
    Write-Host "  Creating new Bitwarden item..." -ForegroundColor Gray
    $itemJson | & $BW encode | & $BW create item --session $env:BW_SESSION | Out-Null
}

& $BW sync --session $env:BW_SESSION | Out-Null
$env:BW_SESSION = ""

Write-Host "  ✓ Webhook stored in Bitwarden" -ForegroundColor Green
Write-Host ""
Write-Host "Now run octo_unlock.ps1 to refresh the secrets cache." -ForegroundColor Cyan
Write-Host ""
