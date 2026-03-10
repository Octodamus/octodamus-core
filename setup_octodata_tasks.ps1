# setup_octodata_tasks.ps1
# Run once as Administrator to register the 3 overnight OctoData cron jobs
# and install the API server as a Windows Service via NSSM.
#
# Prerequisites:
#   pip install fastapi uvicorn requests anthropic --break-system-packages
#   winget install NSSM  (or download from nssm.cc)

$PythonPath = "C:\Users\walli\AppData\Local\Programs\Python\Python311\python.exe"
$ScriptDir  = "C:\Users\walli\octodamus"
$RunSh      = "$ScriptDir\run_octo.ps1"

# ── helper ────────────────────────────────────────────────────────────────────
function Register-OctoTask {
    param(
        [string]$TaskName,
        [string]$Mode,
        [string]$StartTime
    )

    $action  = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NonInteractive -WindowStyle Hidden -File `"$RunSh`" --mode $Mode" `
        -WorkingDirectory $ScriptDir

    # Mon–Sun overnight (including weekends — data doesn't take days off)
    $trigger = New-ScheduledTaskTrigger -Daily -At $StartTime

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action   $action `
        -Trigger  $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Force | Out-Null

    Write-Host "  [OK] $TaskName registered at $StartTime"
}

# ── update run_octo.ps1 to support octo_data_aggregator.py modes ─────────────
# The existing run_octo.ps1 calls octodamus_runner.py.
# We need it to also handle prices/sentiment/briefing modes.
# Add this block to run_octo.ps1 if not already present:
Write-Host ""
Write-Host "Checking run_octo.ps1 for aggregator support..."
$runContent = Get-Content $RunSh -Raw -ErrorAction SilentlyContinue

if ($runContent -notmatch "octo_data_aggregator") {
    $patchLine = @"

# OctoData aggregator modes (added by setup_octodata_tasks.ps1)
if (`$args -match "--mode (prices|sentiment|briefing|all)") {
    `$mode = `$Matches[1]
    & "$PythonPath" "$ScriptDir\octo_data_aggregator.py" --mode `$mode
    exit
}
"@
    # Prepend the patch before the existing python call
    $runContent = $patchLine + "`n" + $runContent
    Set-Content $RunSh $runContent
    Write-Host "  [OK] run_octo.ps1 patched for aggregator modes"
} else {
    Write-Host "  [OK] run_octo.ps1 already supports aggregator"
}

# ── register the 3 overnight tasks ───────────────────────────────────────────
Write-Host ""
Write-Host "Registering overnight OctoData tasks..."

Register-OctoTask -TaskName "OctoData - Price Snapshot"    -Mode "prices"    -StartTime "01:00"
Register-OctoTask -TaskName "OctoData - Sentiment Scoring" -Mode "sentiment" -StartTime "02:00"
Register-OctoTask -TaskName "OctoData - Market Briefing"   -Mode "briefing"  -StartTime "03:00"

Write-Host ""
Write-Host "All 3 tasks registered. Verify in Task Scheduler under 'OctoData'."

# ── install API server as Windows Service via NSSM ────────────────────────────
Write-Host ""
Write-Host "Installing OctoData API server as a Windows Service..."

$nssmPath = (Get-Command nssm -ErrorAction SilentlyContinue)?.Source
if (-not $nssmPath) {
    Write-Host "  [SKIP] NSSM not found. Install with: winget install NSSM"
    Write-Host "  Then re-run this script, or start manually: python octo_api_server.py"
} else {
    & nssm install OctoDataAPI "$PythonPath"
    & nssm set OctoDataAPI AppParameters "$ScriptDir\octo_api_server.py"
    & nssm set OctoDataAPI AppDirectory "$ScriptDir"
    & nssm set OctoDataAPI AppStdout "$ScriptDir\logs\api_server.log"
    & nssm set OctoDataAPI AppStderr "$ScriptDir\logs\api_server_error.log"
    & nssm set OctoDataAPI Start SERVICE_AUTO_START
    Start-Service OctoDataAPI
    Write-Host "  [OK] OctoDataAPI service installed and started on port 8742"
}

# ── create first test API key ─────────────────────────────────────────────────
Write-Host ""
Write-Host "Creating your admin API key for testing..."
Write-Host "  Server must be running first. Run manually for now:"
Write-Host "  cd $ScriptDir"
Write-Host "  python octo_api_server.py"
Write-Host ""
Write-Host "  Then in another terminal:"
Write-Host "  curl -X POST 'http://localhost:8742/admin/keys/create?label=CW-admin&tier=admin&days=0&admin_secret=change-me-in-bitwarden'"
Write-Host ""
Write-Host "  Save the returned key to Bitwarden as: AGENT - Octodamus - OctoData Admin Key"
Write-Host ""
Write-Host "Done. Summary:"
Write-Host "  Overnight jobs:  1am prices | 2am sentiment | 3am briefing"
Write-Host "  API server:      http://localhost:8742 (or expose via Cloudflare Tunnel)"
Write-Host "  API docs:        http://localhost:8742/docs"
Write-Host "  Revenue path:    rapidapi.com/provider → list as 'OctoData'"
