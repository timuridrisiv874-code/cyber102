param(
    [Parameter(Mandatory=$true)][string]$Meshroom,
    [Parameter(Mandatory=$true)][string]$PublicUrl,
    [string]$AppRoot = 'C:\cyber102',
    [string]$Python = 'python',
    [string]$Wheelhouse
)
$ErrorActionPreference = 'Stop'
$task = 'Cyber102Meshroom'
$ffmpeg = (Get-Command ffmpeg.exe).Source
$Meshroom = (Resolve-Path -LiteralPath $Meshroom).Path
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Cyber102Meshroom is already installed.'
}
$root = Join-Path $AppRoot 'var\mesh-service'
$script = Join-Path $AppRoot 'server\mesh_service.py'
$requirements = Join-Path $AppRoot 'deploy\mesh-requirements.txt'
New-Item -ItemType Directory -Path $root -Force | Out-Null
& $Python -m venv (Join-Path $root 'venv')
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Meshroom service environment.' }
$runtime = Join-Path $root 'venv\Scripts\python.exe'
$pipArgs = @('-m', 'pip', 'install', '-r', $requirements)
if ($Wheelhouse) { $pipArgs += @('--no-index', '--find-links', $Wheelhouse) }
& $runtime @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'Could not install Meshroom service dependencies.' }
$arguments = "-u `"$script`" --origin `"$PublicUrl`" --root `"$root\jobs`" --ffmpeg `"$ffmpeg`" --meshroom `"$Meshroom`""
$action = New-ScheduledTaskAction -Execute $runtime -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest | Out-Null
Start-ScheduledTask -TaskName $task
