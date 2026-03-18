#!/bin/bash
cd ~/octodamus
MODE=${1:-daily}
export BW_MASTER_PASS=$(powershell.exe -Command "(Get-StoredCredential -Target bitwarden_master).GetNetworkCredential().Password" 2>/dev/null | tr -d '\r')
export BW_SESSION=$(bw unlock --passwordenv BW_MASTER_PASS --raw 2>/dev/null)
if [ -z "$BW_SESSION" ]; then
    echo "[$(date)] ERROR: Bitwarden unlock failed" >> ~/octodamus/octo_error.log
    exit 1
fi
python3 octodamus_runner.py --mode $MODE >> ~/octodamus/octo.log 2>&1
echo "[$(date)] $MODE completed" >> ~/octodamus/octo.log
