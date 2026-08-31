#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$AgentVersion = '1.0.0'
$KdfContext = 'snipeit-inventory-gateway/v1'
$CredentialEntropy = 'SnipeIT Inventory Gateway SMTP credential v1'

function ConvertTo-CanonicalJson {
    param($Value)
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [string]) { return ($Value | ConvertTo-Json -Compress) }
    if ($Value -is [bool]) { return $(if ($Value) { 'true' } else { 'false' }) }
    if ($Value -is [System.Collections.IDictionary] -or $Value -is [pscustomobject]) {
        $pairs = New-Object System.Collections.Generic.List[string]
        if ($Value -is [System.Collections.IDictionary]) {
            $names = @($Value.Keys)
        } else {
            $names = @($Value.PSObject.Properties.Name)
        }
        foreach ($name in @($names | Sort-Object)) {
            $item = if ($Value -is [System.Collections.IDictionary]) { $Value[$name] } else { $Value.$name }
            $pairs.Add("$(ConvertTo-CanonicalJson ([string]$name)):$(ConvertTo-CanonicalJson $item)")
        }
        return '{' + ($pairs -join ',') + '}'
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        return '[' + ((@($Value) | ForEach-Object { ConvertTo-CanonicalJson $_ }) -join ',') + ']'
    }
    return [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') }) }
    finally { $sha.Dispose() }
}

function Invoke-Hkdf([byte[]]$Ikm, [byte[]]$Salt, [byte[]]$Info, [int]$Length) {
    $extractor = New-Object Security.Cryptography.HMACSHA256 -ArgumentList (, $Salt)
    try { $prk = $extractor.ComputeHash($Ikm) } finally { $extractor.Dispose() }
    $output = New-Object System.Collections.Generic.List[byte]
    [byte[]]$previous = @()
    try {
        for ([byte]$counter = 1; $output.Count -lt $Length; $counter++) {
            $hmac = New-Object Security.Cryptography.HMACSHA256 -ArgumentList (, $prk)
            try {
                [byte[]]$block = @($previous + $Info + $counter)
                $previous = $hmac.ComputeHash($block)
                $output.AddRange($previous)
            } finally { $hmac.Dispose() }
        }
        return $output.GetRange(0, $Length).ToArray()
    } finally {
        [Array]::Clear($prk, 0, $prk.Length)
        if ($previous.Length -gt 0) { [Array]::Clear($previous, 0, $previous.Length) }
    }
}

function ConvertTo-Envelope([hashtable]$Payload, [string]$KeyId, [byte[]]$MasterKey) {
    $withoutId = @{}
    foreach ($key in $Payload.Keys) {
        if ($key -ne 'event_id') { $withoutId[$key] = $Payload[$key] }
    }
    $Payload['event_id'] = Get-Sha256Hex ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $withoutId)))
    [byte[]]$salt = New-Object byte[] 32
    [byte[]]$iv = New-Object byte[] 16
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($salt); $random.GetBytes($iv) } finally { $random.Dispose() }
    [byte[]]$derived = Invoke-Hkdf $MasterKey $salt ([Text.Encoding]::ASCII.GetBytes($KdfContext)) 64
    [byte[]]$encryptionKey = $derived[0..31]
    [byte[]]$macKey = $derived[32..63]
    try {
        $plaintext = [Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $Payload))
        $aes = [Security.Cryptography.Aes]::Create()
        $aes.Mode = [Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = $encryptionKey
        $aes.IV = $iv
        try {
            $encryptor = $aes.CreateEncryptor()
            try { $cipher = $encryptor.TransformFinalBlock($plaintext, 0, $plaintext.Length) }
            finally { $encryptor.Dispose() }
        } finally { $aes.Dispose(); [Array]::Clear($plaintext, 0, $plaintext.Length) }
        $envelope = [ordered]@{
            version = 1
            algorithm = 'AES-256-CBC+HMAC-SHA256'
            key_id = $KeyId
            event_id = $Payload.event_id
            sent_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
            salt = [Convert]::ToBase64String($salt)
            iv = [Convert]::ToBase64String($iv)
            ciphertext = [Convert]::ToBase64String($cipher)
        }
        $mac = New-Object Security.Cryptography.HMACSHA256 -ArgumentList (, $macKey)
        try {
            $envelope['hmac_sha256'] = -join ($mac.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $envelope))
            ) | ForEach-Object { $_.ToString('x2') })
        } finally { $mac.Dispose() }
        return $envelope
    } finally {
        [Array]::Clear($derived, 0, $derived.Length)
        [Array]::Clear($encryptionKey, 0, $encryptionKey.Length)
        [Array]::Clear($macKey, 0, $macKey.Length)
    }
}

