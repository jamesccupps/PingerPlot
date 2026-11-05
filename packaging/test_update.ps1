# Update a copy of PingerPlot to dist\'s build for real.
#
# The old copy is the newest published release older than this build that has
# an updater (portable: its zip, unzipped; installed: its Setup.exe). Until
# there is one -- 1.4.0 is the first -- the build updates itself: the old copy
# is dist\'s own, and the release claims the next patch version. Either way
# the old copy is started, then its own updater code (its source, from its tag,
# or this tree) checks a release folder holding dist\'s files and a latest.json
# signed with a throwaway key, downloads, verifies and applies, and the old
# copy is ended the way it ends itself. The test checks that the update
# replaced the program files, started this build with --updated, and left the
# settings file alone.
#
# CI runs it (release.yml, ci.yml). On a PC of your own, -Kinds installed
# replaces an installed copy, so quit and back up first, or use -Kinds portable.
#
#   pwsh packaging/test_update.ps1 [-Kinds portable,installed] [-From X.Y.Z|self] [-Python python]
param(
    [string[]]$Kinds = @('portable', 'installed'),
    [string]$From = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Windows PowerShell's progress bar slows downloads
$repo = 'jamesccupps/PingerPlot'
$root = Split-Path $PSScriptRoot
$helper = Join-Path $PSScriptRoot 'update_test_helper.py'
$dist = Join-Path $root 'dist'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{64B8717F-5BDB-4070-9A45-47C311BC5DB5}_is1'
$mutex = 'JamesCupps.PingerPlot'

# On a runner, also as an annotation: job logs need a signed-in session, annotations don't.
function Write-Result([string]$level, [string]$message) {
    $message
    if ($env:GITHUB_ACTIONS) { "::${level} title=Update test::$($message -replace "`r?`n", '%0A')" }
}

$version = (& $Python -c "import sys; sys.path.insert(0, r'$root'); import pingerplot; print(pingerplot.__version__)").Trim()
if (-not $From) { $From = "$(& $Python $helper previous)".Trim() }
if (-not $From) { $From = 'self' }
$self = $From -eq 'self'
if ($self) {
    $p = $version.Split('.')
    $offered = "$($p[0]).$($p[1]).$([int]$p[2] + 1)"
    "no published release older than $version has an updater: updating this build to itself, offered as $offered"
} else {
    $offered = $version
    "updating $From to this build, ${version}: $($Kinds -join ', ')"
}

# Not GetTempPath() on a runner: it can be an 8.3 short path, and processes report long ones.
$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$work = Join-Path $temp "pp-updtest-$PID"
$release = Join-Path $work 'release'
New-Item -ItemType Directory -Force $release | Out-Null
foreach ($f in 'PingerPlot-Setup.exe', 'PingerPlot-win64.zip') {
    if (Test-Path (Join-Path $dist $f)) { Copy-Item (Join-Path $dist $f) $release }
}
$key = "$(& $Python $helper sign $release $offered)".Trim()
if ($LASTEXITCODE -ne 0 -or -not $key) { throw "couldn't sign the test release" }

# GitHub answers a download with a 5xx now and then: a 504 Gateway Time-out on
# the old release's zip failed a build on 2026-11-05, for nothing PingerPlot
# did. No answer, a 5xx or a 429 is tried again, further apart each time; any
# other status (a 404: the file isn't there) fails at once. The message goes
# to the host, not the output: Get-Old returns its path through the output.
function Save-Url([string]$url, [string]$out, [int]$tries = 4) {
    for ($try = 1; ; $try++) {
        try {
            Invoke-WebRequest -UseBasicParsing $url -OutFile $out
            return
        } catch {
            $code = 0
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            $transient = $code -eq 0 -or $code -ge 500 -or $code -eq 429
            if (-not $transient -or $try -ge $tries) { throw }
            Write-Host "  $url failed ($($_.Exception.Message)); trying again in $(10 * $try) s"
            Start-Sleep -Seconds (10 * $try)
        }
    }
}

function Get-Old([string]$name) {
    if ($self) { return (Join-Path $dist $name) }
    $out = Join-Path $work "v$From-$name"
    Save-Url "https://github.com/$repo/releases/download/v$From/$name" $out
    $out
}
if ($self) {
    $oldSource = $root
} else {
    Save-Url "https://github.com/$repo/archive/refs/tags/v$From.zip" "$work\source.zip"
    Expand-Archive "$work\source.zip" "$work\source"
    $oldSource = (Get-ChildItem "$work\source" -Directory | Select-Object -First 1).FullName
}
$oldVersion = if ($self) { $version } else { $From }

function Wait-Until([scriptblock]$done, [int]$seconds, [string]$what) {
    $end = (Get-Date).AddSeconds($seconds)
    while (-not (& $done)) {
        if ((Get-Date) -gt $end) { throw "timed out after $seconds s waiting for $what" }
        Start-Sleep -Milliseconds 500
    }
}
function Test-Log([string]$file, [string]$text) {
    (Test-Path $file) -and (Select-String -Path $file -Pattern $text -SimpleMatch -Quiet)
}
function Test-Running {
    $m = $null
    if ([Threading.Mutex]::TryOpenExisting($mutex, [ref]$m)) { $m.Dispose(); return $true }
    $false
}
function Get-Copies([string]$dir) {
    @(Get-Process PingerPlot -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) })
}
function Stop-Copies([string]$dir) {
    Get-Copies $dir | Stop-Process -Force
    Start-Sleep -Seconds 1
}
function Get-InstalledVersion {
    (Get-ItemProperty $uninstallKey -ErrorAction SilentlyContinue).DisplayVersion
}
function Show-Log([string]$data) {
    $f = Join-Path $data 'update.log'
    if (Test-Path $f) { "--- update.log"; Get-Content $f -Tail 20 }
}
# The old copy's settings, as a user would have them: a target to resume, and
# no update check of its own (it would ask GitHub, not the test release).
function Write-Data([string]$data) {
    New-Item -ItemType Directory -Force $data | Out-Null
    $json = '{"check_updates": false, "resume_on_launch": false, "theme": "system", "targets": ["127.0.0.1"]}'
    $f = Join-Path $data 'settings.json'
    [IO.File]::WriteAllText($f, $json, (New-Object Text.UTF8Encoding $false))
    (Get-FileHash $f).Hash
}
function Start-Old([string]$dir) {
    if (Test-Running) { throw "a copy of PingerPlot is already running; quit it first" }
    $p = Start-Process (Join-Path $dir 'PingerPlot.exe') -WorkingDirectory $dir -PassThru
    Wait-Until { Test-Running } 60 "$oldVersion to start"
    Start-Sleep -Seconds 3                    # the window, past the single-instance check
    if ($p.HasExited) { throw "the old copy exited with $($p.ExitCode)" }
    $p
}
function Assert-Updated([string]$dir, [string]$data, [string]$hash) {
    $log = Join-Path $data 'update.log'
    Wait-Until { (Test-Log $log "update: $version started") } 120 "$version to start after the update"
    if (Test-Log $log 'after a failed update') { throw "the update failed and the old version was restored" }
    $exe = Join-Path $dir 'PingerPlot.exe'
    $fileVersion = (Get-Item $exe).VersionInfo.ProductVersion
    if ($fileVersion -ne $version) { throw "the exe is $fileVersion after the update, not $version" }
    Wait-Until { (Get-Copies $dir).Count -eq 1 } 30 'one copy running'
    if ((Get-FileHash (Join-Path $data 'settings.json')).Hash -ne $hash) { throw "the settings file changed" }
    $old = @(Get-ChildItem $dir -Filter *.old)
    if ($old.Count) { throw "the update left $($old.Name -join ', ')" }
}

