#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramData 'SnipeIT Inventory Gateway'),
    [string]$TaskPath = '\ExampleOrg\',
    [string]$TaskName = 'SnipeIT Inventory Collection'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$agentFile = Join-Path $InstallRoot 'SnipeIT.Inventory.Agent.ps1'
$previousFile = "$agentFile.previous"
$configFile = Join-Path $InstallRoot 'Config\config.json'

if (-not (Test-Path -LiteralPath $previousFile -PathType Leaf)) {
    throw 'Previous agent version is not available'
}
if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
    throw 'Agent config is not available'
}

if ($PSCmdlet.ShouldProcess($agentFile, 'Restore previous agent and validate it')) {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -ne $task) { Disable-ScheduledTask -InputObject $task | Out-Null }
    Copy-Item -LiteralPath $previousFile -Destination $agentFile -Force
    $windowsPowerShell = Join-Path $PSHOME 'powershell.exe'
    & $windowsPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $agentFile -Config $configFile -DryRun
    if ($LASTEXITCODE -ne 0) { throw 'Rolled-back agent failed dry-run validation' }
    if ($null -ne $task) { Enable-ScheduledTask -InputObject $task | Out-Null }
}

Write-Output 'Previous SnipeIT Inventory Agent restored and validated.'
