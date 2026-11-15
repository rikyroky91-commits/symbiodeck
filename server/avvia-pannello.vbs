' Avvia il server del Pannello PC senza finestre.
' Usa python.exe (consentito da Windows Firewall sulla rete privata) con la console nascosta.
Set shell = CreateObject("WScript.Shell")
cartella = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = cartella
shell.Run "python.exe """ & cartella & "\pannello.py""", 0, False
