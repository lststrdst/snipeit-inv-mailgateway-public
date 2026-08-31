#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramData 'SnipeIT Inventory Gateway'),
    [string]$TaskPath = '\ExampleOrg\',
    [string]$TaskName = 'SnipeIT Inventory Collection',
    [switch]$PurgeData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    }
}

if ($PurgeData) {
    $resolved = [IO.Path]::GetFullPath($InstallRoot)
    $expected = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'SnipeIT Inventory Gateway'))
    if ($resolved -ne $expected) { throw 'PurgeData is allowed only for the default agent directory' }
    if ((Test-Path -LiteralPath $resolved -PathType Container) -and
        $PSCmdlet.ShouldProcess($resolved, 'Permanently delete agent, config, keys, queue and logs')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    Write-Output 'Agent task and all local data removed.'
    return
}

$agentFile = Join-Path $InstallRoot 'SnipeIT.Inventory.Agent.ps1'
if ((Test-Path -LiteralPath $agentFile -PathType Leaf) -and
    $PSCmdlet.ShouldProcess($agentFile, 'Remove executable while preserving config and queued events')) {
    Remove-Item -LiteralPath $agentFile -Force
}
Write-Output 'Agent task removed. Config, keys, queue and logs were preserved.'
