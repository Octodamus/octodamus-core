# fix_monitor_schedule.ps1
# Creates all Octodamus posting tasks.
# Run: powershell -ExecutionPolicy Bypass -File fix_monitor_schedule.ps1
#
# SCHEDULE PHILOSOPHY (from X algorithm research):
#   Quality > volume. 5-6 posts/day max. Post when there's signal, not on a clock.
#   80% signal/insight, 20% personality. Threads earn the highest engagement.
#   First 60 min after posting is critical for algorithmic distribution.
#   Optimal times for crypto audience: pre-market (5-7am), lunch (12pm), US afternoon close (4pm), evening (8pm).

$ErrorActionPreference = "Continue"
$ProjectDir = "C:\Users\walli\octodamus"
$PythonExe  = "C:\Python314\python.exe"
$ScriptName = "octodamus_runner.py"

Write-Host "Octodamus Schedule Setup" -ForegroundColor Cyan

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal   = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive
$settings    = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances  IgnoreNew `
    -StartWhenAvailable

$allDays    = @("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")
$weekdays   = @("Monday","Tuesday","Wednesday","Thursday","Friday")
$monWedFri  = @("Monday","Wednesday","Friday")
$tueThu     = @("Tuesday","Thursday")
$monOnly    = @("Monday")
$satOnly    = @("Saturday")
$sundayOnly = @("Sunday")

# ── Helper: register or replace a task ────────────────────────────────────────
function Register-OctoTask {
    param($Name, $Time, $Days, $Mode, $Label, $ExtraArgs = "")
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) { Unregister-ScheduledTask -TaskName $Name -Confirm:$false }

    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $Time
    $argStr  = "$ProjectDir\$ScriptName --mode $Mode"
    if ($ExtraArgs) { $argStr += " $ExtraArgs" }
    $action  = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument $argStr `
        -WorkingDirectory $ProjectDir

    Register-ScheduledTask `
        -TaskName    $Name `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description "Octodamus $Label" | Out-Null

    Write-Host "  OK  $Name @ $Time" -ForegroundColor Green
}

# ── Remove old redundant tasks from previous over-posting schedule ─────────────
$oldTasks = @(
    "Octodamus-DailyRead-1pm",    # removed -- was redundant with monitor
    "Octodamus-DailyRead-6am",    # removed -- 5am + 7pm is enough
    "Octodamus-Alert-8am",        # removed -- monitor at 7am covers this
    "Octodamus-Alert-2pm",        # removed -- monitor at 1:15pm covers this
    "Octodamus-Alert-8pm",        # removed -- evening read covers this
    "Octodamus-Alert",            # old naming
    "Octodamus-Monitor-115pm",    # replaced by Format-12pm
    "Octodamus-Format-8am",       # replaced by Monitor-7am
    "Octodamus-Format-12pm",      # replaced by Monitor-12pm
    "Octodamus-Format-4pm",       # replaced by Monitor-4pm
    "Octodamus-Format-8pm",       # replaced by DailyRead-7pm
    "Octodamus-MorningFlow-5am",  # folded into DailyRead-5am
    "Octodamus-MorningFlow-6am",  # removed
    "Octodamus-MorningFlow-7am",  # removed
    "Octodamus-Engage-2pm",       # removed -- engage is lower priority
    "Octodamus-Engage-3pm",       # removed
    "Octodamus-Engage-4pm",       # removed
    "Octodamus-Engage-8pm",       # removed
    "Octodamus-DeepDive-Mon",     # replaced by Thread-Monday
    "Octodamus-DeepDive-Wed"      # replaced by Thread-Wednesday
)
foreach ($old in $oldTasks) {
    $t = Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $old -Confirm:$false
        Write-Host "  DEL $old" -ForegroundColor Yellow
    }
}

# ═══════════════════════════════════════════════════════════════════════════════
# DAILY POSTING SCHEDULE -- max 5-6 posts/day
# Times are PST (UTC-7). Crypto audience peaks: pre-market, US lunch, US close, evening.
# ═══════════════════════════════════════════════════════════════════════════════

# ── 5am -- Morning oracle: fresh overnight data, first signal of the day ────────
# Pre-market crypto crowd is active. Strong hook = algorithmic boost into morning.
Register-OctoTask "Octodamus-DailyRead"       "05:00AM" $allDays "daily"   "morning oracle -- first post of day"

# ── 7am -- Monitor: only posts if there's genuine signal ───────────────────────
# Conditional -- skips if nothing worth saying. Prevents filler.
Register-OctoTask "Octodamus-Monitor-7am"     "07:00AM" $allDays "monitor" "morning monitor -- conditional post"

