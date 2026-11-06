; Nam Tour Agent - Windows installer (Inno Setup 6). Built by build.ps1; do not compile by hand.
; Installs per user (no administrator rights) into %LOCALAPPDATA%\Programs\NamTour.
; .env and data\ are created on the first start and are never touched by an update or by uninstalling.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\..\build\NamTour"
#endif

[Setup]
AppId={{6E2B6C51-8C1D-4E59-9C8B-7A1F2D0B9E31}
AppName=Nam Tour Agent
AppVersion={#AppVersion}
AppPublisher=JIHAN JAN JASUR NAM TOUR MCHJ
DefaultDirName={localappdata}\Programs\NamTour
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputBaseFilename=NamTour-Setup
SetupIconFile={#SourceDir}\namtour.ico
UninstallDisplayIcon={app}\namtour.ico
UninstallDisplayName=Nam Tour Agent
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Start the agent when Windows starts / Zapuskat agenta vmeste s Windows"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: ".env,data\*,__pycache__"

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{autoprograms}\Nam Tour Agent"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop open"; WorkingDir: "{app}"; IconFilename: "{app}\namtour.ico"
Name: "{autoprograms}\Nam Tour Agent - stop"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop stop"; WorkingDir: "{app}"; IconFilename: "{app}\namtour.ico"
Name: "{autodesktop}\Nam Tour Agent"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop open"; WorkingDir: "{app}"; IconFilename: "{app}\namtour.ico"; Tasks: desktopicon
Name: "{userstartup}\Nam Tour Agent"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop serve"; WorkingDir: "{app}"; IconFilename: "{app}\namtour.ico"; Tasks: autostart

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop open"; WorkingDir: "{app}"; Description: "Nam Tour Agent"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.desktop stop"; WorkingDir: "{app}"; RunOnceId: "StopAgent"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Code]
// Stop a running agent before files are replaced by an update.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  if FileExists(ExpandConstant('{app}\python\pythonw.exe')) then
  begin
    Exec(ExpandConstant('{app}\python\pythonw.exe'), '-m app.desktop stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(2000);
  end;
  Result := '';
end;