function Test-AgentConfiguration($Settings) {
    $required = @(
        'SchemaVersion', 'GatewayUrl', 'KeyId', 'MasterKeyPath', 'EnableSmtpFallback',
        'SmtpCredentialPath', 'SmtpHost', 'SmtpPort', 'MailFrom', 'MailTo',
        'StatePath', 'QueuePath', 'LogPath', 'MaxQueueAttemptsPerRun'
    )
    foreach ($name in $required) {
        if ($Settings.PSObject.Properties.Name -notcontains $name) { throw "Missing config field: $name" }
    }
    if ([int]$Settings.SchemaVersion -ne 1) { throw 'Unsupported config schema' }
    $gateway = $null
    if (-not [Uri]::TryCreate([string]$Settings.GatewayUrl, [UriKind]::Absolute, [ref]$gateway) -or
        $gateway.Scheme -ne 'https' -or $gateway.AbsolutePath -ne '/api/v1/events') {
        throw 'GatewayUrl must be an absolute HTTPS URL ending in /api/v1/events'
    }
    if ([string]::IsNullOrWhiteSpace([string]$Settings.KeyId) -or ([string]$Settings.KeyId).Length -gt 64) {
        throw 'Invalid KeyId'
    }
    if ([int]$Settings.MaxQueueAttemptsPerRun -lt 1 -or [int]$Settings.MaxQueueAttemptsPerRun -gt 50) {
        throw 'MaxQueueAttemptsPerRun must be between 1 and 50'
    }
    if ([bool]$Settings.EnableSmtpFallback) {
        if ([string]::IsNullOrWhiteSpace([string]$Settings.SmtpHost) -or [int]$Settings.SmtpPort -ne 587) {
            throw 'SMTP fallback must use a configured STARTTLS host on port 587'
        }
        if ([string]$Settings.MailFrom -notmatch '^[^@\s]+@[^@\s]+$' -or
            [string]$Settings.MailTo -notmatch '^[^@\s]+@[^@\s]+$') {
            throw 'SMTP sender and recipient must be valid configured addresses'
        }
    }
}

function Write-AgentLog([string]$Level, [string]$Message) {
    try {
        $path = [string]$script:Settings.LogPath
        $directory = Split-Path -Parent $path
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and (Get-Item -LiteralPath $path).Length -gt 2097152) {
            Move-Item -LiteralPath $path -Destination "$path.1" -Force
        }
        $safe = $Message -replace '[\r\n]+', ' '
        Add-Content -LiteralPath $path -Encoding UTF8 -Value (
            '{0} [{1}] {2}' -f [DateTime]::UtcNow.ToString('o'), $Level.ToUpperInvariant(), $safe
        )
    } catch {
        Write-Verbose "Agent log write failed: $($_.Exception.GetType().Name)"
    }
}

