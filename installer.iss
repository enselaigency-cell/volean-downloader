[Setup]
AppName=VOLEAN Downloader
AppVersion=1.2
AppPublisher=VOLEAN
AppPublisherURL=https://volean.app
DefaultDirName={localappdata}\VOLEANDownloader
DefaultGroupName=VOLEAN Downloader
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=VOLEAN_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=VOLEAN Downloader
UninstallDisplayIcon={app}\VOLEAN Downloader.exe
WizardStyle=modern
SetupIconFile=static\AppIcon.ico
; Allow reinstall / upgrade silently
CloseApplications=force

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\VOLEAN Downloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\VOLEAN Downloader"; Filename: "{app}\VOLEAN Downloader.exe"; WorkingDir: "{app}"
Name: "{group}\VOLEAN Downloader";       Filename: "{app}\VOLEAN Downloader.exe"; WorkingDir: "{app}"
Name: "{group}\Удалить VOLEAN Downloader"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\VOLEAN Downloader.exe"; Description: "Запустить VOLEAN Downloader"; Flags: nowait postinstall skipifsilent
