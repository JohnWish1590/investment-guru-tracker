param(
    [string]$TaskName = "InvestmentGuruTracker-Daily",
    [string]$At = "08:10"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = (Get-Command python).Source
$script = Join-Path $root "scripts\daily_check.py"
$runner = Join-Path $root "scripts\run_daily.cmd"
$logFile = Join-Path $root "logs/task.log"

if (-not (Test-Path $script)) { throw "missing $script" }

$lines = @(
    '@echo off',
    ('cd /d "' + $root + '"'),
    ('"' + $py + '" "' + $script + '" >> "' + $logFile + '" 2>&1')
)
Set-Content -Path $runner -Value $lines -Encoding ASCII

$action = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily check of 13F / STOCK Act / OGE disclosures and rebuild of the tracker site" -Force | Out-Null

Write-Host "Registered task: $TaskName (daily $At)"
Write-Host "  script: $script"
Write-Host "  log:    $logFile"
Write-Host "Run now:      Start-ScheduledTask -TaskName $TaskName"
Write-Host "Check status: Get-ScheduledTaskInfo -TaskName $TaskName"
