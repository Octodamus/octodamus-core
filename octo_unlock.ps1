# octo_unlock.ps1
# Octodamus - One Password Full Boot
# Usage: powershell -ExecutionPolicy Bypass -File C:\Users\walli\octodamus\octo_unlock.ps1

$PROJECT_DIR = "C:\Users\walli\octodamus"
$PYTHON      = "C:\Python314\python.exe"
$BW          = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"
$LOG         = "$PROJECT_DIR\logs\octo_unlock.log"

New-Item -ItemType Directory -Force -Path "$PROJECT_DIR\logs" | Out-Null
"" | Out-File -FilePath $LOG -Encoding utf8

function Log($msg) {
    $ts = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LOG -Value $line -Encoding utf8
    Write-Host $line
}

function Status($label, $ok, $detail) {
    if ($ok) { $icon = "OK  "; $color = "Green" } else { $icon = "FAIL"; $color = "Red" }
    if ($detail) { $msg = "  [$icon] $label - $detail" } else { $msg = "  [$icon] $label" }
    Write-Host $msg -ForegroundColor $color
    Add-Content -Path $LOG -Value $msg -Encoding utf8
}

Clear-Host
Write-Host ""
Write-Host "  OCTODAMUS BOOT" -ForegroundColor Cyan
Write-Host "  ==============" -ForegroundColor Cyan
Write-Host ""

# STEP 1: Bitwarden unlock
Write-Host "  [1/6] Unlocking Bitwarden..." -ForegroundColor Yellow
$securePass = Read-Host "  Master password" -AsSecureString
$bwPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
)
$env:BW_PASSWORD = $bwPassword
$rawOutput = & $BW unlock --passwordenv BW_PASSWORD --raw 2>&1
$env:BW_PASSWORD = ""
$bwPassword = ""
$session = ($rawOutput | Where-Object { $_ -match '^[A-Za-z0-9+/=]{20,}$' }) | Select-Object -Last 1
if (-not $session) {
    Status "Bitwarden unlock" $false "wrong password or run: bw login"
    exit 1
}
$env:BW_SESSION = $session
Status "Bitwarden unlock" $true "vault open"

# STEP 2: Load secrets to Windows cache
Write-Host ""
Write-Host "  [2/6] Loading secrets..." -ForegroundColor Yellow
$loaderPath = "$env:TEMP\octo_loader.py"
$loaderCode = @"
import sys
sys.path.insert(0, r'C:\Users\walli\octodamus')
import bitwarden
s = bitwarden.load_all_secrets(verbose=False)
print('Loaded' + ' ' + str(len(s)) + ' secrets')
"@
$loaderCode | Out-File -FilePath $loaderPath -Encoding utf8
$loadOut = & $PYTHON $loaderPath 2>&1
Remove-Item $loaderPath -ErrorAction SilentlyContinue
$env:BW_SESSION = ""
$cacheFile = "$PROJECT_DIR\.octo_secrets"
$secretsOk = (Test-Path $cacheFile) -and ((Get-Item $cacheFile).LastWriteTime -gt (Get-Date).AddMinutes(-2))
Status "Windows secrets cache" $secretsOk ($loadOut | Select-Object -Last 1)
if (-not $secretsOk) { Log "ABORT: secrets cache not written"; exit 1 }

# STEP 3: Sync cache to WSL
Write-Host ""
Write-Host "  [3/6] Syncing to WSL..." -ForegroundColor Yellow
$wslTarget = "\\wsl$\Ubuntu\home\walli\octodamus\.octo_secrets"
try {
    Copy-Item $cacheFile $wslTarget -Force
    Status "WSL secrets sync" $true
} catch {
    Status "WSL secrets sync" $false "$_"
}

# Sync all Python files Windows -> WSL
Write-Host "  Syncing Python files to WSL..." -ForegroundColor Cyan
wsl.exe -d Ubuntu -- bash -c "cp /mnt/c/Users/walli/octodamus/*.py /home/walli/octodamus/ 2>/dev/null"
Status "Python file sync" $true

# STEP 4: Start Telegram bot
Write-Host ""
Write-Host "  [4/6] Starting Telegram bot..." -ForegroundColor Yellow
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" 2>$null).CommandLine
    if ($cmd -like "*telegram_bot*") {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        Log "Killed stale Telegram bot PID $($_.Id)"
    }
}
Start-Sleep -Seconds 1
Start-Process $PYTHON -ArgumentList "$PROJECT_DIR\telegram_bot.py" -WindowStyle Hidden -WorkingDirectory $PROJECT_DIR
Start-Sleep -Seconds 2
$botPids = @(Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" 2>$null).CommandLine -like "*telegram_bot*"
})
Status "Telegram bot" ($botPids.Count -gt 0)

# STEP 4.5: Start OctoBoto
Write-Host ""
Write-Host "  [4.5/6] Starting OctoBoto..." -ForegroundColor Yellow
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" 2>$null).CommandLine
    if ($cmd -like "*octo_boto*") {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        Log "Killed stale OctoBoto PID $($_.Id)"
    }
}
Start-Sleep -Seconds 1
Start-Process $PYTHON -ArgumentList "$PROJECT_DIR\octo_boto.py" -WindowStyle Hidden -WorkingDirectory $PROJECT_DIR
Start-Sleep -Seconds 2
$botoPids = @(Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" 2>$null).CommandLine -like "*octo_boto*"
})
Status "OctoBoto" ($botoPids.Count -gt 0)

# STEP 5: Start ACP worker in WSL
Write-Host ""
Write-Host "  [5/6] Starting ACP worker..." -ForegroundColor Yellow
wsl.exe -d Ubuntu -- bash -c "pkill -f octo_acp_worker 2>/dev/null; sleep 1"
Start-Sleep -Seconds 2
schtasks /Run /TN "Octodamus-ACP-Worker" | Out-Null
Start-Sleep -Seconds 20
$acpOut = wsl.exe -d Ubuntu -- bash -c "pgrep -f octo_acp_worker > /dev/null && echo yes || echo no"
Status "ACP worker" ($acpOut.Trim() -eq "yes")

# STEP 6: Verify API server
Write-Host ""
Write-Host "  [6/6] Checking API server..." -ForegroundColor Yellow
try {
    $resp = Invoke-WebRequest -Uri "https://api.octodamus.com/health" -TimeoutSec 5 -UseBasicParsing
    Status "API server" ($resp.StatusCode -eq 200) "api.octodamus.com OK"
} catch {
    Status "API server" $false "not responding - check Octodamus-API-Server task"
}

# STATUS BOARD
Write-Host ""
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host "  OCTODAMUS ONLINE" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "  Waiting for services to initialize..." -ForegroundColor Cyan
  Start-Sleep -Seconds 20
  Write-Host "  Running system health check..." -ForegroundColor Cyan
  & "C:\Python314\python.exe" "C:\Users\walli\octodamus\octo_health.py" "boot"
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host "  Log: $LOG" -ForegroundColor Gray
Write-Host "  Task Scheduler handles all scheduled posts." -ForegroundColor Gray
Write-Host ""
