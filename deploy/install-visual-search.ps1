# Copy models/visual-search/model.safetensors from the prepared release before running.
param([switch]$SkipWebRestart)
$ErrorActionPreference = 'Stop'
$root = 'C:\cyber102'
$python = "$root\.venv-visual\Scripts\python.exe"
$task = 'Cyber102VisualSearch'
if (!(Test-Path "$root\models\visual-search\model.safetensors")) { throw 'Copy the verified visual model into models/visual-search first' }
if (!(Test-Path $python)) {
    & "$root\runtime\python.exe" -m venv "$root\.venv-visual"
    if ($LASTEXITCODE -ne 0) { throw 'Visual environment creation failed' }
}
if (Test-Path "$root\wheelhouse-visual") {
    & $python -m pip install --no-index --find-links "$root\wheelhouse-visual" -r "$root\vision_runtime\visual-requirements.txt"
} else {
    & $python -m pip install 'torch==2.8.0' 'torchvision==0.23.0' --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw 'CPU dependencies failed' }
    & $python -m pip install -r "$root\vision_runtime\visual-requirements.txt"
}
if ($LASTEXITCODE -ne 0) { throw 'Visual dependencies failed' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Visual dependencies are inconsistent' }
& icacls "$root\.venv-visual" /grant '*S-1-5-19:(OI)(CI)(RX)' | Out-Null
& icacls "$root\models\visual-search" /grant '*S-1-5-19:(OI)(CI)(RX)' | Out-Null
& icacls "$root\var" /grant '*S-1-5-19:(OI)(CI)(M)' | Out-Null
$action = New-ScheduledTaskAction -Execute $python -Argument '-u C:\cyber102\deploy\run-visual.py' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCAL SERVICE' -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $task }
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:11437/health' -TimeoutSec 2
        if ($health.status -ceq 'ok' -and $health.device -ceq 'cpu' -and $health.count -gt 0) { $ready = $true; break }
    } catch { }
}
if (!$ready) { throw 'Visual search did not start; inspect var/visual-search.log' }
if (!$SkipWebRestart) {
    # Allow existing inspections to complete before restarting the web API.
    $idle = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $web = Invoke-RestMethod 'http://127.0.0.1:8102/api/health' -TimeoutSec 3
        if (!$web.analysis.running -and $web.analysis.waiting -eq 0) { $idle = $true; break }
        Start-Sleep -Seconds 2
    }
    if (!$idle) { throw 'Visual search is ready, but the web analysis queue is busy. Restart Cyber102Web after it drains.' }
    Stop-ScheduledTask -TaskName Cyber102Web
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName Cyber102Web
}
Write-Output "Visual search ready on CPU; $($health.count) reference photos. Verify /api/equipment/similar through HTTPS."
