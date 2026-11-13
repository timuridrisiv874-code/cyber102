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
Push-Location -LiteralPath $root
try {
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'tests failed' }
} finally {
    Pop-Location
}
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
Write-Output 'Cyber102 web API ready; Caddy reloaded; startup task installed.'
$visionBackend = 'internvl3'
$visionBackendFile = "$root\var\vision-backend.txt"
try {
    if (Test-Path -LiteralPath $visionBackendFile) {
        $visionBackend = [IO.File]::ReadAllText($visionBackendFile, [Text.Encoding]::UTF8).Trim()
    }
    $visionReady = $false
    if ($visionBackend -ceq 'internvl3') {
        $visionHealth = Invoke-RestMethod 'http://127.0.0.1:11436/health' -TimeoutSec 3
        $visionReady = $visionHealth.status -ceq 'ok' -and $visionHealth.model -ceq 'OpenGVLab/InternVL3-2B-hf' -and $visionHealth.revision -ceq 'cb57a075cb75a2e6d1b668b128d48bb00ae321d2'
    } elseif ($visionBackend -ceq 'ollama') {
        $visionHealth = Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 3
        $visionReady = @($visionHealth.models | Where-Object { $_.model -ceq 'qwen3-vl:8b' -or $_.name -ceq 'qwen3-vl:8b' }).Count -gt 0
    }
    if ($visionReady) {
        Write-Output "Cyber102 vision service ready ($visionBackend). Verify inference with a camera frame."
    } else {
        Write-Warning 'Cyber102 vision is not ready. Check the selected backend and expected model; the web API is ready.'
    }
} catch {
    Write-Warning 'Cyber102 vision is not ready. The configured service did not respond or its configuration could not be read; the web API is ready.'
}
Get-ScheduledTask -TaskName $task | Select-Object TaskName,State | ConvertTo-Json
