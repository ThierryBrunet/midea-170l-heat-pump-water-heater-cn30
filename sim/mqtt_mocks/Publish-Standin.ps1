# Load MQTT creds from SecretStore and publish the HP170 MQTT stand-in. Never print secret values.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

& "$env:USERPROFILE\.grok\skills\secretstore-get\scripts\Invoke-WithSecrets.ps1" `
    -Mappings @(
        @{ SecretName = "Frigate_MQTT_MazeppaHome_SecObj"; EnvVar = "MQTT_PASSWORD" }
        @{ SecretName = "Frigate_MQTT_MazeppaHome_SecObj"; EnvVar = "MQTT_USER"; Property = "AssignedTo" }
        @{ SecretName = "Frigate_MQTT_MazeppaHome_SecObj"; EnvVar = "MQTT_URL"; Property = "Url" }
    ) `
    -ScriptBlock {
        $url = $env:MQTT_URL
        if ($url -match "mqtt://([^:/]+):?(\d+)?") {
            $env:MQTT_HOST = $Matches[1]
            if ($Matches[2]) { $env:MQTT_PORT = $Matches[2] } else { $env:MQTT_PORT = "1883" }
        }
        Write-Host "MQTT host set: $($null -ne $env:MQTT_HOST)"
        python "$here\hws_standin_mock.py"
    }
