# ACP Worker watchdog -- runs every 15 min as SYSTEM
# Uses PID file (data/acp_worker.pid) -- reliable across user/SYSTEM contexts

$LOG     = "C:\Users\walli\octodamus\logs\octo_acp_watchdog.log"
$PIDFILE = "C:\Users\walli\octodamus\data\acp_worker.pid"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $LOG
}

$running = $false

if (Test-Path $PIDFILE) {
    try {
        $pid = [int](Get-Content $PIDFILE -ErrorAction Stop).Trim()
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) { $running = $true }
    } catch {}
}

if ($running) {
    exit 0
}

Log "ACP Worker not found (PID file missing or process dead) -- triggering Octodamus-ACP-Worker task"
Start-ScheduledTask -TaskName "Octodamus-ACP-Worker"
Log "Task triggered"