function Get-InteractiveIdentity($Computer) {
    $candidates = New-Object System.Collections.Generic.List[object]
    if (-not [string]::IsNullOrWhiteSpace([string]$Computer.UserName)) {
        $candidates.Add([ordered]@{ value = [string]$Computer.UserName; source = 'Win32_ComputerSystem'; confidence = 'high' })
    }
    try {
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'")) {
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
            if ($owner.ReturnValue -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$owner.User)) {
                $value = if ($owner.Domain) { "$($owner.Domain)\$($owner.User)" } else { [string]$owner.User }
                $candidates.Add([ordered]@{ value = $value; source = 'explorer.exe'; confidence = 'high' })
            }
        }
    } catch {
        Write-AgentLog 'warning' "Interactive session lookup failed: $($_.Exception.GetType().Name)"
    }
    $blockedExact = @('administrator', 'guest', 'krbtgt', 'system', 'shared-terminal', 'snipeit')
    $blockedPrefixes = @('ad_', 'svc_', 'service_', 'dwm-', 'umfd-')
    $seen = @{}
    foreach ($candidate in $candidates) {
        $raw = [string]$candidate.value
        $normalized = $raw.Trim().ToLowerInvariant()
        if ($normalized.Contains('\')) { $normalized = $normalized.Substring($normalized.LastIndexOf('\') + 1) }
        if ($normalized.Contains('@')) { $normalized = $normalized.Substring(0, $normalized.IndexOf('@')) }
        if ($seen.ContainsKey($normalized)) { continue }
        $seen[$normalized] = $true
        if ($normalized.EndsWith('$') -or $blockedExact -contains $normalized) { continue }
        if (@($blockedPrefixes | Where-Object { $normalized.StartsWith($_) }).Count -gt 0) { continue }
        if ($normalized -notmatch '^[a-z0-9._-]{1,128}$') { continue }
        return [ordered]@{
            detected_username = $raw
            observed_account = $normalized
            source = [string]$candidate.source
            confidence = [string]$candidate.confidence
        }
    }
    return [ordered]@{
        detected_username = ''
        observed_account = ''
        source = 'none'
        confidence = 'none'
    }
}

function Get-InventoryPayload {
    $computer = Get-CimInstance Win32_ComputerSystem
    $bios = Get-CimInstance Win32_BIOS
    $systemProduct = Get-CimInstance Win32_ComputerSystemProduct
    $operatingSystem = Get-CimInstance Win32_OperatingSystem
    $processors = @(Get-CimInstance Win32_Processor | ForEach-Object {
        [ordered]@{ name = [string]$_.Name; cores = [int]$_.NumberOfCores; threads = [int]$_.NumberOfLogicalProcessors }
    })
    $disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
        [ordered]@{ device = [string]$_.DeviceID; size_bytes = [int64]$_.Size; free_bytes = [int64]$_.FreeSpace }
    })
    $serial = ([string]$bios.SerialNumber).Trim()
    $genericSerials = @('', 'default string', 'system serial number', 'to be filled by o.e.m.', 'none', 'unknown')
    if ($genericSerials -contains $serial.ToLowerInvariant()) {
        $uuid = ([string]$systemProduct.UUID).Trim()
        if ([string]::IsNullOrWhiteSpace($uuid)) { throw 'Neither BIOS serial nor hardware UUID is available' }
        $serial = "UUID-$uuid"
    }
    $identity = Get-InteractiveIdentity $computer
    $inventory = [ordered]@{
        name = [string]$env:COMPUTERNAME
        notes = "Inventory observed by agent $AgentVersion"
        custom_fields = [ordered]@{
            manufacturer = [string]$computer.Manufacturer
            model = [string]$computer.Model
            ram_bytes = [int64]$computer.TotalPhysicalMemory
            cpu = $processors
            os = [string]$operatingSystem.Caption
            os_version = [string]$operatingSystem.Version
            os_build = [string]$operatingSystem.BuildNumber
            disks = $disks
            agent_version = $AgentVersion
        }
    }
    return [ordered]@{
        schema_version = 1
        event_id = ''
        event_type = 'inventory'
        event_generation = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        observed_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        computer_name = [string]$env:COMPUTERNAME
        serial_number = $serial
        identity = $identity
        inventory = $inventory
        agent = [ordered]@{ name = 'SnipeIT Inventory Agent'; version = $AgentVersion; transport = 'https+smtp+queue' }
    }
}

