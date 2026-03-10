# fix_monitor_schedule.ps1
# Replaces the every-30-min Octodamus-Monitor task with 3 fixed daily posts.
# Targets: 8am, 1pm, 7pm PT — Mon through Fri.
#
# Run from PowerShell as the same user that runs Octodamus:
#   powershell -ExecutionPolicy Bypass -File fix_monitor_schedule.ps1
# ═══════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$ProjectDir = "C:\Users\walli\octodamus"
$PythonExe  = "C:\Python314\python.exe"
$ScriptName = "octodamus_runner.py"
$TaskPrefix = "Octodamus-Monitor"

Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " Octodamus Monitor Schedule Fix" -ForegroundColor Cyan
Write-Host " Replacing every-30-min task → 3x daily (8am/1pm/7pm)" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan

# ─── Step 1: Remove old monitor task ───────────────────────────
Write-Host "`nStep 1: Removing old monitor task..." -ForegroundColor White
$oldTask = Get-ScheduledTask -TaskName $TaskPrefix -ErrorAction SilentlyContinue
if ($oldTask) {
    Unregister-ScheduledTask -TaskName $TaskPrefix -Confirm:$false
    Write-Host "   ✅ Removed: $TaskPrefix" -ForegroundColor Green
} else {
    Write-Host "   ℹ️  Task '$TaskPrefix' not found — skipping removal." -ForegroundColor Yellow
}

# ─── Helper: build action + principal + settings ───────────────
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function New-OctoAction {
    param($Mode)
    New-ScheduledTaskAction `
        -Execute    $PythonExe `
        -Argument   "$ProjectDir\$ScriptName --mode $Mode" `
        -WorkingDirectory $ProjectDir
}

$principal = New-ScheduledTaskPrincipal `
    -UserId    $CurrentUser `
    -LogonType Interactive

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances  IgnoreNew `
    -StartWhenAvailable

# ─── Step 2: Register 3 new fixed-time monitor tasks ───────────
Write-Host "`nStep 2: Creating 3 daily monitor tasks..." -ForegroundColor White

$slots = @(
    @{ Name = "Octodamus-Monitor-8am";  Time = "08:00AM"; Label = "morning" },
    @{ Name = "Octodamus-Monitor-1pm";  Time = "01:00PM"; Label = "midday"  },
    @{ Name = "Octodamus-Monitor-7pm";  Time = "07:00PM"; Label = "evening" }
)

foreach ($slot in $slots) {
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
        -At $slot.Time

    Register-ScheduledTask `
        -TaskName    $slot.Name `
        -Action      (New-OctoAction "monitor") `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description "🐙 Octodamus $($slot.Label) market oracle post." | Out-Null

    Write-Host "   ✅ $($slot.Name) — $($slot.Time) Mon–Fri" -ForegroundColor Green
}

# ─── Summary ───────────────────────────────────────────────────
Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host " Done. New monitor schedule:" -ForegroundColor Cyan
Write-Host "   Octodamus-Monitor-8am    Mon–Fri  8:00 AM" -ForegroundColor Gray
Write-Host "   Octodamus-Monitor-1pm    Mon–Fri  1:00 PM" -ForegroundColor Gray
Write-Host "   Octodamus-Monitor-7pm    Mon–Fri  7:00 PM" -ForegroundColor Gray
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "`nVerify with: Get-ScheduledTask | Where-Object {$_.TaskName -like 'Octodamus*'}" -ForegroundColor Yellow
