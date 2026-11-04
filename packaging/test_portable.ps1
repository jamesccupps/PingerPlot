# Unzip PingerPlot-win64.zip to a scratch folder, check it is a portable copy
# (its settings stay beside it, never in %APPDATA%), run the app's self-test
# there, and have the headless runner probe loopback.
#
#   pwsh packaging/test_portable.ps1 [-Zip dist\PingerPlot-win64.zip]
param([string]$Zip = "dist\PingerPlot-win64.zip")
$ErrorActionPreference = 'Stop'

$root = Join-Path ([IO.Path]::GetTempPath()) "pingerplot-portable-$PID"
Expand-Archive $Zip -DestinationPath $root -Force
$dir = Join-Path $root 'PingerPlot'
foreach ($f in 'PingerPlot.exe', 'pingerplot-headless.exe', 'portable.txt', 'LICENSE', 'THIRD-PARTY-NOTICES.md') {
    if (-not (Test-Path (Join-Path $dir $f))) { throw "the zip has no PingerPlot\$f" }
}

$out = & (Join-Path $dir 'pingerplot-headless.exe') --version | Out-String
$out
if ($LASTEXITCODE -ne 0) { throw "the headless runner exited with $LASTEXITCODE" }

# A GUI-subsystem exe: Start-Process -Wait -PassThru, never $LASTEXITCODE, which
# PowerShell doesn't set for one and which would read whatever ran before.
$t = Start-Process (Join-Path $dir 'PingerPlot.exe') -ArgumentList '--self-test' -WorkingDirectory $dir -Wait -PassThru
$report = Join-Path $dir 'self-test.txt'
if (-not (Test-Path $report)) { throw "the self-test wrote no report beside the exe: the copy isn't portable" }
Get-Content $report
if ($t.ExitCode -ne 0) { throw "the portable copy's self-test failed (exit $($t.ExitCode))" }
if ((Get-Content $report -Raw) -notmatch '\(portable\)') { throw "the zip's copy doesn't think it's portable" }

$cfg = Join-Path $root 'smoke.json'
$json = '{ "status_interval": 0, "defaults": { "interval": 0.5, "timeout_ms": 1000 }, "targets": [ { "target": "127.0.0.1", "final_hop_only": true } ] }'
[IO.File]::WriteAllText($cfg, $json, (New-Object Text.UTF8Encoding $false))
& (Join-Path $dir 'pingerplot-headless.exe') $cfg --report 2
if ($LASTEXITCODE -ne 0) { throw "the headless runner couldn't probe loopback (exit $LASTEXITCODE)" }

Remove-Item -Recurse -Force $root
"portable ok"
