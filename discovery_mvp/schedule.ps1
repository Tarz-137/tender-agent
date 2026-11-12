param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$At = '09:00',
    [ValidateSet('codex', 'gpt', 'ollama', 'both')][string]$Provider = 'codex',
    [ValidateSet('codex', 'gpt', 'ollama')][string]$Ranker = 'codex',
    [ValidateRange(1,32)][int]$QueryBudget = 6,
    [string]$TaskName = 'Ripples-Tender-Discovery-MVP',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    exit
}
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$projectRoot = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute $pythonPath -WorkingDirectory $projectRoot `
    -Argument "-m discovery_mvp.run --provider $Provider --ranker $Ranker --query-budget $QueryBudget"
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
# Interactive token avoids storing a Windows password. Runs while this user is logged on.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'Daily search-first tender discovery MVP'
Write-Host "Scheduled $TaskName daily at $At in the computer's local timezone."
