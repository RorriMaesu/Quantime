; QuantimeSetup.iss
; Inno Setup configuration script to compile the Quantime Windows Installer.

[Setup]
AppId={{D37E618A-706E-45E4-A159-4E6DF9B53A04}}
AppName=Quantime
AppVersion=1.2.0
AppPublisher=RorriMaesu
DefaultDirName={userpf}\Quantime
DefaultGroupName=Quantime
OutputDir=dist
OutputBaseFilename=QuantimeSetup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin

[Files]
; Copy all project files except ignored ones
Source: "*"; DestDir: "{app}"; Excludes: "backend\.venv,frontend\node_modules,quantime.db,backend\quantime.db,*chroma_db,backend\.env,dist,.git"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Quantime"; Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""
Name: "{commondesktop}\Quantime"; Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""

[Run]
; Launch Quantime post-install
Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""; Flags: postinstall nowait skipifsilent; Description: "Launch Quantime"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Configure progress page settings with a marquee progress bar
    WizardForm.StatusLabel.Caption := 'Configuring local environment and compiling assets (this may take a few minutes)...';
    WizardForm.ProgressGauge.Style := npbstMarquee;
    
    // Launch PowerShell post-install synchronously to prevent thread starvation and CallSpawnServer timeouts
    if not Exec('powershell.exe', '-ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\post_install_wizard.ps1') + '" -Silent', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      MsgBox('Failed to run environment post-installation configuration.', mbError, MB_OK);
      Exit;
    end;
    
    if ResultCode <> 0 then
    begin
      MsgBox('Environment configuration completed with error code: ' + IntToStr(ResultCode), mbInformation, MB_OK);
    end;
  end;
end;

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstString: String;
begin
  sUnInstPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{D37E618A-706E-45E4-A159-4E6DF9B53A04}}_is1';
  sUnInstString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstString);
  Result := sUnInstString;
end;

function InitializeSetup(): Boolean;
var
  sUnInstString: String;
  sParameters: String;
  iResultCode: Integer;
begin
  Result := True;
  sUnInstString := GetUninstallString();
  if sUnInstString <> '' then
  begin
    sUnInstString := RemoveQuotes(sUnInstString);
    sParameters := '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES';
    Exec(sUnInstString, sParameters, '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
  end;
end;