function Get-SmtpCredential($Settings) {
    $path = [string]$Settings.SmtpCredentialPath
    if ([IO.Path]::GetExtension($path).ToLowerInvariant() -eq '.xml') {
        return Import-Clixml -LiteralPath $path
    }
    $document = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ([int]$document.SchemaVersion -ne 1 -or [string]$document.Scope -ne 'LocalMachine') {
        throw 'Unsupported SMTP credential format'
    }
    $entropy = [Text.Encoding]::UTF8.GetBytes($CredentialEntropy)
    [byte[]]$cipherBytes = [Convert]::FromBase64String([string]$document.CipherText)
    [byte[]]$plainBytes = $null
    [char[]]$passwordChars = $null
    $securePassword = New-Object Security.SecureString
    try {
        $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
            $cipherBytes,
            $entropy,
            [Security.Cryptography.DataProtectionScope]::LocalMachine
        )
        $passwordChars = [Text.Encoding]::UTF8.GetChars($plainBytes)
        foreach ($passwordChar in $passwordChars) { $securePassword.AppendChar($passwordChar) }
        $securePassword.MakeReadOnly()
        return New-Object Management.Automation.PSCredential([string]$document.UserName, $securePassword)
    } catch {
        $securePassword.Dispose()
        throw
    } finally {
        if ($null -ne $passwordChars) { [Array]::Clear($passwordChars, 0, $passwordChars.Length) }
        if ($null -ne $plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
        [Array]::Clear($cipherBytes, 0, $cipherBytes.Length)
        [Array]::Clear($entropy, 0, $entropy.Length)
    }
}

function Send-FallbackMail($Settings, $Envelope) {
    if (-not [bool]$Settings.EnableSmtpFallback) { throw 'SMTP fallback is disabled' }
    $credential = Get-SmtpCredential $Settings
    if ($credential.UserName.ToLowerInvariant() -ne ([string]$Settings.MailFrom).ToLowerInvariant()) {
        throw 'SMTP credential identity mismatch'
    }
    $eventId = [string]$Envelope.event_id
    $message = New-Object Net.Mail.MailMessage($Settings.MailFrom, $Settings.MailTo)
    $message.Subject = "[SNIPEIT-INVENTORY] RELAY: $env:COMPUTERNAME $($eventId.Substring(0, 16))"
    $message.Headers.Add('X-SnipeIT-Relay', '1')
    $message.Body = 'Encrypted SnipeIT Inventory fallback event.'
    $temp = Join-Path $env:TEMP ("inventory-{0}.snipeit-event.json" -f $eventId)
    try {
        [IO.File]::WriteAllText($temp, (ConvertTo-CanonicalJson $Envelope), (New-Object Text.UTF8Encoding($false)))
        $message.Attachments.Add((New-Object Net.Mail.Attachment($temp, 'application/json')))
        $smtp = New-Object Net.Mail.SmtpClient($Settings.SmtpHost, [int]$Settings.SmtpPort)
        $smtp.EnableSsl = $true
        $smtp.Credentials = $credential.GetNetworkCredential()
        try { $smtp.Send($message) } finally { $smtp.Dispose() }
    } finally {
        $message.Dispose()
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}

function Send-Envelope($Settings, $Envelope) {
    $body = ConvertTo-CanonicalJson $Envelope
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $Settings.GatewayUrl `
            -ContentType 'application/json' -Headers @{ 'User-Agent' = "SnipeIT-Inventory-Agent/$AgentVersion" } `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 20
        if ([int]$response.StatusCode -lt 200 -or [int]$response.StatusCode -ge 300) {
            throw "Gateway returned HTTP $($response.StatusCode)"
        }
        return 'https'
    } catch {
        Write-AgentLog 'warning' "HTTPS delivery failed: $($_.Exception.GetType().Name)"
    }
    try {
        Send-FallbackMail $Settings $Envelope
        return 'smtp'
    } catch {
        Write-AgentLog 'warning' "SMTP delivery failed or disabled: $($_.Exception.GetType().Name)"
        return $null
    }
}

