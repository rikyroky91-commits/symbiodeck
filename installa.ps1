# Pannello PC - installazione sul PC Windows (nessun permesso di amministratore richiesto)
# Uso:  tasto destro su installa.ps1 > "Esegui con PowerShell"
#   oppure:  powershell -ExecutionPolicy Bypass -File installa.ps1
$ErrorActionPreference = "Stop"
$qui = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $qui "server"

Write-Host "`n=== Pannello PC: installazione ===`n" -ForegroundColor Cyan

# 1. Python 3.9+
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python non trovato. Installalo da https://www.python.org/downloads/ (spunta 'Add python.exe to PATH')" -ForegroundColor Yellow
    Write-Host "oppure con:  winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
$versione = & python.exe -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$versione -lt [version]"3.9") { Write-Host "Serve Python 3.9 o piu' recente (trovato $versione)" -ForegroundColor Yellow; exit 1 }
Write-Host "[1/4] Python $versione trovato"

# 2. Dipendenze (psutil: CPU, RAM e rete quando MSI Afterburner non c'e')
& python.exe -m pip install --user --quiet --disable-pip-version-check -r (Join-Path $qui "requirements.txt")
Write-Host "[2/4] Dipendenze installate"

# 2b. Jarvis (facoltativo): comandi vocali con Claude Code, circa 3 GB di librerie e modelli
$jarvis = Read-Host "Installare anche Jarvis, i comandi vocali ('Hey Jarvis')? Servono ~3 GB e Claude Code. [s/N]"
if ($jarvis -match '^[sSyY]') {
    & python.exe -m pip install --user --quiet --disable-pip-version-check -r (Join-Path $qui "requirements-jarvis.txt")
    & python.exe -c "import openwakeword.utils as u; u.download_models(model_names=['hey_jarvis'])"
    if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) {
        Write-Host "      Claude Code non trovato: installalo da https://claude.com/claude-code e poi esegui 'claude auth login'" -ForegroundColor Yellow
    }
    Write-Host "      Jarvis installato: parte con il pannello. Sul tablet: scorri dal bordo sinistro verso destra."
}

# 3. Avvio automatico all'accesso a Windows (attivita' pianificata dell'utente corrente)
$azione = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$(Join-Path $server 'avvia-pannello.vbs')`"" -WorkingDirectory $server
$quando = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$regole = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Pannello PC" -Action $azione -Trigger $quando -Settings $regole -Force | Out-Null
Write-Host "[3/4] Avvio automatico configurato (Utilita' di pianificazione > 'Pannello PC')"

# 4. Configurazione, collegamenti alle impostazioni e primo avvio
& python.exe (Join-Path $server "pannello.py") --collegamenti
Start-ScheduledTask -TaskName "Pannello PC"
Start-Sleep -Seconds 3
Write-Host "[4/4] Server avviato`n"

Write-Host "Ora sul tablet Android:" -ForegroundColor Green
Write-Host "  1. installa PannelloPC.apk (dalla pagina Releases del progetto su GitHub)"
Write-Host "  2. aprila: entro 10 minuti trova questo PC da sola, sulla stessa rete Wi-Fi o col cavo USB"
Write-Host "     (se Windows chiede di consentire Python nel firewall, scegli 'Reti private')"
Write-Host "  3. per abbinare un altro tablet piu' tardi: 'Pannello PC - Impostazioni' > 'Abbina un tablet'`n"
$link = Get-ChildItem ([Environment]::GetFolderPath("Desktop")) -Filter "Pannello PC - Impostazioni.url" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($link) { Start-Process $link.FullName }
