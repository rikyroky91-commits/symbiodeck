# Aggiunge ad Apollo (Vibepollo) l'app "Secondo monitor tablet", usata solo dal pannello del tablet.
# Ha un monitor virtuale suo (virtual-screen) in layout 'extended': si AGGIUNGE a quelli del PC
# (la TV resta accesa e con la sua risoluzione) e viene tolto quando il tablet torna al pannello. Le impostazioni globali non cambiano, quindi
# gli altri dispositivi continuano a usare Apollo come prima.
# Va eseguito come amministratore (apps.json è in Program Files). Copia: apps.json.prima-del-pannello

$ErrorActionPreference = 'Stop'
$file = 'C:\Program Files\Apollo\config\apps.json'
$copia = "$file.prima-del-pannello"
if (-not (Test-Path $copia)) { Copy-Item $file $copia }

$nome = 'Secondo monitor tablet'
$catalogo = Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json
$altre = @($catalogo.apps | Where-Object { $_.name -ne $nome })
$app = [ordered]@{
    'name'                   = $nome
    'uuid'                   = '7a1c5e2b-4d3f-4b8a-9e61-2c0f5d7b9a13'
    'image-path'             = 'desktop.png'
    'virtual-screen'         = $true          # monitor virtuale solo per questa app
    'virtual-display-mode'   = 'per_client'
    'virtual-display-layout' = 'extended'     # si aggiunge alla TV: niente spegnimento né cambio risoluzione
    'terminate-on-pause'     = $true          # tornando al pannello la sessione si chiude e il monitor sparisce
    'scale-factor'           = 150            # testo leggibile sugli 8 pollici del tablet
    'config-overrides'       = [ordered]@{ 'dd_config_revert_on_disconnect' = 'enabled' }
}
$catalogo.apps = @($altre) + @([pscustomobject]$app)
$json = $catalogo | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText($file, $json, (New-Object System.Text.UTF8Encoding $false))

Restart-Service ApolloService
Write-Host "Fatto: aggiunta l'app '$nome' ad Apollo. Gli altri dispositivi non cambiano." -ForegroundColor Green
Start-Sleep 4
