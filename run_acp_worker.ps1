$LOG = "C:\Users\walli\octodamus\logs\octo_acp_worker.log"
function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $LOG
}

# Guard: exit if already running (use WMI for CommandLine — standard Process object doesn't expose it)
$existing = Get-WmiObject Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*octo_acp_worker*' }
if ($existing) {
    Log "ACP Worker already running (PID $($existing.ProcessId)) -- skipping duplicate start"
    exit 0
}

Log "=== ACP Worker v7 Startup ==="
try {
    $proc = Start-Process python -ArgumentList "C:\Users\walli\octodamus\octo_acp_worker.py" `
        -WorkingDirectory "C:\Users\walli\octodamus" `
        -WindowStyle Hidden `
        -PassThru
    Log "Started PID $($proc.Id)"
    $proc.WaitForExit()
    Log "ACP Worker exited with code $($proc.ExitCode) -- signaling Task Scheduler to restart"
    exit 1
} catch {
    Log "FAIL: $_"
    exit 1
}