# ── 9am Mon/Wed -- Thread: highest-value format, 2x per week ───────────────────
# Threads earn the most bookmarks and follow conversions.
# Mon: start-of-week macro/BTC outlook. Wed: mid-week signal deep dive.
Register-OctoTask "Octodamus-Thread-Mon"      "09:00AM" $monOnly   "thread" "Monday thread -- macro/BTC outlook"
Register-OctoTask "Octodamus-Thread-Wed"      "09:00AM" @("Wednesday") "thread" "Wednesday thread -- mid-week deep dive"

# ── 12pm -- Midday format: US lunch hour, peak impressions window ───────────────
Register-OctoTask "Octodamus-Format-12pm"     "12:15PM" $allDays "format"  "midday format post"

# ── 4pm -- Afternoon monitor: US market close signal ──────────────────────────
# Conditional -- posts when derivatives/price action has something worth saying.
Register-OctoTask "Octodamus-Monitor-4pm"     "04:00PM" $allDays "monitor" "afternoon monitor -- market close signal"

# ── 7pm -- Evening daily read: wrap the day ────────────────────────────────────
Register-OctoTask "Octodamus-DailyRead-7pm"   "07:00PM" $allDays "daily"   "evening daily read"

# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY SPECIALS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Saturday 10am -- Wisdom: reflective, personality-driven ────────────────────
Register-OctoTask "Octodamus-Wisdom"          "10:00AM" $satOnly    "wisdom"          "Saturday wisdom post"

# ── Sunday 11am -- Soul: deeper character post ─────────────────────────────────
Register-OctoTask "Octodamus-Soul"            "11:00AM" $sundayOnly "soul"            "Sunday soul post"

# ── Sunday 4am -- Strategy: weekly macro setup ─────────────────────────────────
Register-OctoTask "Octodamus-StrategySunday"  "04:00AM" $sundayOnly "strategy_sunday" "Sunday strategy -- weekly macro post"

# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND INTELLIGENCE (no posting -- data collection / QRT scanning)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Mon-Sat 9am -- Strategy monitor: background intel snapshot (no post) ────────
Register-OctoTask "Octodamus-StrategyMonitor" "09:30AM" @("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday") "strategy_monitor" "STRATEGY daily intel snapshot"

# ── Daily 7am -- QRT scan: find replies/QRTs worth engaging ───────────────────
Register-OctoTask "Octodamus-QRT-Scan"        "07:30AM" $allDays "qrt"              "QRT scan -- find engagement opportunities"

# ── Daily 8pm -- Congress scan: politician trading alerts ──────────────────────
Register-OctoTask "Octodamus-Congress"        "08:00PM" $allDays "congress"         "Congress trading scan"

Write-Host "`nDone. Tasks registered." -ForegroundColor Cyan
Write-Host ""
Write-Host "  DAILY POSTING (max 5-6 posts):" -ForegroundColor White
Write-Host "    5:00am  Morning oracle (every day)" -ForegroundColor Gray
Write-Host "    7:00am  Monitor -- conditional (every day)" -ForegroundColor Gray
Write-Host "    9:00am  Thread -- Mon + Wed only (highest engagement format)" -ForegroundColor Gray
Write-Host "   12:15pm  Format post (every day)" -ForegroundColor Gray
Write-Host "    4:00pm  Monitor -- conditional (every day)" -ForegroundColor Gray
Write-Host "    7:00pm  Evening read (every day)" -ForegroundColor Gray
Write-Host ""
Write-Host "  WEEKLY SPECIALS:" -ForegroundColor White
Write-Host "    Saturday 10am  Wisdom post" -ForegroundColor Gray
Write-Host "    Sunday   11am  Soul post" -ForegroundColor Gray
Write-Host "    Sunday    4am  Strategy weekly macro" -ForegroundColor Gray
Write-Host ""
Write-Host "  BACKGROUND (no posting):" -ForegroundColor White
Write-Host "    7:30am  QRT scan (every day)" -ForegroundColor Gray
Write-Host "    9:30am  Strategy monitor -- Mon-Sat" -ForegroundColor Gray
Write-Host "    8:00pm  Congress scan (every day)" -ForegroundColor Gray
Write-Host ""
Write-Host "  REMOVED (over-posting cleanup):" -ForegroundColor Yellow
Write-Host "    DailyRead-6am, Alert x3, Monitor-1:15pm, Format x4, MorningFlow x3, Engage x4, DeepDive x2" -ForegroundColor DarkYellow
