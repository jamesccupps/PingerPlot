#Requires -Version 5.1
<#
.SYNOPSIS
    PingerPlot launcher / installer for Windows.

.DESCRIPTION
    Launches the app with no console window, can self-elevate for full TCP/UDP
    traceroute, create Desktop/Start-menu shortcuts, and install a logon
    auto-start task that runs the app elevated with no UAC prompt.

.EXAMPLE
    .\launch.ps1                    # launch now (no console window)
    .\launch.ps1 -Elevated          # launch elevated (UAC) so TCP/UDP works
    .\launch.ps1 -CreateShortcuts   # Desktop + Start-menu shortcuts (normal + Admin)
    .\launch.ps1 -InstallAutostart  # auto-start elevated at logon (no UAC nag)
    .\launch.ps1 -UninstallAutostart
    .\launch.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Elevated,
    [switch]$CreateShortcuts,
    [switch]$InstallAutostart,
    [switch]$UninstallAutostart,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$AppName    = 'PingerPlot'
$TaskName   = 'PingerPlot Autostart'
$Root       = $PSScriptRoot
$MainPy     = Join-Path $Root 'main.py'
$ScriptPath = $PSCommandPath

# --- small UI helpers (work even when launched hidden) ---------------------
function Show-Info {
    param([string]$Message, [string]$Title = $AppName)
    try { [void](New-Object -ComObject WScript.Shell).Popup($Message, 0, $Title, 64) }
    catch { Write-Host $Message }
}
function Show-ErrorBox {
    param([string]$Message)
    try { [void](New-Object -ComObject WScript.Shell).Popup($Message, 0, "$AppName - error", 16) }
    catch { Write-Host $Message -ForegroundColor Red }
}
function Show-Warn {
    param([string]$Message)
    try { [void](New-Object -ComObject WScript.Shell).Popup($Message, 0, "$AppName - security note", 48) }
    catch { Write-Host $Message -ForegroundColor Yellow }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Test-AdminOnlyPath {
    param([string]$Path)
    # Best-effort: would a standard (non-elevated) user be unable to modify
    # $Path? Approximate "yes" by whether it lives under a system-protected root
    # (Program Files / Windows). User profiles and most data-drive folders are
    # writable by their owner — a non-admin in normal use — so an auto-elevating
    # logon task that launches code from there is a local privilege-escalation
    # risk. Heuristic by design (we only warn on it, never block), and locale-
    # independent (resolves the protected roots from the environment).
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    try { $full = [IO.Path]::GetFullPath($Path) } catch { return $false }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:SystemRoot) |
        Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') }
    foreach ($r in $roots) {
        if ($full.Equals($r, [StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($r + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

# --- locate a windowed Python interpreter (no console window) --------------
function Find-PythonW {
    $pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue        # py launcher (windowed)
    if ($pyw) { return @{ Exe = $pyw.Source; Pre = @('-3') } }
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($pythonw) { return @{ Exe = $pythonw.Source; Pre = @() } }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $cand = Join-Path ([IO.Path]::GetDirectoryName($python.Source)) 'pythonw.exe'
        if (Test-Path $cand) { return @{ Exe = $cand; Pre = @() } }
        return @{ Exe = $python.Source; Pre = @() }
    }
    foreach ($g in @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\pythonw.exe",
        "$env:ProgramFiles\Python3*\pythonw.exe",
        "C:\Python3*\pythonw.exe")) {
        $hit = Get-ChildItem $g -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return @{ Exe = $hit.FullName; Pre = @() } }
    }
    throw "Python 3 not found. Install from https://python.org and tick 'Add python.exe to PATH'."
}

function Get-ArgLine {
    param($Py)
    $line = ''
    if ($Py.Pre.Count -gt 0) { $line = ($Py.Pre -join ' ') + ' ' }
    return $line + ('"{0}"' -f $MainPy)
}

# --- actions ---------------------------------------------------------------
function Start-App {
    param([switch]$AsAdmin)
    $py = Find-PythonW
    $argLine = Get-ArgLine $py
    if ($AsAdmin -and -not (Test-Admin)) {
        return (Start-Process -FilePath $py.Exe -ArgumentList $argLine -Verb RunAs -PassThru)
    }
    return (Start-Process -FilePath $py.Exe -ArgumentList $argLine -WorkingDirectory $Root -PassThru)
}

function New-Lnk {
    param([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkDir, [bool]$RunAsAdmin)
    $sh  = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($Path)
    $lnk.TargetPath       = $Target
    $lnk.Arguments        = $Arguments
    $lnk.WorkingDirectory = $WorkDir
    $lnk.IconLocation     = "$Target,0"
    $lnk.Description       = 'PingerPlot network path monitor'
    $lnk.Save()
    if ($RunAsAdmin) {
        $bytes = [IO.File]::ReadAllBytes($Path)
        $bytes[0x15] = $bytes[0x15] -bor 0x20   # set the "Run as administrator" flag
        [IO.File]::WriteAllBytes($Path, $bytes)
    }
    return $Path
}

function New-AppShortcuts {
    $py = Find-PythonW
    $argLine = Get-ArgLine $py
    $desktop  = [Environment]::GetFolderPath('Desktop')
    $programs = Join-Path ([Environment]::GetFolderPath('Programs')) $AppName
    if (-not (Test-Path $programs)) { [void](New-Item -ItemType Directory -Path $programs -Force) }
    $made = @()
    foreach ($dir in @($desktop, $programs)) {
        $made += New-Lnk (Join-Path $dir "$AppName.lnk")         $py.Exe $argLine $Root $false
        $made += New-Lnk (Join-Path $dir "$AppName (Admin).lnk") $py.Exe $argLine $Root $true
    }
    Show-Info ("Shortcuts created (the (Admin) one self-elevates for TCP/UDP):`r`n`r`n" + ($made -join "`r`n"))
}

function Install-Autostart {
    if (-not (Test-Admin)) {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $ScriptPath), '-InstallAutostart')
        return
    }
    $py = Find-PythonW
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action    = New-ScheduledTaskAction -Execute $py.Exe -Argument (Get-ArgLine $py) -WorkingDirectory $Root
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    [void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force)
    if ((Test-AdminOnlyPath $Root) -and (Test-AdminOnlyPath $py.Exe)) {
        Show-Info "Auto-start enabled. $AppName will launch elevated at logon with no UAC prompt.`r`n`r`nRemove it any time with:  launch.ps1 -UninstallAutostart"
    } else {
        # Warn-but-allow: the task is registered, but it auto-elevates code from
        # a folder a non-elevated process could tamper with — call that out.
        Show-Warn (@(
            "Auto-start enabled - but a security note:",
            "",
            "This task runs $AppName ELEVATED at every logon with no UAC prompt. It launches:",
            "    $($py.Exe)",
            "    $MainPy",
            "",
            "One or both sits in a folder a standard (non-elevated) user can modify. Anything",
            "running as you, without elevation, could replace that code and have it run as",
            "Administrator at your next logon.",
            "",
            "For best security, install $AppName under an admin-only path such as",
            "    $env:ProgramFiles\$AppName",
            "and re-run -InstallAutostart from there. Remove auto-start any time with:",
            "    launch.ps1 -UninstallAutostart"
        ) -join "`r`n")
    }
}

function Uninstall-Autostart {
    if (-not (Test-Admin)) {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $ScriptPath), '-UninstallAutostart')
        return
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Show-Info "Auto-start removed."
    } else {
        Show-Info "Auto-start was not installed."
    }
}

function Show-Status {
    try { $py = (Find-PythonW).Exe } catch { $py = '(not found)' }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { $taskState = 'installed' } else { $taskState = 'not installed' }
    $lines = @(
        "Folder:            $Root",
        "Python (windowed): $py",
        "Elevated session:  $(Test-Admin)",
        "Auto-start task:   $taskState"
    )
    Show-Info ($lines -join "`r`n") "$AppName status"
}

# --- dispatch (skipped when the script is dot-sourced, e.g. for testing) ---
if ($MyInvocation.InvocationName -ne '.') {
    try {
        if ($Status)             { Show-Status }
        elseif ($CreateShortcuts){ New-AppShortcuts }
        elseif ($InstallAutostart)   { Install-Autostart }
        elseif ($UninstallAutostart) { Uninstall-Autostart }
        else                     { [void](Start-App -AsAdmin:$Elevated) }
    } catch {
        Show-ErrorBox $_.Exception.Message
        exit 1
    }
}
