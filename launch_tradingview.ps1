# Launch TradingView Desktop with Chrome DevTools Protocol enabled (port 9222)
# Required for TradingView MCP / Claude Code chart integration

$tvExe = "C:\Program Files\WindowsApps\TradingView.Desktop_3.0.0.7652_x64__n534cwy3pjxzj\TradingView.exe"

# Kill existing TradingView instance
$existing = Get-Process -Name "TradingView" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Closing existing TradingView..."
    $existing | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# Launch with CDP debug port
Write-Host "Launching TradingView with --remote-debugging-port=9222..."
Start-Process -FilePath $tvExe -ArgumentList "--remote-debugging-port=9222"

# Wait and confirm
Start-Sleep -Seconds 3
$running = Get-Process -Name "TradingView" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "TradingView launched. CDP available at http://localhost:9222"
} else {
    Write-Host "WARNING: TradingView may not have started. Check manually."
}
