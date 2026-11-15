# Riattiva il rilevamento delle applicazioni di RivaTuner (serve per gli FPS sul pannello).
# Va eseguito come amministratore: RTSS gira elevato. L'OSD resta spento (EnableOSD non si tocca).
# Copia della configurazione precedente: ..\RTSS-Global.prima-del-pannello
$ErrorActionPreference = 'Stop'
$cartella = 'C:\Program Files (x86)\RivaTuner Statistics Server'
$globale = Join-Path $cartella 'Profiles\Global'
$registro = Join-Path $PSScriptRoot 'rtss-rilevamento.log'
try {
    Get-Process RTSS, RTSSHooksLoader, RTSSHooksLoader64 -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    $testo = Get-Content -LiteralPath $globale -Raw
    $valori = @{ 'EnableHooking' = 1; 'HookDirect3D8' = 1; 'HookDirect3D9' = 1; 'HookDXGI' = 1;
                 'HookDirect3D12' = 1; 'HookOpenGL' = 1; 'HookVulkan' = 1 }
    foreach ($k in $valori.Keys) {
        $testo = [regex]::Replace($testo, "(?m)^$k=\d+", "$k=$($valori[$k])")
    }
    Set-Content -LiteralPath $globale -Value $testo -NoNewline -Encoding ASCII
    Start-Process -FilePath (Join-Path $cartella 'RTSS.exe') -WorkingDirectory $cartella
    "$(Get-Date -Format s) ok" | Out-File -FilePath $registro -Encoding UTF8
} catch {
    "$(Get-Date -Format s) errore: $($_.Exception.Message)" | Out-File -FilePath $registro -Encoding UTF8
}
