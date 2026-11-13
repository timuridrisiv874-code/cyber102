# Run after copying the release into C:\cyber102 and provisioning private/auth.caddy.
$ErrorActionPreference = 'Stop'
$root = 'C:\cyber102'
$basePython = 'C:\Users\fatik\AppData\Local\Programs\Python\Python311\python.exe'
$runtime = "$root\runtime"
$python = "$root\.venv\Scripts\python.exe"
$caddy = 'C:\elektro62\caddy\caddy.exe'
$config = 'C:\elektro62\caddy\Caddyfile'
$task = 'Cyber102Web'
New-Item -ItemType Directory -Force "$root\var" | Out-Null
# The user-installed Python is private to fatik. Copy only its standard runtime,
# without granting LOCAL SERVICE access to the user's home or third-party packages.
if (!(Test-Path "$runtime\python.exe")) {
    $base = Split-Path $basePython
    New-Item -ItemType Directory -Force $runtime | Out-Null
    Copy-Item "$base\python*.exe","$base\python*.dll","$base\vcruntime*.dll" $runtime
    & robocopy "$base\Lib" "$runtime\Lib" /E /XD site-packages __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Python standard library copy failed' }
    & robocopy "$base\DLLs" "$runtime\DLLs" /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Python DLL copy failed' }
}
$basePython = "$runtime\python.exe"
if (!(Test-Path $python)) {
    & $basePython -m venv "$root\.venv"
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
} elseif (!(Get-Content "$root\.venv\pyvenv.cfg" -Raw).Contains($runtime)) {
    & $basePython -m venv --upgrade "$root\.venv"
    if ($LASTEXITCODE -ne 0) { throw 'venv migration failed' }
}
if (Test-Path "$root\wheelhouse") {
    & $python -m pip install --disable-pip-version-check --no-index --find-links "$root\wheelhouse" -r "$root\requirements.txt"
} else {
    & $python -m pip install --disable-pip-version-check --timeout 15 --retries 1 -r "$root\requirements.txt"
}
if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }
& $python -m unittest discover -s "$root\tests" -v
if ($LASTEXITCODE -ne 0) { throw 'tests failed' }
# LOCAL SERVICE may read program files and write only the runtime directory.
& icacls $root /grant '*S-1-5-19:(OI)(CI)(RX)' | Out-Null
& icacls "$root\var" /grant '*S-1-5-19:(OI)(CI)(M)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'runtime directory permissions failed' }
$action = New-ScheduledTaskAction -Execute $python -Argument '-u C:\cyber102\deploy\run.py' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCAL SERVICE' -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $task
    Start-Sleep -Seconds 2
}
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task
$healthy = $false
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8102/api/health' -TimeoutSec 3
        if ($health.status -eq 'ok') { $healthy = $true; break }
    } catch { }
}
if (!$healthy) { throw 'Cyber102 backend failed health check; proxy unchanged' }
$original = [IO.File]::ReadAllText($config)
$import = 'import C:/cyber102/deploy/Caddyfile'
$candidate = $original
if (!$original.Contains($import)) { $candidate += "`r`n`r`n$import`r`n" }
$candidateFile = "$config.cyber102-candidate"
[IO.File]::WriteAllText($candidateFile, $candidate, [Text.UTF8Encoding]::new($false))
& $caddy validate --config $candidateFile --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw 'Caddy validation failed; proxy unchanged' }
$backup = "$config.before-cyber102-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $config $backup
[IO.File]::WriteAllText($config, $candidate, [Text.UTF8Encoding]::new($false))
& $caddy reload --config $config --adapter caddyfile
if ($LASTEXITCODE -ne 0) {
    Copy-Item $backup $config -Force
    & $caddy reload --config $config --adapter caddyfile
    throw 'Caddy reload failed; original configuration restored'
}
Write-Output 'Cyber102 backend healthy; Caddy reloaded; startup task installed.'
Get-ScheduledTask -TaskName $task | Select-Object TaskName,State | ConvertTo-Json
