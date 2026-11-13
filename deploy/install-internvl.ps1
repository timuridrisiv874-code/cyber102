# Run after installing vision_runtime/requirements.txt and the pinned local weights.
$ErrorActionPreference='Stop'
$root='C:\cyber102'
$python="$root\.venv-internvl\Scripts\python.exe"
if (!(Test-Path $python)) { throw 'Install the isolated .venv-internvl Python environment first' }
if (!(Test-Path "$root\models\internvl3_5-2b-hf\cyber102-manifest.json")) { throw 'Download and verify the pinned InternVL3.5 model first' }
$task='Cyber102InternVL'
$action=New-ScheduledTaskAction -Execute $python -Argument '-u C:\cyber102\deploy\run-internvl.py' -WorkingDirectory $root
$principal=New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\fatik" -LogonType S4U -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $task }
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtStartup) -Force | Out-Null
Start-ScheduledTask -TaskName $task
Write-Output 'Cyber102InternVL started; inspect http://127.0.0.1:11436/health before switching the web API.'
