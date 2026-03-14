# octo_startup.ps1
# Octodamus --- Full Auto Startup

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
        Log "FAIL: OctodamusBitwarden not found in Credential Manager"
        exit 1
    }
    $bwPassword = $stored.GetNetworkCredential().Password
    Log "OK: Password retrieved"
} catch {
    Log "FAIL: $_"
    exit 1
}

# Step 2: Unlock Bitwarden --- filter out Node.js warnings to get clean session token
try {
    $env:BW_PASSWORD = $bwPassword
    $rawOutput = & $BW unlock --passwordenv BW_PASSWORD --raw 2>&1
    $env:BW_PASSWORD = ""
    
    # Filter: session token is a long base64 string, not a warning line
    $session = ($rawOutput | Where-Object { 
        $_ -match '^[A-Za-z0-9+/=]{20,}$' -and $_ -notmatch 'node|warning|deprecated'
    }) | Select-Object -Last 1
    
    if (-not $session -or $session.Length -lt 20) {
        Log "FAIL: Could not extract session token from output"
        Log "Raw output: $rawOutput"
        exit 1
    }
    $env:BW_SESSION = $session
    Log "OK: Vault unlocked (token length: $($session.Length))"
} catch {
    Log "FAIL: $_"
    exit 1
}

# Step 3: Load secrets to cache
$loaderScript = "$env:TEMP\octo_loader_$PID.py"
@"
import sys
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
    $env:BW_SESSION = ""
}

# Step 4: Verify cache
$cacheFile = "$PROJECT_DIR\.octo_secrets"
if (Test-Path $cacheFile) {
    $age = (Get-Date) - (Get-Item $cacheFile).LastWriteTime
    Log "OK: Cache verified ($([int]$age.TotalMinutes) min old)"
} else {
    Log "FAIL: Cache not found"
    exit 1
}

# Step 5: Start Telegram bot
try {
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-Process $PYTHON -ArgumentList "$PROJECT_DIR\telegram_bot.py" -WindowStyle Hidden -WorkingDirectory $PROJECT_DIR
    Log "OK: Telegram bot started"
} catch {
    Log "FAIL: Telegram: $_"
}

Log "=== Startup complete --- Octodamus is live ==="


