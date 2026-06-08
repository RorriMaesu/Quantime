; QuantimeSetup.iss
; Inno Setup configuration script to compile the Quantime Windows Installer.

[Setup]
AppId={{D37E618A-706E-45E4-A159-4E6DF9B53A04}}
AppName=Quantime
AppVersion=1.2.7
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
; Run the post-install configuration wizard in a visible PowerShell window automatically after copying files
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\post_install_wizard.ps1"""; StatusMsg: "Configuring environment and pre-caching AI models (this may take a few minutes)..."
; Launch Quantime post-install (after configuration wizard finishes)
Filename: "{app}\backend\.venv\Scripts\pythonw.exe"; Parameters: """{app}\backend\tray_icon.py"""; Flags: postinstall nowait skipifsilent; Description: "Launch Quantime"

[Code]
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
  
  // Terminate any running Quantime processes first to release file locks and prevent reboot requests
  Exec('powershell.exe', '-NoProfile -NonInteractive -Command "Get-Process | Where-Object { $_.Path -and ($_.Path -like ''*Quantime*'') } | Stop-Process -Force"', '', SW_HIDE, ewWaitUntilTerminated, iResultCode);

  sUnInstString := GetUninstallString();
  if sUnInstString <> '' then
  begin
    sUnInstString := RemoveQuotes(sUnInstString);
    sParameters := '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES';
    Exec(sUnInstString, sParameters, '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
  end;
end;
