$ErrorActionPreference='Stop'
$root='C:\cyber102'
$ollama='C:\Users\fatik\AppData\Local\Programs\Ollama\ollama.exe'
if (!(Test-Path $ollama)) { throw 'Install Ollama and download qwen3-vl:8b locally first' }
if (!(Test-Path 'C:\Users\fatik\.ollama\models\manifests\registry.ollama.ai\library\qwen3-vl\8b')) {
    throw 'qwen3-vl:8b model not found locally; no automatic model download'
}
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\cyber102\deploy\vision.ps1' -WorkingDirectory $root
$principal=New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\fatik" -LogonType S4U -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName Cyber102Vision -Action $action -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtStartup) -Force | Out-Null
Start-ScheduledTask -TaskName Cyber102Vision
Write-Output 'Cyber102Vision scheduled on loopback 11435; images stay on this host.'
