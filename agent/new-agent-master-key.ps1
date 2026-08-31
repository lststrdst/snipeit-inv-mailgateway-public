#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$resolved = [IO.Path]::GetFullPath($Path)
if ((Test-Path -LiteralPath $resolved) -and -not $Force) {
    throw 'Refusing to overwrite an existing key without -Force'
}

[byte[]]$key = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($key)
    if ($PSCmdlet.ShouldProcess($resolved, 'Create a new 256-bit inventory event key')) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $resolved) -Force | Out-Null
        [IO.File]::WriteAllText(
            $resolved,
            'base64:' + [Convert]::ToBase64String($key),
            (New-Object Text.UTF8Encoding($false))
        )
    }
} finally {
    $random.Dispose()
    [Array]::Clear($key, 0, $key.Length)
}

Write-Output "New event key written to $resolved; key material was not printed."