foreach ($kind in $Kinds) {
    "--- $kind"
    if ($kind -eq 'portable') {
        Expand-Archive (Get-Old 'PingerPlot-win64.zip') "$work\portable"
        $dir = Join-Path $work 'portable\PingerPlot'
        $data = $dir
        Remove-Item Env:\PINGERPLOT_DATA_DIR -ErrorAction SilentlyContinue
    } elseif ($kind -eq 'installed') {
        $p = Start-Process (Get-Old 'PingerPlot-Setup.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "the $oldVersion installer exited with $($p.ExitCode)" }
        if ((Get-InstalledVersion) -ne $oldVersion) { throw "$oldVersion didn't install" }
        $dir = (Get-ItemProperty $uninstallKey).InstallLocation.TrimEnd('\')
        $data = Join-Path $work 'installed-data'
        $env:PINGERPLOT_DATA_DIR = $data
    } else { throw "unknown kind $kind" }
    $hash = Write-Data $data
    try {
        $old = Start-Old $dir
        $dataArg = if ($kind -eq 'installed') { $data } else { '' }
        & $Python $helper drive $oldSource $release $kind $dir $old.Id $key $dataArg
        if ($LASTEXITCODE -ne 0) { throw "the $oldVersion updater failed" }
        if ($kind -eq 'portable') {
            Wait-Until { Test-Log (Join-Path $data 'update.log') 'update: done' } 120 'the swap'
            if (-not (Test-Path (Join-Path $dir 'portable.txt'))) { throw "the update lost portable.txt" }
        } else {
            Wait-Until { (Get-InstalledVersion) -eq $version } 180 "Settings > Apps to say $version"
            if (Test-Path (Join-Path $dir 'portable.txt')) { throw "the installed copy gained portable.txt" }
        }
        Assert-Updated $dir $data $hash
        Show-Log $data
        Write-Result notice "$kind ok: $oldVersion updated itself to $version"
    } catch {
        Show-Log $data
        Write-Result error "$kind, $oldVersion to ${version}: $($_.Exception.Message)"
        throw
    } finally {
        Stop-Copies $dir
        Remove-Item Env:\PINGERPLOT_DATA_DIR -ErrorAction SilentlyContinue
        if ($kind -eq 'installed' -and (Test-Path $uninstallKey)) {
            $unins = Join-Path $dir 'unins000.exe'
            Start-Process $unins -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
            Wait-Until { -not (Test-Path $unins) } 60 'the uninstaller'
        }
    }
}
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
"update ok: $oldVersion to $version ($($Kinds -join ', '))"
