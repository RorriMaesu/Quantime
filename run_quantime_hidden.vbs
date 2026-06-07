Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WshShell.CurrentDirectory & "\backend\.venv\Scripts\pythonw.exe"" """ & WshShell.CurrentDirectory & "\backend\tray_icon.py""", 0, False
