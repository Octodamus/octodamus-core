$LOG = "C:\Users\walli\octodamus\logs\octo_acp_worker.log"
function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $LOG
}
Log "=== ACP Worker v7 Startup ==="
try {
    $proc = Start-Process python -ArgumentList "C:\Users\walli\octodamus\octo_acp_worker.py" `
        -WorkingDirectory "C:\Users\walli\octodamus" `
        -WindowStyle Hidden `
        -PassThru
    Log "Started PID $($proc.Id)"
    $proc.WaitForExit()
    Log "ACP Worker exited with code $($proc.ExitCode)"
} catch {
    Log "FAIL: $_"
    exit 1
}
