Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -File """ & WshShell.CurrentDirectory & "\run_quantime.ps1""", 0, False
