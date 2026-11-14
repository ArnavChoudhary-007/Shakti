#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef UvExe
  #error UvExe is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{E0CE54FD-4BAC-4FC7-8F65-7F9B01EAC928}
AppName=Shakti
AppVersion={#AppVersion}
VersionInfoVersion={#FileVersion}
AppPublisher=Shakti
AppPublisherURL=https://github.com/ArnavChoudhary-007/Shakti
AppSupportURL=https://github.com/ArnavChoudhary-007/Shakti/issues
DefaultDirName={localappdata}\Shakti
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
WizardStyle=modern
SetupIconFile={#PayloadDir}\rag_pipeline\data\shakti.ico
UninstallDisplayIcon={app}\app\rag_pipeline\data\shakti.ico
OutputDir={#OutputDir}
OutputBaseFilename=Shakti-Setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
InfoBeforeFile=welcome.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce
Name: "ollama"; Description: "Install Ollama if missing (recommended; additional download)"; Flags: checkedonce

[Dirs]
Name: "{app}\data"; Flags: uninsneveruninstall

[Files]
Source: "{#UvExe}"; Flags: dontcopy
Source: "bootstrap.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "ShaktiInstaller.psm1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "{#PayloadDir}\rag_pipeline\requirements.txt"; Flags: dontcopy
Source: "{#PayloadDir}\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\rag_pipeline\config.example.yaml"; DestDir: "{app}\data"; DestName: "config.yaml"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{userprograms}\Shakti"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m rag_pipeline --data-dir ""{app}\data"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\rag_pipeline\data\shakti.ico"; Comment: "Shakti - private document AI"
Name: "{userdesktop}\Shakti"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m rag_pipeline --data-dir ""{app}\data"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\rag_pipeline\data\shakti.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m rag_pipeline --data-dir ""{app}\data"""; WorkingDir: "{app}\app"; Description: "Open Shakti"; Flags: nowait postinstall skipifsilent

[InstallDelete]
Type: filesandordirs; Name: "{app}\app"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\installer"

[Code]
function ValidInstallRoot: Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))),
    RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{localappdata}\Shakti')))) = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Parameters: String;
  ExitCode: Integer;
  Failure: AnsiString;
begin
  Result := '';
  if not ValidInstallRoot then begin
    Result := 'Shakti must be installed in the current user''s LocalAppData\Shakti folder.';
    Exit;
  end;
  ExtractTemporaryFile('uv.exe');
  ExtractTemporaryFile('bootstrap.ps1');
  ExtractTemporaryFile('ShaktiInstaller.psm1');
  ExtractTemporaryFile('requirements.txt');
  WizardForm.PreparingLabel.Caption := 'Setting up Python and AI libraries. The first install needs internet access and may take several minutes. Progress is recorded in ' + ExpandConstant('{app}\installer\setup.log');
  Parameters := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\bootstrap.ps1') +
    '" -Action Prepare -InstallRoot "' + ExpandConstant('{app}') + '" -UvPath "' + ExpandConstant('{tmp}\uv.exe') +
    '" -RequirementsPath "' + ExpandConstant('{tmp}\requirements.txt') + '"';
  if WizardIsTaskSelected('ollama') then Parameters := Parameters + ' -InstallOllama';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Parameters,
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := 'Could not start prerequisite setup. Please run this installer again.'
  else if ExitCode = 3010 then begin
    NeedsRestart := True;
    Result := 'Ollama requires a Windows restart. Restart, then run Shakti Setup again.';
  end
  else if ExitCode <> 0 then begin
    Result := 'Prerequisite setup failed.';
    if LoadStringFromFile(ExpandConstant('{app}\installer\setup-error.txt'), Failure) then
      Result := UTF8Decode(Failure);
    Result := Result + #13#10 + 'Details: ' + ExpandConstant('{app}\installer\setup.log');
  end;
end;

function InitializeUninstall: Boolean;
var
  Parameters: String;
  ExitCode: Integer;
begin
  Result := False;
  if not ValidInstallRoot then Exit;
  Parameters := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\bootstrap.ps1') +
    '" -Action Stop -InstallRoot "' + ExpandConstant('{app}') + '"';
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Parameters,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := ExitCode = 0;
  if not Result then
    MsgBox('Shakti could not be stopped safely. Close it and retry uninstalling. Your documents have been kept. See installer\setup.log in the Shakti installation folder.', mbError, MB_OK);
end;
