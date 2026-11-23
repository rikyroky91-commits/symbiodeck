# Jarvis come amministratore: registra l'attività pianificata "Pannello PC - Jarvis" che, all'accesso a Windows,
# avvia Jarvis con i privilegi più alti (nessuna richiesta UAC ogni volta). Così Claude Code, quando glielo chiedi
# a voce, può anche modificare Program Files (es. la configurazione di Apollo), servizi e impostazioni di sistema.
# Le azioni irreversibili chiedono comunque conferma a voce (impostazione "Conferme" nel pannello di Jarvis).
#
# Va eseguito UNA volta: tasto destro > "Esegui con PowerShell" (chiede da solo il permesso di amministratore).
# Per tornare indietro:  .\jarvis-amministratore.ps1 -Togli   (sempre da amministratore)
param([switch]$Togli)
$ErrorActionPreference = 'Stop'
$nome = 'Pannello PC - Jarvis'
$qui = Split-Path -Parent $MyInvocation.MyCommand.Path

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Serve PowerShell come amministratore: lo riapro chiedendo il permesso..." -ForegroundColor Yellow
    $argomenti = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    if ($Togli) { $argomenti += ' -Togli' }
    Start-Process powershell -Verb RunAs -ArgumentList $argomenti
    exit
}

# il Jarvis attuale (senza privilegi, avviato dal pannello) si ferma: ripartirà dall'attività
function Ferma-Jarvis {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match '-m jarvis' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Togli) {
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue
    Ferma-Jarvis
    Write-Host "Attività '$nome' tolta: Jarvis torna a partire dal pannello, senza privilegi." -ForegroundColor Green
    Start-Sleep 3
    exit
}

$utente = "$env:USERDOMAIN\$env:USERNAME"
$azione = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$(Join-Path $qui 'avvia-jarvis.vbs')`"" -WorkingDirectory $qui
$quando = New-ScheduledTaskTrigger -AtLogOn -User $utente
$chi = New-ScheduledTaskPrincipal -UserId $utente -LogonType Interactive -RunLevel Highest
$regole = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $nome -Action $azione -Trigger $quando -Principal $chi -Settings $regole -Force | Out-Null
Ferma-Jarvis
Start-Sleep 2
Start-ScheduledTask -TaskName $nome
Write-Host "Fatto: Jarvis ora parte come amministratore (attività '$nome')." -ForegroundColor Green
Write-Host "Il pannello se ne accorge da solo e non avvia più la copia senza privilegi."
Start-Sleep 4