function Save-QueuedEnvelope($Settings, $Envelope) {
    New-Item -ItemType Directory -Path $Settings.QueuePath -Force | Out-Null
    $path = Join-Path $Settings.QueuePath ("{0}.snipeit-event.json" -f $Envelope.event_id)
    if (-not (Test-Path -LiteralPath $path)) {
        [IO.File]::WriteAllText($path, (ConvertTo-CanonicalJson $Envelope), (New-Object Text.UTF8Encoding($false)))
    }
    Get-ChildItem -LiteralPath $Settings.QueuePath -Filter '*.snipeit-event.json' -File |
        Where-Object LastWriteTimeUtc -lt ([DateTime]::UtcNow.AddDays(-30)) |
        Remove-Item -Force
    $items = @(Get-ChildItem -LiteralPath $Settings.QueuePath -Filter '*.snipeit-event.json' -File |
        Sort-Object LastWriteTimeUtc -Descending)
    if ($items.Count -gt 200) { $items[200..($items.Count - 1)] | Remove-Item -Force }
}

function Send-LocalQueue($Settings) {
    if (-not (Test-Path -LiteralPath $Settings.QueuePath)) { return 0 }
    $sent = 0
    $limit = [int]$Settings.MaxQueueAttemptsPerRun
    foreach ($item in @(Get-ChildItem -LiteralPath $Settings.QueuePath -Filter '*.snipeit-event.json' -File |
        Sort-Object LastWriteTimeUtc | Select-Object -First $limit)) {
        try {
            $queued = Get-Content -LiteralPath $item.FullName -Raw | ConvertFrom-Json
            $transport = Send-Envelope $Settings $queued
            if ($null -ne $transport) {
                Remove-Item -LiteralPath $item.FullName -Force
                $sent++
            } else { break }
        } catch {
            Write-AgentLog 'error' "Queued event could not be read or delivered: $($_.Exception.GetType().Name)"
            break
        }
    }
    return $sent
}

function Write-AgentState($Settings, [string]$EventId, [string]$Delivery, [int]$QueueSent) {
    $statePath = [string]$Settings.StatePath
    $directory = Split-Path -Parent $statePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $queued = @(Get-ChildItem -LiteralPath $Settings.QueuePath -Filter '*.snipeit-event.json' -File -ErrorAction SilentlyContinue).Count
    $state = [ordered]@{
        schema_version = 1
        agent_version = $AgentVersion
        last_run_utc = [DateTime]::UtcNow.ToString('o')
        last_event_id = $EventId
        last_delivery = $Delivery
        queued_events = $queued
        queued_events_sent_this_run = $QueueSent
    }
    $temp = "$statePath.tmp.$PID"
    [IO.File]::WriteAllText($temp, ($state | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $statePath -Force
}

try {
    $script:Settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
} catch {
    throw 'Agent config is not valid JSON'
}
Test-AgentConfiguration $script:Settings
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$stateDirectory = Split-Path -Parent ([string]$script:Settings.StatePath)
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$lockPath = Join-Path $stateDirectory 'agent.lock'
$lockStream = $null
try {
    try {
        $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        Write-AgentLog 'info' 'Another agent instance is already running'
        exit 0
    }

    $masterText = (Get-Content -LiteralPath $script:Settings.MasterKeyPath -Raw).Trim()
    if (-not $masterText.StartsWith('base64:')) { throw 'Invalid master key encoding' }
    [byte[]]$masterKey = [Convert]::FromBase64String($masterText.Substring(7))
    try {
        if ($masterKey.Length -ne 32) { throw 'Master key must be 256 bits' }
        $envelope = ConvertTo-Envelope (Get-InventoryPayload) $script:Settings.KeyId $masterKey
    } finally { [Array]::Clear($masterKey, 0, $masterKey.Length) }

    if ($DryRun) {
        Write-Output "dry-run event_id=$($envelope.event_id) version=$AgentVersion"
        exit 0
    }

    $queueSent = Send-LocalQueue $script:Settings
    $delivery = Send-Envelope $script:Settings $envelope
    if ($null -eq $delivery) {
        Save-QueuedEnvelope $script:Settings $envelope
        $delivery = 'local_queue'
        Write-AgentLog 'warning' "Event $($envelope.event_id) stored in encrypted local queue"
    } else {
        Write-AgentLog 'info' "Event $($envelope.event_id) delivered via $delivery"
    }
    Write-AgentState $script:Settings $envelope.event_id $delivery $queueSent
} catch {
    Write-AgentLog 'error' "Agent failed: $($_.Exception.GetType().Name)"
    throw
} finally {
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
