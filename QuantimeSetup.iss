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
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
SetupIconFile=frontend\public\logo512.ico
UninstallDisplayIcon={app}\frontend\public\logo512.ico

[Files]
; Copy all project files except ignored ones
Source: "*"; DestDir: "{app}"; Exclude: "backend\.venv\*;frontend\node_modules\*;quantime.db;backend\quantime.db;*chroma_db\*;backend\credentials.json;backend\.env;dist\*"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"
Name: "{commondesktop}\Quantime"; Filename: "{app}\run_quantime_hidden.vbs"; IconFilename: "{app}\frontend\public\logo512.ico"

[Run]
; Run the automated post-install script silently to setup Python, Node, Ollama, and LLMs
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\post_install_wizard.ps1"""; StatusMsg: "Configuring local environment, downloading Ollama, and compiling AI weights (this may take a few minutes)..."; Flags: runhidden
