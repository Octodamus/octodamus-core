# octo_unlock.ps1
# Octodamus — Interactive Unlock and Secrets Cache
#
# Run once after each reboot:
#   powershell -ExecutionPolicy Bypass -File C:\Users\walli\octodamus\octo_unlock.ps1

$PROJECT_DIR = "C:\Users\walli\octodamus"
$PYTHON      = "C:\Python314\python.exe"
$BW          = "C:\Users\walli\AppData\Roaming\npm\bw.cmd"

Write-Host ""
Write-Host "OCTODAMUS UNLOCK" -ForegroundColor Cyan
Write-Host "================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Bitwarden unlock
Write-Host "Step 1: Unlocking Bitwarden vault..." -ForegroundColor Yellow
try {
    $session = & $BW unlock --raw 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Unlock failed. Run: bw login first." -ForegroundColor Red
        exit 1
    }
    $env:BW_SESSION = $session
    Write-Host "  OK Vault unlocked" -ForegroundColor Green
} catch {
    Write-Host "  FAIL Bitwarden error: $_" -ForegroundColor Red
    exit 1
}

# Step 2: Load secrets and save cache
Write-Host ""
Write-Host "Step 2: Fetching secrets and saving cache..." -ForegroundColor Yellow

$loaderScript = "$env:TEMP\octo_loader.py"
Set-Content -Path $loaderScript -Value "import sys; sys.path.insert(0, r'C:\Users\walli\octodamus'); import bitwarden; s = bitwarden.load_all_secrets(verbose=True); print(f'Loaded {len(s)} secrets')"

$loadOutput = & $PYTHON $loaderScript 2>&1
Write-Host $loadOutput
Remove-Item $loaderScript -ErrorAction SilentlyContinue
$env:BW_SESSION = ""

# Step 3: Verify Twitter credentials
Write-Host ""
Write-Host "Step 3: Verifying Twitter credentials..." -ForegroundColor Yellow

$twitterScript = "$env:TEMP\octo_twitter_check.py"
Set-Content -Path $twitterScript -Value "import sys; sys.path.insert(0, r'C:\Users\walli\octodamus'); import bitwarden; bitwarden.load_all_secrets(); from octo_x_poster import check_credentials; check_credentials()"

$twitterOutput = & $PYTHON $twitterScript 2>&1
Write-Host $twitterOutput
Remove-Item $twitterScript -ErrorAction SilentlyContinue

# Step 4: Check cache file
Write-Host ""
Write-Host "Step 4: Verifying cache..." -ForegroundColor Yellow

$cacheFile = "C:\Users\walli\octodamus\.octo_secrets"
if (Test-Path $cacheFile) {
    $age = (Get-Date) - (Get-Item $cacheFile).LastWriteTime
    Write-Host "  OK Cache saved" -ForegroundColor Green
    Write-Host "  OK Age: $([int]$age.TotalMinutes) minutes" -ForegroundColor Green
} else {
    Write-Host "  FAIL Cache not found - check errors above" -ForegroundColor Red
    exit 1
}

# Step 5: Task scheduler status
Write-Host ""
Write-Host "Step 5: Task Scheduler check..." -ForegroundColor Yellow
$tasks = @(
    "Octodamus-Monitor-7am",
    "Octodamus-Monitor-115pm",
    "Octodamus-Monitor-6pm",
    "Octodamus-DailyRead",
    "Octodamus-DailyRead-1pm",
    "Octodamus-DailyRead-7pm"
)
foreach ($task in $tasks) {
    $info = schtasks /Query /TN $task /FO LIST 2>$null
    if ($info) {
        Write-Host "  OK $task" -ForegroundColor Green
    } else {
        Write-Host "  ?? $task not found" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Octodamus unlocked and ready." -ForegroundColor Green
Write-Host "Cache valid ~23 hours. Run this script again after reboot." -ForegroundColor Gray
Write-Host ""
