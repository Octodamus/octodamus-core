param([string]$mode = "status", [string]$ticker = "")

$bwPath = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"
$pyScript = "C:\Users\walli\octodamus\octodamus_runner.py"

$cred = Get-StoredCredential -Target "bitwarden_octodamus"
if (-not $cred) { Write-Error "Bitwarden credential not found."; exit 1 }

$env:BW_PASSWORD = $cred.GetNetworkCredential().Password
$env:BW_SESSION = (& $bwPath unlock --passwordenv BW_PASSWORD --raw 2>$null)
if (-not $env:BW_SESSION) { Write-Error "Failed to unlock Bitwarden."; exit 1 }

if ($ticker) {
    python $pyScript --mode $mode --ticker $ticker
} else {
    python $pyScript --mode $mode
}
