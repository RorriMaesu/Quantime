; QuantimeSetup.iss
; Inno Setup configuration script to compile the Quantime Windows Installer.

[Setup]
AppId={{D37E618A-706E-45E4-A159-4E6DF9B53A04}}
AppName=Quantime
AppVersion=1.2
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
Source: "*"; DestDir: "{app}"; Excludes: "backend\.venv\*;frontend\node_modules\*;quantime.db;backend\quantime.db;*chroma_db\*;backend\credentials.json;backend\.env;dist\*"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"
Name: "{commondesktop}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"

[Run]
; Run the automated post-install script silently to setup Python, Node, Ollama, and LLMs
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\post_install_wizard.ps1"" -Silent"; StatusMsg: "Configuring local environment and launching background services..."; Flags: runhidden
Filename: "wscript.exe"; Parameters: """{app}\run_quantime_hidden.vbs"""; Flags: postinstall nowait skipifsilent; Description: "Launch Quantime"

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
  sUnInstString := GetUninstallString();
  if sUnInstString <> '' then
  begin
    sUnInstString := RemoveQuotes(sUnInstString);
    sParameters := '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES';
    Exec(sUnInstString, sParameters, '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
  end;
end;
