#Requires -Version 7.0
<#
.SYNOPSIS
  Verify Chromagen Midea 170L over EW-11A Modbus TCP (before HACS).

.EXAMPLE
  .\verify\Test-Midea170.ps1 ping
  .\verify\Test-Midea170.ps1 status
  .\verify\Test-Midea170.ps1 selftest
  .\verify\Test-Midea170.ps1 selftest -Writes
  .\verify\Test-Midea170.ps1 web
  .\verify\Test-Midea170.ps1 power on
  .\verify\Test-Midea170.ps1 mode eco
  .\verify\Test-Midea170.ps1 target 62
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('ping', 'status', 'selftest', 'web', 'power', 'mode', 'target', 'sterilize')]
    [string] $Command = 'status',

    [Parameter(Position = 1)]
    [string] $Arg,

    [string] $HostName = '192.168.31.219',
    [int] $Port = 502,
    [int] $Unit = 1,
    [switch] $Writes,
    [string] $Listen = '127.0.0.1',
    [int] $HttpPort = 8765
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$py = Get-Command python -ErrorAction Stop
$cli = Join-Path $PSScriptRoot 'cli.py'
$web = Join-Path $PSScriptRoot 'web_app.py'
$common = @('--host', $HostName, '--port', "$Port", '--unit', "$Unit")

switch ($Command) {
    'web' {
        & $py.Source $web @common --listen $Listen --http-port $HttpPort
    }
    'selftest' {
        $extra = @()
        if ($Writes) { $extra += '--writes' }
        & $py.Source $cli @common selftest @extra
        exit $LASTEXITCODE
    }
    'ping' { & $py.Source $cli @common ping; exit $LASTEXITCODE }
    'status' { & $py.Source $cli @common status; exit $LASTEXITCODE }
    default {
        if (-not $Arg) { throw "$Command needs an argument" }
        & $py.Source $cli @common $Command $Arg
        exit $LASTEXITCODE
    }
}
