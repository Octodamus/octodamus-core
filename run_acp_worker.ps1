$bwPath = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"
$LOG = "C:\Users\walli\octodamus\logs\octo_acp_worker.log"
function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $LOG
}
Log "=== ACP Worker Startup ==="
try {
    Import-Module CredentialManager -ErrorAction Stop
    $stored = Get-StoredCredential -Target "OctodamusBitwarden"
    if (-not $stored) { Log "FAIL: OctodamusBitwarden not found"; exit 1 }
    $bwPassword = $stored.GetNetworkCredential().Password
    Log "OK: Password retrieved"
} catch { Log "FAIL: $_"; exit 1 }
try {
    $env:BW_PASSWORD = $bwPassword
    $rawOutput = & $bwPath unlock --passwordenv BW_PASSWORD --raw 2>&1
    $env:BW_PASSWORD = ""
    $session = ($rawOutput | Where-Object { $_ -match '^[A-Za-z0-9+/=]{20,}$' }) | Select-Object -Last 1
    if (-not $session) { Log "FAIL: Bitwarden unlock failed"; exit 1 }
    $env:BW_SESSION = $session
    Log "OK: Bitwarden unlocked"
} catch { Log "FAIL: $_"; exit 1 }
# Load ACP key from key file (written by octo_unlock.ps1 at boot)
$keyPath = "C:\Users\walli\octodamus\octo_acp_key.txt"
if (-not (Test-Path $keyPath)) { Log "FAIL: octo_acp_key.txt not found - run octo_unlock.ps1"; exit 1 }
$acpKey = (Get-Content $keyPath -Raw).Trim()
if (-not $acpKey) { Log "FAIL: octo_acp_key.txt is empty"; exit 1 }
Log "OK: ACP key loaded from file ($($acpKey.Length) chars)"
Start-Process wsl.exe -ArgumentList '-d Ubuntu -- bash -c "OCTO_ACP_PRIVATE_KEY=\$(cat /mnt/c/Users/walli/octodamus/octo_acp_key.txt) QUIVER_API_KEY=\$(cat /home/walli/octodamus/octo_quiver_key.txt 2>/dev/null) python3 /home/walli/octodamus/octo_acp_worker.py >> /home/walli/octodamus/logs/octo_acp_worker.log 2>&1"' -WindowStyle Hidden
Log "ACP worker exited"
