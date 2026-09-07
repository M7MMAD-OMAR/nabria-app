#ifndef AppVersion
  #error AppVersion must come from src/nabria/__init__.py
#endif
[Setup]
AppId=com.sbarah.Nabria
AppName=Nabria
AppVersion={#AppVersion}
AppPublisher=Nabria contributors
AppPublisherURL=https://github.com/M7MMAD-OMAR/nabria-app
DefaultDirName={localappdata}\Programs\Nabria
DefaultGroupName=Nabria
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.18362
OutputDir=..\..\dist
OutputBaseFilename=Nabria-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Nabria.exe
CloseApplications=yes

[Files]
Source: "..\..\dist\Nabria\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: startup; Description: "Start Nabria when I sign in"; Flags: unchecked

[Icons]
Name: "{group}\Nabria"; Filename: "{app}\Nabria.exe"; AppUserModelID: "com.sbarah.Nabria"
Name: "{userstartup}\Nabria"; Filename: "{app}\Nabria.exe"; Parameters: "daemon"; Tasks: startup; AppUserModelID: "com.sbarah.Nabria"

[Run]
Filename: "{app}\Nabria.exe"; Description: "Open Nabria"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Nabria.exe"; Parameters: "quit"; Flags: runhidden skipifdoesntexist
