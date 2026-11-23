' Avvia Jarvis (server/jarvis) senza finestre e aspetta che finisca: così l'attività pianificata
' "Pannello PC - Jarvis" resta "in esecuzione" e, se Jarvis cade, Windows lo riavvia.
' Usa python.exe (consentito da Windows Firewall sulla rete privata) con la console nascosta.
Set shell = CreateObject("WScript.Shell")
cartella = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = cartella
codice = shell.Run("python.exe -m jarvis", 0, True)
WScript.Quit codice
