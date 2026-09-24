' InvisibleRunner.vbs - silent runner for scheduled tasks (group U060 pattern).
' BigCompute copy of the group standard (BigLife/BigMoney same file).
' No window ever flashes. Usage: wscript.exe //B //nologo InvisibleRunner.vbs <exe> [args...]
Dim sh, args, i, a
Set sh = CreateObject("WScript.Shell")
args = ""
For i = 0 To WScript.Arguments.Count - 1
    a = WScript.Arguments(i)
    If InStr(a, " ") > 0 Or InStr(a, "&") > 0 Or InStr(a, "^") > 0 Or InStr(a, "=") > 0 Then
        If Left(a, 1) <> """" Then a = Chr(34) & a & Chr(34)
    End If
    args = args & " " & a
Next
If args = "" Then WScript.Quit 1
WScript.Quit sh.Run(Mid(args, 2), 0, True)
