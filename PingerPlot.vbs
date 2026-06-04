' PingerPlot.vbs - double-click to launch with no console window and no flash.
' Delegates to launch.ps1 (which finds the right windowed Python and starts the app).
Dim fso, root, ps1, sh
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = root & "\launch.ps1"
Set sh = CreateObject("WScript.Shell")
' window style 0 = hidden, wait = False -> no console, no flash
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """", 0, False
