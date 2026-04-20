$action = New-ScheduledTaskAction -Execute "C:\Python314\python.exe" -Argument "C:\Users\walli\octodamus\octodamus_runner.py --mode mentions"
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 30) -Once -At "07:00" -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
Register-ScheduledTask -TaskName "Octodamus-Mentions" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Description "Poll @octodamusai mentions and auto-reply every 30 min"
Write-Host "Octodamus-Mentions task registered."
