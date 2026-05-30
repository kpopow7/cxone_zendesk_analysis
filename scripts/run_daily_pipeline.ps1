# Runs the daily CXone + Zendesk + combined dataset pipeline.
# Use with Windows Task Scheduler or run manually after backfill is complete.
#
# Usage:
#   .\scripts\run_daily_pipeline.ps1
#   .\scripts\run_daily_pipeline.ps1 -Timezone "America/New_York"
#   .\scripts\run_daily_pipeline.ps1 -SyncRailway

param(
    [string]$Timezone = "UTC",
    [int]$ZendeskLookbackDays = 2,
    [switch]$SyncRailway,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("daily_pipeline_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python. Run: python -m venv .venv; pip install -r requirements.txt"
}

# Ensure Postgres container is running (ignore error if docker unavailable)
try {
    docker compose up -d 2>&1 | Out-Null
} catch {
    Write-Warning "Could not start docker compose (is Docker running?)"
}

$Args = @(
    "scripts/run_daily_pipeline.py",
    "--timezone", $Timezone,
    "--zendesk-lookback-days", $ZendeskLookbackDays
)
if ($SyncRailway) { $Args += "--sync-railway" }
if ($DryRun) { $Args += "--dry-run" }

Write-Host "Logging to $LogFile"
& $Python @Args 2>&1 | Tee-Object -FilePath $LogFile
$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    Write-Error "Daily pipeline failed with exit code $ExitCode. See $LogFile"
}
Write-Host "Daily pipeline finished successfully."
