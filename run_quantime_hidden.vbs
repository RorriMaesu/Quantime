Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & scriptDir & "\backend\.venv\Scripts\pythonw.exe"" """ & scriptDir & "\backend\tray_icon.py""", 0, False
