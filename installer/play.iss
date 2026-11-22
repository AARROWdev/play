; PLAY installer (Inno Setup 6). Built by build.ps1, which passes /DAppVersion=<VERSION from play/server.py>.
; Packs the PyInstaller one-folder build in build\dist\PLAY, lets the TVs reach it through Windows Firewall
; (private networks only), and can start PLAY with Windows. Uninstall removes all of that; your media is never touched.

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
Name: "autostart"; Description: "Start PLAY with Windows (so the TVs can always reach it)"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\PLAY\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PLAY"; Filename: "{app}\PLAY.exe"
Name: "{autodesktop}\PLAY"; Filename: "{app}\PLAY.exe"; Tasks: desktopicon

[Registry]
; HKCU of the account that runs setup. If another account's admin password was used, PLAY's Settings checkbox
; turns it on for the signed-in user instead (the README says so).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PLAY"; ValueData: """{app}\PLAY.exe"" --tray"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; Inbound TCP for the movie server and UDP for "find my PC", private networks only. Old rules are replaced.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PLAY"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PLAY"" dir=in action=allow program=""{app}\PLAY.exe"" enable=yes profile=private"; Flags: runhidden
Filename: "{app}\PLAY.exe"; Description: "{cm:LaunchProgram,PLAY}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PLAY"""; Flags: runhidden; RunOnceId: "PLAYFirewall"

[Code]
// The Settings checkbox can add the Run entry after install; remove it on uninstall either way.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PLAY');
end;

// PLAY holds "PLAYAppMutex" while it runs. Setup closes it (CloseApplications); the uninstaller asks first
// instead of leaving a half-removed folder.
function InitializeUninstall(): Boolean;
begin
  Result := True;
  while Result and CheckForMutexes('PLAYAppMutex,Global\PLAYAppMutex') do
    if UninstallSilent or (MsgBox('PLAY is running. Quit it from its tray icon, then click OK to uninstall.',
                                  mbError, MB_OKCANCEL) <> IDOK) then
      Result := False;
end;
