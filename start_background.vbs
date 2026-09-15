Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = base & "\.venv\Scripts\pythonw.exe"
spawn = base & "\spawn_tray.py"

If Not fso.FileExists(launcher) Then
    WScript.Quit 1
End If

sh.CurrentDirectory = base
sh.Run """" & launcher & """ """ & spawn & """", 0, False
