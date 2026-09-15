Set fso = CreateObject("Scripting.FileSystemObject")

Set sh = CreateObject("WScript.Shell")



base = fso.GetParentFolderName(WScript.ScriptFullName)

logFile = base & "\autostart.log"

launcher = base & "\.venv\Scripts\pythonw.exe"

spawn = base & "\spawn_tray.py"



Sub WriteLog(msg)

    On Error Resume Next

    Set log = fso.OpenTextFile(logFile, 8, True, -1)

    log.WriteLine Now & "  " & msg

    log.Close

    On Error GoTo 0

End Sub



WriteLog "autostart-launch.vbs started"



If Not fso.FileExists(launcher) Then

    WriteLog "ERROR: launcher not found: " & launcher

    WScript.Quit 1

End If



If IsDictationRunning() Then

    WriteLog "Dictation already running, skip launch"

    WScript.Quit 0

End If



startupDelaySec = 20

WriteLog "Waiting " & startupDelaySec & " seconds after logon..."

WScript.Sleep startupDelaySec * 1000



If IsDictationRunning() Then

    WriteLog "Dictation already running after delay, skip launch"

    WScript.Quit 0

End If



WriteLog "Launching via spawn_tray.py"

sh.CurrentDirectory = base

sh.Run """" & launcher & """ """ & spawn & """", 0, False



maxWaitSec = 90

pollSec = 3

elapsed = 0



Do While elapsed < maxWaitSec

    WScript.Sleep pollSec * 1000

    elapsed = elapsed + pollSec

    If IsDictationRunning() Then

        WriteLog "SUCCESS: dictation process is running"

        WScript.Quit 0

    End If

Loop



WriteLog "FAILED: dictation process did not appear within " & maxWaitSec & " seconds"

WScript.Quit 1



Function IsDictationRunning()

    On Error Resume Next

    Set wmi = GetObject("winmgmts:\\.\root\cimv2")

    Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name='pythonw.exe' OR Name='python.exe'")

    For Each proc In procs

        If InStr(LCase(proc.CommandLine), "dictation.main") > 0 Then

            IsDictationRunning = True

            Exit Function

        End If

    Next

    IsDictationRunning = False

End Function


