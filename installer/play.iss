; PLAY installer (Inno Setup 6). Built by build.ps1, which passes /DAppVersion=<VERSION from play/server.py>.
; Packs the PyInstaller one-folder build in build\dist\PLAY and lets the TVs reach it through Windows Firewall
; (private networks only). PLAY itself turns "Start with Windows" on at its first start, for the Windows user who
; runs it (Settings switches it off), so an installer run with another account's admin password can't set it up for
; the wrong account. Uninstall removes the rule and this account's start entry; your media is never touched.

#ifndef AppVersion
  #error Build with build.ps1 (it passes /DAppVersion)
#endif

[Setup]
AppId={{3C8E2F41-7B5A-4D19-8E6C-2A9F1D4B7C60}
AppName=PLAY
AppVersion={#AppVersion}
AppVerName=PLAY {#AppVersion}
AppPublisher=AARROWdev
AppPublisherURL=https://github.com/AARROWdev/play
DefaultDirName={autopf}\PLAY
DefaultGroupName=PLAY
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no
SetupIconFile=..\assets\play.ico
UninstallDisplayIcon={app}\PLAY.exe
UninstallDisplayName=PLAY
OutputDir=..\dist
OutputBaseFilename=PLAYSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\PLAY\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PLAY"; Filename: "{app}\PLAY.exe"
Name: "{autodesktop}\PLAY"; Filename: "{app}\PLAY.exe"; Tasks: desktopicon

[Run]
; Inbound TCP for the movie server and UDP for "find my PC", private networks only. Old rules are replaced.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PLAY"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PLAY"" dir=in action=allow program=""{app}\PLAY.exe"" enable=yes profile=private"; Flags: runhidden
; runasoriginaluser: the first start (which sets up "Start with Windows") belongs to the signed-in user
Filename: "{app}\PLAY.exe"; Description: "{cm:LaunchProgram,PLAY}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PLAY"""; Flags: runhidden; RunOnceId: "PLAYFirewall"

[Code]
// Remove this account's "Start with Windows" entry. Another account's entry is in that account's registry, which
// an uninstaller can't reach; it then points at nothing and Windows skips it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PLAY');
end;

// PLAY holds "PLAYAppMutex" (this session) and "Global\PLAYAppMutex" (any account) while it runs. Setup closes it
// (CloseApplications); the uninstaller asks first instead of leaving a half-removed folder.
function InitializeUninstall(): Boolean;
var Msg: String;
begin
  Result := True;
  while Result and CheckForMutexes('PLAYAppMutex,Global\PLAYAppMutex') do begin
    if CheckForMutexes('PLAYAppMutex') then
      Msg := 'PLAY is running. Quit it from its tray icon, then click OK to uninstall.'
    else
      Msg := 'PLAY is running for another Windows user on this PC (or as a scheduled task). Quit it there, or ' +
             'restart the PC, then click OK to uninstall.';
    if UninstallSilent or (MsgBox(Msg, mbError, MB_OKCANCEL) <> IDOK) then
      Result := False;
  end;
end;
