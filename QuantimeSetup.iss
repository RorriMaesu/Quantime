; QuantimeSetup.iss
; Inno Setup configuration script to compile the Quantime Windows Installer.

[Setup]
AppId={{D37E618A-706E-45E4-A159-4E6DF9B53A04}}
AppName=Quantime
AppVersion=1.5.1
AppPublisher=RorriMaesu
DefaultDirName={userpf}\Quantime
DefaultGroupName=Quantime
OutputDir=dist
OutputBaseFilename=QuantimeSetup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Files]
; Copy all project files except ignored ones
Source: "*"; DestDir: "{app}"; Excludes: "backend\.venv,frontend\node_modules,quantime.db,backend\quantime.db,*chroma_db,backend\.env,dist,.git"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Quantime"; Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""
Name: "{commondesktop}\Quantime"; Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""

[Run]
; Launch Quantime post-install (after configuration wizard finishes)
Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""; Flags: postinstall nowait skipifsilent; Description: "Launch Quantime"

[Code]
type
  TWinPoint = record
    X: Longint;
    Y: Longint;
  end;

  TWinMsg = record
    hwnd: HWND;
    message: Cardinal;
    wParam: Longint;
    lParam: Longint;
    time: Cardinal;
    pt: TWinPoint;
  end;

function PeekMessage(var lpMsg: TWinMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: Cardinal): Boolean;
  external 'PeekMessageW@user32.dll stdcall';

function TranslateMessage(const lpMsg: TWinMsg): Boolean;
  external 'TranslateMessage@user32.dll stdcall';

function DispatchMessage(const lpMsg: TWinMsg): Longint;
  external 'DispatchMessageW@user32.dll stdcall';

const
  PM_REMOVE = 1;

procedure AppProcessMessage;
var
  Msg: TWinMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, PM_REMOVE) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ProgressFile: String;
  AnsiContent: AnsiString;
  Content: String;
  PctStr: String;
  MsgStr: String;
  CleanPctStr: String;
  DelimiterPos: Integer;
  IsDone: Boolean;
  LoopsCount: Integer;
  i: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    ProgressFile := ExpandConstant('{%USERPROFILE}\.quantime\install_progress.txt');
    
    // Delete the old progress file first if it exists
    DeleteFile(ProgressFile);

    // Initialize progress page settings
    WizardForm.StatusLabel.Caption := 'Initializing configuration environment...';
    WizardForm.ProgressGauge.Min := 0;
    WizardForm.ProgressGauge.Max := 100;
    WizardForm.ProgressGauge.Position := 0;

    // Launch PowerShell post-install silently in the background (completely hidden)
    if Exec('powershell.exe', '-ExecutionPolicy Bypass -NoProfile -File "' + ExpandConstant('{app}\post_install_wizard.ps1') + '" -Silent', '', SW_HIDE, ewNoWait, ResultCode) then
    begin
      IsDone := False;
      LoopsCount := 0;
      
      // Wait for progress file updates with a 15-minute timeout (4500 * 200ms = 15 minutes)
      while (not IsDone) and (LoopsCount < 4500) do
      begin
        // Process Windows messages to keep the UI responsive and draggable
        AppProcessMessage;
        Sleep(200);
        
        LoopsCount := LoopsCount + 1;

        if LoadStringFromFile(ProgressFile, AnsiContent) then
        begin
          Content := String(AnsiContent);
          DelimiterPos := Pos('|', Content);
          if DelimiterPos > 0 then
          begin
            PctStr := Copy(Content, 1, DelimiterPos - 1);
            MsgStr := Copy(Content, DelimiterPos + 1, Length(Content) - DelimiterPos);
            
            MsgStr := Trim(MsgStr);
            PctStr := Trim(PctStr);

            CleanPctStr := '';
            for i := 1 to Length(PctStr) do
            begin
              if (PctStr[i] >= '0') and (PctStr[i] <= '9') then
                CleanPctStr := CleanPctStr + PctStr[i];
            end;
            PctStr := CleanPctStr;

            // Update status text and progress bar position in native installer UI
            WizardForm.StatusLabel.Caption := MsgStr;
            WizardForm.ProgressGauge.Position := StrToIntDef(PctStr, 0);

            if PctStr = '100' then
              IsDone := True;
          end;
        end;
        
        WizardForm.Refresh;
      end;
    end
    else
    begin
      MsgBox('Failed to launch environment configuration script.', mbError, MB_OK);
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
  
  // Terminate only running backend (python/pythonw) and frontend (node) processes associated with Quantime to release file locks and prevent reboot requests
  Exec('powershell.exe', '-NoProfile -NonInteractive -Command "Get-Process | Where-Object { ($_.Name -eq ''pythonw'' -or $_.Name -eq ''python'' -or $_.Name -eq ''node'') -and $_.Path -and ($_.Path -like ''*Quantime*'') } | Stop-Process -Force"', '', SW_HIDE, ewWaitUntilTerminated, iResultCode);

  sUnInstString := GetUninstallString();
  if sUnInstString <> '' then
  begin
    sUnInstString := RemoveQuotes(sUnInstString);
    sParameters := '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES';
    Exec(sUnInstString, sParameters, '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
  end;
end;
