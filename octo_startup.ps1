# octo_startup.ps1
# Octodamus - Full Auto Startup

$PROJECT_DIR = "C:\Users\walli\octodamus"
$PYTHON      = "C:\Python314\python.exe"
$BW          = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"
$LOG         = "$PROJECT_DIR\octo_startup.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $LOG
    Write-Host "$ts $msg"
}

"" | Out-File -Encoding utf8 $LOG
Log "=== Octodamus Startup ==="

# Step 1: Get password from Credential Manager
try {
    Import-Module CredentialManager -ErrorAction Stop
    $stored = Get-StoredCredential -Target "OctodamusBitwarden"
    if (-not $stored) {
        Log "FAIL: OctodamusBitwarden not found"
        exit 1
    }
    $bwPassword = $stored.GetNetworkCredential().Password
    Log "OK: Password retrieved"
} catch {
    Log "FAIL: $_"
    exit 1
}

# Step 2: Unlock Bitwarden - filter Node.js warnings
try {
    $env:BW_PASSWORD = $bwPassword
    $rawOutput = & $BW unlock --passwordenv BW_PASSWORD --raw 2>&1
    $env:BW_PASSWORD = ""
    $session = ($rawOutput | Where-Object {
        $_ -match '^[A-Za-z0-9+/=]{20,}$'
    }) | Select-Object -Last 1
    if (-not $session -or $session.Length -lt 20) {
        Log "FAIL: Could not extract session token"
        exit 1
    }
    Log "OK: Vault unlocked (token length: $($session.Length))"
} catch {
    Log "FAIL: $_"
    exit 1
}

# Step 3: Write session to temp file so Python subprocess can read it
$sessionFile = "$env:TEMP\octo_bw_session.tmp"
$session | Out-File -FilePath $sessionFile -Encoding ascii -NoNewline

# Step 4: Load secrets using session from file
$loaderScript = "$env:TEMP\octo_loader_$PID.py"
@"
import sys, os, subprocess
from pathlib import Path

# Read BW_SESSION from temp file
session_file = r'$sessionFile'
session = Path(session_file).read_text(encoding='ascii').strip()
os.environ['BW_SESSION'] = session

sys.path.insert(0, r'$PROJECT_DIR')
import bitwarden
s = bitwarden.load_all_secrets(verbose=False)
print(f'Loaded {len(s)} secrets')
"@ | Set-Content -Path $loaderScript -Encoding utf8

try {
    $loadOutput = & $PYTHON $loaderScript 2>&1
    Log "OK: $loadOutput"
} catch {
    Log "FAIL: $_"
    exit 1
} finally {
    Remove-Item $loaderScript -ErrorAction SilentlyContinue
    Remove-Item $sessionFile -ErrorAction SilentlyContinue
}

# Step 5: Verify cache
$cacheFile = "$PROJECT_DIR\.octo_secrets"
if (Test-Path $cacheFile) {
    $age = (Get-Date) - (Get-Item $cacheFile).LastWriteTime
    Log "OK: Cache verified ($([int]$age.TotalMinutes) min old)"
} else {
    Log "FAIL: Cache not found"
    exit 1
}

# Step 6: Start Telegram bot
try {
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-Process $PYTHON -ArgumentList "$PROJECT_DIR\telegram_bot.py" -WindowStyle Hidden -WorkingDirectory $PROJECT_DIR
    Log "OK: Telegram bot started"
} catch {
    Log "FAIL: Telegram: $_"
}

Log "=== Startup complete ==="
