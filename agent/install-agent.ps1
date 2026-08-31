#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$SourceDirectory,
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$MasterKeyPath,
    [PSCredential]$SmtpCredential,
    [switch]$NoSmtpFallback,
    [switch]$SkipScheduledTask,
    [string]$InstallRoot = (Join-Path $env:ProgramData 'SnipeIT Inventory Gateway'),
    [string]$TaskPath = '\ExampleOrg\',
    [string]$TaskName = 'SnipeIT Inventory Collection'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$agentVersion = '1.0.0'

function Assert-InputFile {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description does not exist: $Path"
    }
}

function Assert-MasterKey {
    param([string]$Path)
    $encoded = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $encoded.StartsWith('base64:')) { throw 'Master key must use base64: encoding' }
    [byte[]]$decoded = $null
    try { $decoded = [Convert]::FromBase64String($encoded.Substring(7)) }
    catch { throw 'Master key is not valid base64' }
    try {
        if ($decoded.Length -ne 32) { throw 'Master key must decode to exactly 32 bytes' }
    } finally {
        if ($null -ne $decoded) { [Array]::Clear($decoded, 0, $decoded.Length) }
    }
}

function Protect-SmtpCredential {
    param([PSCredential]$Credential, [string]$Destination, [string]$ExpectedUser)
    if ($Credential.UserName.ToLowerInvariant() -ne $ExpectedUser.ToLowerInvariant()) {
        throw 'SMTP credential username must match MailFrom in agent config'
    }
    $entropy = [Text.Encoding]::UTF8.GetBytes('SnipeIT Inventory Gateway SMTP credential v1')
    [byte[]]$plainBytes = $null
    [byte[]]$cipherBytes = $null
    $plainText = $null
    try {
        $plainText = $Credential.GetNetworkCredential().Password
        $plainBytes = [Text.Encoding]::UTF8.GetBytes($plainText)
        $cipherBytes = [Security.Cryptography.ProtectedData]::Protect(
            $plainBytes,
            $entropy,
            [Security.Cryptography.DataProtectionScope]::LocalMachine
        )
        $document = [ordered]@{
            SchemaVersion = 1
            UserName = $Credential.UserName
            Scope = 'LocalMachine'
            CipherText = [Convert]::ToBase64String($cipherBytes)
        }
        [IO.File]::WriteAllText(
            $Destination,
            ($document | ConvertTo-Json -Compress),
            (New-Object Text.UTF8Encoding($false))
        )
    } finally {
        $plainText = $null
        if ($null -ne $plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
        if ($null -ne $cipherBytes) { [Array]::Clear($cipherBytes, 0, $cipherBytes.Length) }
        [Array]::Clear($entropy, 0, $entropy.Length)
    }
}

function Initialize-TaskFolder {
    param([string]$Path)
    $normalized = '\' + $Path.Trim('\')
    if ($normalized -eq '\') { return }
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $current = $service.GetFolder('\')
    foreach ($part in $normalized.Trim('\').Split('\')) {
        try { $current = $current.GetFolder($part) }
        catch { $current = $current.CreateFolder($part) }
    }
}

$sourceAgent = Join-Path $SourceDirectory 'SnipeIT.Inventory.Agent.ps1'
Assert-InputFile $sourceAgent 'Agent script'
Assert-InputFile $ConfigPath 'Agent config'
Assert-InputFile $MasterKeyPath 'Master key'
Assert-MasterKey $MasterKeyPath

try { $settings = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json }
catch { throw 'Agent config is not valid JSON' }
if ([int]$settings.SchemaVersion -ne 1) { throw 'Unsupported agent config schema' }
$gatewayUri = $null
if (-not [Uri]::TryCreate([string]$settings.GatewayUrl, [UriKind]::Absolute, [ref]$gatewayUri) -or
    $gatewayUri.Scheme -ne 'https' -or $gatewayUri.AbsolutePath -ne '/api/v1/events') {
    throw 'GatewayUrl must be an absolute HTTPS URL ending in /api/v1/events'
}
if ($NoSmtpFallback -and $null -ne $SmtpCredential) {
    throw 'Use either -SmtpCredential or -NoSmtpFallback, not both'
}

$configDirectory = Join-Path $InstallRoot 'Config'
$stateDirectory = Join-Path $InstallRoot 'State'
$queueDirectory = Join-Path $InstallRoot 'Queue'
$logDirectory = Join-Path $InstallRoot 'Logs'
$agentTarget = Join-Path $InstallRoot 'SnipeIT.Inventory.Agent.ps1'
$previousTarget = "$agentTarget.previous"
$configTarget = Join-Path $configDirectory 'config.json'
$keyTarget = Join-Path $configDirectory 'event-master-key.txt'
$credentialTarget = Join-Path $configDirectory 'smtp-credential.json'
$stateTarget = Join-Path $stateDirectory 'state.json'
$logTarget = Join-Path $logDirectory 'agent.log'

foreach ($path in @($InstallRoot, $configDirectory, $stateDirectory, $queueDirectory, $logDirectory)) {
    if ($PSCmdlet.ShouldProcess($path, 'Create protected directory')) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

if ($PSCmdlet.ShouldProcess($InstallRoot, "Install SnipeIT Inventory Agent v$agentVersion")) {
    if (Test-Path -LiteralPath $agentTarget -PathType Leaf) {
        Copy-Item -LiteralPath $agentTarget -Destination $previousTarget -Force
    }
    Copy-Item -LiteralPath $sourceAgent -Destination $agentTarget -Force
    Copy-Item -LiteralPath $MasterKeyPath -Destination $keyTarget -Force

    $smtpEnabled = $false
    if ($null -ne $SmtpCredential) {
        Protect-SmtpCredential -Credential $SmtpCredential -Destination $credentialTarget -ExpectedUser $settings.MailFrom
        $smtpEnabled = $true
    } elseif (-not $NoSmtpFallback -and (Test-Path -LiteralPath $credentialTarget -PathType Leaf)) {
        $smtpEnabled = $true
    } elseif (-not $NoSmtpFallback) {
        throw 'SMTP credential is required. Pass -SmtpCredential or explicitly use -NoSmtpFallback.'
    }

    $installedValues = [ordered]@{
        MasterKeyPath = $keyTarget
        SmtpCredentialPath = $credentialTarget
        EnableSmtpFallback = $smtpEnabled
        StatePath = $stateTarget
        QueuePath = $queueDirectory
        LogPath = $logTarget
        MaxQueueAttemptsPerRun = 10
    }
    foreach ($entry in $installedValues.GetEnumerator()) {
        if ($settings.PSObject.Properties.Name -contains $entry.Key) {
            $settings.($entry.Key) = $entry.Value
        } else {
            $settings | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $entry.Value
        }
    }
    [IO.File]::WriteAllText(
        $configTarget,
        ($settings | ConvertTo-Json -Depth 10),
        (New-Object Text.UTF8Encoding($false))
    )

    & icacls.exe $InstallRoot /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to apply protected ACL to agent directory' }
}

if ($PSCmdlet.ShouldProcess($agentTarget, 'Run encrypted-event smoke test')) {
    $windowsPowerShell = Join-Path $PSHOME 'powershell.exe'
    & $windowsPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $agentTarget -Config $configTarget -DryRun
    if ($LASTEXITCODE -ne 0) { throw 'Agent dry-run validation failed' }
}

if (-not $SkipScheduledTask -and $PSCmdlet.ShouldProcess("$TaskPath$TaskName", 'Register SYSTEM scheduled task')) {
    Initialize-TaskFolder $TaskPath
    $powerShell = Join-Path $PSHOME 'powershell.exe'
    $arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$agentTarget`" -Config `"$configTarget`""
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments
    $startup = New-ScheduledTaskTrigger -AtStartup
    $startup.Delay = 'PT2M'
    $logon = New-ScheduledTaskTrigger -AtLogOn
    $logon.Delay = 'PT5M'
    $daily = New-ScheduledTaskTrigger -Daily -At '12:00'
    $daily.RandomDelay = 'PT45M'
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action `
        -Trigger @($startup, $logon, $daily) -Principal $principal -Settings $taskSettings `
        -Description "SnipeIT Inventory Agent v${agentVersion}: encrypted HTTPS with SMTP fallback" `
        -Force | Out-Null
}

Write-Output "SnipeIT Inventory Agent v$agentVersion installed in $InstallRoot"
Write-Output "Scheduled task: $TaskPath$TaskName"
