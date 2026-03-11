$bwPath = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"
$cred = Get-StoredCredential -Target "bitwarden_octodamus"
if (-not $cred) { Write-Error "Bitwarden credential not found."; exit 1 }
$env:BW_PASSWORD = $cred.GetNetworkCredential().Password
$env:BW_SESSION = (& $bwPath unlock --passwordenv BW_PASSWORD --raw 2>$null)
if (-not $env:BW_SESSION) { Write-Error "Failed to unlock Bitwarden."; exit 1 }
& $bwPath sync --session $env:BW_SESSION 2>$null
$itemJson = (& $bwPath get item "AGENT - Octodamus - ACP Wallet" --session $env:BW_SESSION 2>$null)
$acpKey = ($itemJson | & "C:\Python314\python.exe" -c "import sys,json; d=json.load(sys.stdin); print(d['login']['password'])")
if (-not $acpKey) { Write-Error "Failed to get ACP private key."; exit 1 }
$acpKey | Out-File -FilePath "\\wsl$\Ubuntu\tmp\octo_acp_key.txt" -Encoding ascii -NoNewline
wsl.exe -d Ubuntu -- bash -c 'export OCTO_ACP_PRIVATE_KEY=$(cat /tmp/octo_acp_key.txt); cd /mnt/c/Users/walli/octodamus && python3 octo_acp_worker.py'
