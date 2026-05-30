# Register a Windows Scheduled Task to run the daily pipeline.
# Run PowerShell as Administrator (optional for some task types; current user works for /SC DAILY).
#
# Usage:
#   .\scripts\register_daily_task.ps1
#   .\scripts\register_daily_task.ps1 -Time "06:30" -Timezone "America/New_York" -SyncRailway

param(
    [string]$TaskName = "CXoneZendeskDailyPipeline",
    [string]$Time = "06:00",
    [string]$Timezone = "UTC",
    [int]$ZendeskLookbackDays = 2,
    [switch]$SyncRailway
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $ProjectRoot "scripts\run_daily_pipeline.ps1"

$SyncFlag = if ($SyncRailway) { "-SyncRailway" } else { "" }
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Timezone `"$Timezone`" -ZendeskLookbackDays $ZendeskLookbackDays $SyncFlag"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Daily CXone transcript extract, Zendesk ticket extract, and combined_interactions update." `
    -Force

Write-Host "Registered scheduled task '$TaskName' daily at $Time."
Write-Host "Runner: $Runner"
Write-Host "Test now: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "View logs: Get-ChildItem (Join-Path '$ProjectRoot' 'logs') | Sort-Object LastWriteTime -Descending | Select-Object -First 5"
