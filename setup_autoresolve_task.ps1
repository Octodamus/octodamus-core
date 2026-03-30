# setup_autoresolve_task.ps1
# Creates a Task Scheduler task that auto-resolves expired Oracle calls every 6 hours.
# Checks live prices against open calls and resolves them.

$taskName = "Octodamus-AutoResolve"
$pythonPath = "C:\Python314\python.exe"
$scriptPath = "C:\Users\walli\octodamus\octo_calls.py"
$workDir = "C:\Users\walli\octodamus"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "$scriptPath autoresolve" `
    -WorkingDirectory $workDir

# Run every 6 hours
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(6) `
    -RepetitionInterval (New-TimeSpan -Hours 6)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$principal = New-ScheduledTaskPrincipal -UserId "walli" -RunLevel Highest -LogonType S4U

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Auto-resolve expired Octodamus Oracle calls every 6 hours"

Write-Host "Task '$taskName' created. Runs every 6 hours."
Write-Host "Test: python C:\Users\walli\octodamus\octo_calls.py autoresolve"
