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
Source: "*"; DestDir: "{app}"; Excludes: "backend\.venv,frontend\node_modules,quantime.db,backend\quantime.db,*chroma_db,backend\.env,dist"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"
Name: "{commondesktop}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"

[Run]
; Launch Quantime post-install
Filename: "wscript.exe"; Parameters: """{app}\run_quantime_hidden.vbs"""; Flags: postinstall nowait skipifsilent; Description: "Launch Quantime"

[Code]
// Helper to parse progress file: "Percentage|Message"
procedure DecodeProgress(Line: String; var Pct: Integer; var Msg: String);
var
  SepPos: Integer;
begin
  SepPos := Pos('|', Line);
  if SepPos > 0 then
  begin
    Pct := StrToIntDef(Copy(Line, 1, SepPos - 1), 0);
    Msg := Copy(Line, SepPos + 1, Length(Line) - SepPos);
  end
  else
  begin
    Pct := 0;
    Msg := Line;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ProgressFile: String;
  Lines: TArrayOfString;
  Pct: Integer;
  Msg: String;
  Finished: Boolean;
  TimeoutCount: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    ProgressFile := ExpandConstant('{%USERPROFILE}\.quantime\install_progress.txt');
    
    // Ensure data folder and starting sentinel exists
    ForceDirectories(ExpandConstant('{%USERPROFILE}\.quantime'));
    SaveStringToFile(ProgressFile, '0|Initializing setup wizard...', False);
    
    // Configure progress page settings
    WizardForm.StatusLabel.Caption := 'Configuring local environment and compiling assets...';
    WizardForm.ProgressGauge.Style := npbstNormal;
    WizardForm.ProgressGauge.Position := 0;
    
    // Launch PowerShell post-install asynchronously
    if not Exec('powershell.exe', '-ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\post_install_wizard.ps1') + '" -Silent', '', SW_HIDE, ewNoWait, ResultCode) then
    begin
      MsgBox('Failed to start environment post-installation configuration.', mbError, MB_OK);
      Exit;
    end;
    
    Finished := False;
    TimeoutCount := 0;
    
    while not Finished do
    begin
      Sleep(200);
      WizardForm.Refresh;
      
      if LoadStringsFromFile(ProgressFile, Lines) then
      begin
        if GetArrayLength(Lines) > 0 then
        begin
          DecodeProgress(Lines[0], Pct, Msg);
          WizardForm.ProgressGauge.Position := Pct;
          WizardForm.StatusLabel.Caption := 'Status: ' + Msg + ' (' + IntToStr(Pct) + '%)';
          
          if Pct >= 100 then
          begin
            Finished := True;
          end;
        end;
      end;
      
      TimeoutCount := TimeoutCount + 1;
      // 8 minutes timeout (8 * 60 * 5 = 2400 loops)
      if TimeoutCount > 2400 then
      begin
        Finished := True;
      end;
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
