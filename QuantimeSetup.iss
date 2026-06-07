; QuantimeSetup.iss
; Inno Setup configuration script to compile the Quantime Windows Installer.

[Setup]
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
