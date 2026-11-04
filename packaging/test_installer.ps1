# Install PingerPlot-Setup.exe silently for this user, check what it installed,
# run the installed app's self-test against a scratch data folder, uninstall,
# and check the uninstall. CI runs it; on a PC of your own it REPLACES an
# installed copy of PingerPlot.
#
#   pwsh packaging/test_installer.ps1 -Setup dist\PingerPlot-Setup.exe -Version 1.4.0
param(
    [Parameter(Mandatory)][string]$Setup,
    [Parameter(Mandatory)][string]$Version
)
$ErrorActionPreference = 'Stop'

$dir = Join-Path $env:LOCALAPPDATA 'Programs\PingerPlot'
$log = Join-Path ([IO.Path]::GetTempPath()) "pingerplot-setup-$PID.log"
$p = Start-Process $Setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$log`"" -Wait -PassThru
if ($p.ExitCode -ne 0) { Get-Content $log -ErrorAction SilentlyContinue; throw "Setup exited with $($p.ExitCode)" }

foreach ($f in 'PingerPlot.exe', 'pingerplot-headless.exe', 'unins000.exe', 'LICENSE') {
    if (-not (Test-Path (Join-Path $dir $f))) { throw "nothing installed at $dir\$f" }
}
if (Test-Path (Join-Path $dir 'portable.txt')) { throw "the installed copy has portable.txt: it would keep settings in Program files" }

$entry = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
    ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -eq 'PingerPlot' }
if (-not $entry) { throw "no entry in Settings > Apps" }
if ($entry.DisplayVersion -ne $Version) { throw "Settings > Apps says $($entry.DisplayVersion), not $Version" }
$lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'PingerPlot.lnk'
if (-not (Test-Path $lnk)) { throw "no Start menu shortcut at $lnk" }

$data = Join-Path ([IO.Path]::GetTempPath()) "pingerplot-installed-data-$PID"
New-Item -ItemType Directory $data | Out-Null
$env:PINGERPLOT_DATA_DIR = $data
try {
    $t = Start-Process (Join-Path $dir 'PingerPlot.exe') -ArgumentList '--self-test' -Wait -PassThru
} finally {
    Remove-Item Env:PINGERPLOT_DATA_DIR
}
Get-Content (Join-Path $data 'self-test.txt')
if ($t.ExitCode -ne 0) { throw "the installed copy's self-test failed (exit $($t.ExitCode))" }

$u = Start-Process (Join-Path $dir 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($u.ExitCode -ne 0) { throw "the uninstaller exited with $($u.ExitCode)" }
# The uninstaller hands off to a copy of itself in %TEMP% to delete its own exe.
for ($i = 0; $i -lt 60 -and (Test-Path (Join-Path $dir 'PingerPlot.exe')); $i++) { Start-Sleep -Milliseconds 500 }
if (Test-Path (Join-Path $dir 'PingerPlot.exe')) { throw "uninstalling left the program behind" }
if (Test-Path $lnk) { throw "uninstalling left the Start menu shortcut" }

Remove-Item -Recurse -Force $data
"installer ok"
