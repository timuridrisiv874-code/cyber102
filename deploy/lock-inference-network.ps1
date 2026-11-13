# Run only after downloading model weights/dependencies. Scope: Cyber102's private Python runtime.
# Caddy, system Python and the user's other applications are not changed.
$ErrorActionPreference='Stop'
$programs=@{
    'Cyber102-InternVL-Python-Outbound'='C:\cyber102\.venv-internvl\Scripts\python.exe'
    'Cyber102-PrivateRuntime-Outbound'='C:\cyber102\runtime\python.exe'
}
foreach ($name in $programs.Keys) {
    if (!(Test-Path $programs[$name])) { throw "Missing Cyber102 runtime: $($programs[$name])" }
    if (!(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $name -DisplayName $name -Group 'Cyber102' -Direction Outbound -Action Block -Program $programs[$name] -Profile Any | Out-Null
    }
}
Get-NetFirewallRule -Group 'Cyber102' | Select-Object Name,Enabled,Direction,Action
