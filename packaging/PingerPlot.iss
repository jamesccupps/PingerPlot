; Inno Setup script for PingerPlot-Setup.exe (Inno Setup 6.x).
;
;   iscc /DAppVersion=1.4.0 packaging\PingerPlot.iss
;
; packaging/build_windows.py runs this after PyInstaller. It installs for the
; current Windows user only, with no admin prompt, into
; %LOCALAPPDATA%\Programs\PingerPlot, with a Start menu shortcut and an entry
; in Settings > Apps. Settings live in %APPDATA%\PingerPlot and are never
; touched by installing, upgrading or uninstalling.
;
; Running PingerPlot is unelevated: ICMP and TCP to the destination need no
; administrator rights. Full TCP/UDP traceroute does; right-click the shortcut
; and choose Run as administrator for that.
;
; AppId must never change: it is how Windows (and this installer) recognise an
; existing installation to upgrade in place.

#define AppName "PingerPlot"
#define AppExe "PingerPlot.exe"
#define AppMutex "JamesCupps.PingerPlot"
#define AppUserModelID "JamesCupps.PingerPlot"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{64B8717F-5BDB-4070-9A45-47C311BC5DB5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=James Cupps
AppPublisherURL=https://jamesccupps.github.io/PingerPlot/
AppSupportURL=https://github.com/jamesccupps/PingerPlot/issues
AppUpdatesURL=https://github.com/jamesccupps/PingerPlot/releases
AppCopyright=Copyright (c) James Cupps. MIT license.
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PingerPlot-Setup
SetupIconFile=..\build\pingerplot.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The app's own wait below handles a running copy; the Restart Manager would
; only add a prompt.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the program files wholesale, so nothing from an older
; version lingers in _internal.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PingerPlot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; Start with Windows is the app's own setting; uninstalling takes it away.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ A silent run (an update, a script) waits a little for a running copy to
  quit. Run by hand while the app is open, Setup asks. }
function AppIsGone: Boolean;
var
  i: Integer;
begin
  i := 0;
  while CheckForMutexes('{#AppMutex}') and (i < 100) do
  begin
    Sleep(150);
    i := i + 1;
  end;
  Result := not CheckForMutexes('{#AppMutex}');
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if WizardSilent then
  begin
    Result := AppIsGone;
    exit;
  end;
  while CheckForMutexes('{#AppMutex}') do
  begin
    if MsgBox('{#AppName} is running. Quit it first: right-click its icon in the ' +
              'notification area and choose Quit. Then click Retry.',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      exit;
    end;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  while CheckForMutexes('{#AppMutex}') do
  begin
    if UninstallSilent then
    begin
      Result := AppIsGone;
      exit;
    end;
    if MsgBox('{#AppName} is running. Quit it first: right-click its icon in the ' +
              'notification area and choose Quit. Then click Retry.',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      exit;
    end;
  end;
end;
