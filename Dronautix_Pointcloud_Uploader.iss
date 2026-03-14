#include "installer_version.iss"
#define SourceExe "dist\\Dronautix_Pointcloud_Uploader.exe"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Dronautix\Pointcloud Uploader
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=Dronautix_Pointcloud_Uploader_Setup_{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Zusätzliche Aufgaben:"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} starten"; Flags: nowait postinstall skipifsilent

[Code]
function GetUninstallString(): string;
var
  uninstallKey: string;
  uninstallString: string;
begin
  uninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{#AppId}_is1';
  uninstallString := '';

  if not RegQueryStringValue(HKLM64, uninstallKey, 'UninstallString', uninstallString) then
    if not RegQueryStringValue(HKLM, uninstallKey, 'UninstallString', uninstallString) then
      RegQueryStringValue(HKCU, uninstallKey, 'UninstallString', uninstallString);

  Result := uninstallString;
end;

function UnInstallOldVersion(): Integer;
var
  uninstallString: string;
  resultCode: Integer;
begin
  Result := 0;
  uninstallString := RemoveQuotes(GetUninstallString());

  if uninstallString <> '' then
  begin
    if Exec(
      uninstallString,
      '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      resultCode
    ) then
      Result := resultCode
    else
      Result := -1;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  uninstallResult: Integer;
begin
  Result := '';
  uninstallResult := UnInstallOldVersion();

  if (uninstallResult <> 0) and (uninstallResult <> 1) and (uninstallResult <> 3010) then
    Result := 'Vorherige Version konnte nicht deinstalliert werden. Fehlercode: ' + IntToStr(uninstallResult);
end;
