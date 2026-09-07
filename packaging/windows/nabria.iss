#ifndef AppVersion
  #error AppVersion must come from src/nabria/__init__.py
#endif
[Setup]
AppId=com.sbarah.Nabria
AppName=Nabria
AppVersion={#AppVersion}
AppPublisher=Sbarah
AppPublisherURL=https://sbarah.com
AppSupportURL=https://github.com/M7MMAD-OMAR/nabria-app/issues
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

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ar"; MessagesFile: "Arabic.isl"

#include "..\..\dist\windows-messages.iss"

[Files]
Source: "..\..\dist\Nabria\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: startup; Description: "{cm:startup}"; Flags: unchecked

Name: desktopicon; Description: "{cm:desktop}"; Flags: unchecked

[Icons]
Name: "{userdesktop}\Nabria"; Filename: "{app}\Nabria.exe"; Tasks: desktopicon; AppUserModelID: "com.sbarah.Nabria"
Name: "{group}\Nabria"; Filename: "{app}\Nabria.exe"; AppUserModelID: "com.sbarah.Nabria"
Name: "{userstartup}\Nabria"; Filename: "{app}\Nabria.exe"; Parameters: "--background"; Tasks: startup; AppUserModelID: "com.sbarah.Nabria"

[Run]
Filename: "{app}\Nabria.exe"; Description: "{cm:open}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Nabria.exe"; Parameters: "quit"; Flags: runhidden skipifdoesntexist
