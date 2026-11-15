# Pannello PC per lo SHIELD Tablet

Pannello sempre acceso con temperature, carichi, FPS, app e scorciatoie del PC.

## Come è fatto
- `server/pannello.py`: gira sul PC (porta 47800). Legge i sensori da MSI Afterburner
  (memoria condivisa `MAHMSharedMemory`; se Afterburner è chiuso usa psutil e nvidia-smi, senza FPS
  né temperatura CPU), serve la pagina e riceve i comandi. Parte all'accesso a Windows con l'attività
  pianificata `Pannello PC` (Utilità di pianificazione, utente corrente, senza admin) che lancia
  `server/avvia-pannello.vbs` (python.exe nascosto). Dal 2026-11-14 non più dalla cartella Esecuzione
  automatica: Windows la eseguiva ~2 minuti dopo l'accesso. Una sola copia alla volta (mutex
  `Local\PannelloPCServer`; una seconda si chiude e scrive "server già in esecuzione" nel log).
- FPS: li misura RivaTuner (RTSS) agganciandosi al gioco quando parte; Afterburner li espone in
  `MAHMSharedMemory` (0x50/0x53/0x55). RTSS aveva "Livello di rilevamento" = Nessuno (`EnableHooking=0` in
  `Profiles\Global`): il 2026-11-14 riattivato con `server/rtss-rilevamento.ps1` (da amministratore, RTSS gira
  elevato; OSD lasciato spento). Copia di prima: `RTSS-Global.prima-del-pannello`. Un gioco già aperto non
  viene agganciato: va riavviato. Da verificare con FC 27 (anti-cheat EA).
- Se il pannello non riceve dati da 8 s attenua i valori e mostra "In attesa del PC…" (PC in avvio o spento).
- `web/`: la pagina del pannello (960x600, niente librerie esterne).
- `android/`: l'app del tablet. È la schermata Home: parte da sola, resta a tutto schermo e con il
  tablet in carica lo schermo non si spegne. Prova prima `127.0.0.1` (il server lo inoltra via USB con
  `adb reverse`), poi l'IP del PC in Wi-Fi.

## Cambiare app e scorciatoie
- Dal PC (più comodo): collegamento **Pannello PC - Impostazioni** sul Desktop e nel menu Start. Trascina per
  riordinare, aggiungi dal menu Start, dai giochi Steam installati o con "Sfoglia…" (qualsiasi file o link),
  registra le scorciatoie premendo i tasti. Si salva da solo e il tablet si aggiorna subito.
- Dal tablet: ✎ in alto a destra, poi tocca un riquadro (o + e ✕).

## Simbiosi con il PC
- La simbiosi vive nel processo dell'app (`Applicazione.java`), dichiarato `android:persistent="true"`:
  Android lo avvia all'accensione, non lo chiude per memoria e lo riavvia se muore (provato con `kill -9`).
  Prima stava nell'activity: il 2026-11-14 Android l'ha chiusa (ANR a schermo spento) e lo schermo non si
  riaccendeva più. Conseguenza: `adb install` non aggiorna più l'app, vedi "Ricostruire l'app del tablet".
- PC in sospensione o spento: il server avvisa il tablet (eventi di Windows) e il tablet spegne lo schermo.
  In sospensione l'avviso spesso **non arriva** (Windows si sospende prima): il server manda un dato al
  secondo, e se il flusso tace da 15 s il tablet spegne comunque lo schermo ("PC muto"). Se il PC non si è
  mai fatto vivo, dopo 45 s. PC di nuovo acceso: lo schermo si riaccende da solo.
  (Dopo l'avvio del tablet, o quando il PC ricompare via USB, l'attesa è di 150 s: il PC può essere in avvio.)
- Dopo la ripresa dalla sospensione adb può restare **offline** (niente `adb reverse`: pannello fermo su "In
  attesa del PC", nemmeno `adb kill-server` lo sblocca, solo staccare il cavo). Se il PC si ricollega via USB
  ma in 40 s non arrivano dati, l'app spegne e riaccende il debug USB (`Settings.Global.ADB_ENABLED`, serve
  `WRITE_SECURE_SETTINGS` in `privapp-permissions-pannello.xml` e nell'APK di `/system`), al massimo 2 volte e
  solo nei 4 minuti dopo il ricollegamento (mai a PC addormentato: ricollegare l'USB potrebbe svegliarlo).
  Provato simulando il ricollegamento: adb è caduto ed è tornato in 3 s; con un vero adb offline non provato.
- 2026-11-15, ripresa vera: il tablet ha ritrovato il PC via Wi-Fi, quindi la riparazione (che guardava solo
  "nessun dato") non è scattata, e la pagina del pannello è rimasta ferma sull'indirizzo USB. Dall'app 1.8.1:
  se i dati arrivano **solo dalla rete** con il cavo collegato a un PC acceso, dopo 20 s si riavvia il debug USB
  (al massimo 2 volte, poi di nuovo dopo 30 min; ogni 10 s si prova se l'USB risponde di nuovo). E la pagina
  (`web/pannello.js`), dopo 20 s senza dati, chiede all'app di ricaricarsi provando tutti gli indirizzi.
  Non provato con un adb offline vero (non si riesce a simularlo: il server rimette `adb reverse` ogni 15 s).
- Cosa riaccende lo schermo: il flusso del server ("acceso"), il **collegamento dati USB** (broadcast
  `android.hardware.usb.action.USB_STATE`, `configured=true`: un PC acceso ha riconosciuto il tablet) oppure
  un caricatore da muro (AC). La sola corrente USB **no**: il PC ha l'Avvio rapido (Windows va in ibernazione,
  `LastBootUpTime` non cambia) e la sua porta dà corrente anche da spento; allo spegnimento la toglie e la ridà
  per un attimo. Il collegamento USB nei 90 s dopo l'avviso di spegnimento/sospensione si ignora.
- Android accende da sé lo schermo a ogni cambio di corrente (anche a PC in sospensione la porta la toglie e
  la ridà): se il PC non manda dati, l'app lo rispegne dopo ~1 s (si vede un lampo), anche se lo schermo era
  stato spento a mano con il tasto.
- PC spento e tablet senza corrente: il tablet **non** si spegne, resta a schermo spento con la CPU libera di
  dormire (il wakelock parziale si tiene solo in carica). Si spegne del tutto solo con batteria ≤ 10 %, senza
  PC e senza corrente da 1 minuto (serve l'app in `/system/priv-app/PannelloPC` con
  `privapp-permissions-pannello.xml`, e `dpm set-active-admin`).
- Perché non si spegne: da spento del tutto lo SHIELD Tablet non riparte collegando il cavo. Con
  `fastboot oem off-mode-charge 1` (impostazione attuale) mostra la batteria disegnata dal bootloader NVIDIA
  (partizioni CHG/LBP), quindi Android non parte e una regola `on charger` non serve; con `off-mode-charge 0`
  resta proprio spento. Provato il 2026-11-14: serve il tasto di accensione.
- Provato il 2026-11-14 (app 1.4.1), con eventi di Windows simulati, server fermato e `dumpsys battery`:
  sospensione/ripresa, spegnimento/annullato, PC irraggiungibile, niente corrente → resta acceso, corrente
  tornata → schermo acceso. Non provato: lo spegnimento a batteria ≤ 10 % e uno spegnimento vero del PC.
- 2026-11-14 sera, spegnimento vero (app 1.4.1): il tablet ha spento lo schermo all'avviso, ma 50 s dopo l'ha
  riacceso il ritorno della corrente USB a PC spento. Corretto nell'app 1.5.2 e provato simulando avviso,
  corrente USB tolta/ridata e `USB_STATE` (schermo resta spento; si riaccende con USB dati dopo 90 s o con il
  server). Da verificare con il prossimo spegnimento vero che `configured` resti `false` a PC spento.

## Secondo monitor
- Pulsante **Monitor** (o scorrimento dal bordo destro verso sinistra): Artemis apre l'app Apollo
  "Secondo monitor tablet", che crea un monitor virtuale *in più* (layout `extended`): la TV resta com'è.
- Schermata di blocco: lo spegnimento dello schermo con `lockNow` (simbiosi) lascia attiva la schermata di blocco
  a scorrimento. Il pannello ci sta sopra (`FLAG_SHOW_WHEN_LOCKED`), Artemis no: Android lo metteva in pausa
  appena aperto e lui si chiudeva ("il secondo monitor non parte", 2026-11-14). Dall'app 1.7.2 il pannello la
  toglie con `requestDismissKeyguard` (senza PIN non serve toccare nulla) prima di aprire Artemis e a ogni
  ritorno in primo piano. In alternativa: Impostazioni > Sicurezza > Blocco schermo: Nessuno (non fatto).
- La dock si apre solo se il monitor virtuale è **in più** rispetto a uno schermo vero: con la TV spenta Apollo
  tiene acceso un monitor virtuale come unico schermo del PC, e il 2026-11-15 la dock ci era finita sopra.
- Per tornare al pannello: scorrimento dal bordo sinistro verso destra. La sessione si chiude
  (`terminate-on-pause`) e il monitor virtuale sparisce.
- Sul monitor del tablet compare da sola una **dock** in basso (`server/dock.py`, appbar di Windows: le finestre
  massimizzate si fermano sopra) con le app della sezione "Secondo monitor" del companion. Toccando un'icona
  l'app si apre e la sua finestra viene portata e massimizzata sul monitor del tablet. Le app del Microsoft
  Store (es. Claude) si aprono con `shell:AppsFolder\<AppID>`.
- App **già aperta** (dal 2026-11-15): prima il server aspettava che Windows portasse davanti la finestra, ma
  Windows non lo permette a un processo in secondo piano, e l'app restava dov'era (con Claude aperto sul monitor
  principale bisognava cliccarla sulla barra delle applicazioni). Ora cerca le finestre dell'app per
  AppUserModelID (`id_app`: proprietà della finestra o pacchetto del processo; Esplora file per classe
  `CabinetWClass`) e sposta direttamente quella più in alto. La finestra viene anche alzata sopra le altre
  (`metti_davanti`: sempre in cima e poi no, aggancio all'input), e la dock al tocco chiama
  `AllowSetForegroundWindow` così l'app che si apre può venire davanti. Provato: riconoscimento di Claude ed
  Esplora file aperti; **non ancora provato con un tocco vero** dalla dock. Le app della dock indicate come file
  `.lnk` usano ancora solo il vecchio modo (finestra nuova o finestra che viene davanti).
- **Giochi a schermo intero** (dal 2026-11-15, `CustodeGioco` in `server/pannello.py`): un tocco sul tablet con
  Artemis sposta il mouse del PC sul monitor virtuale e clicca lì, e il gioco perdeva i comandi (in FC 27 serviva
  Alt+Tab). Ora, se la finestra davanti era a schermo intero su uno schermo vero e il primo piano passa altrove
  mentre il cursore è sul monitor virtuale, il server lascia arrivare il tocco e poi, a dito fermo da 1,2 s (o
  appena il mouse torna fuori dal monitor del tablet, o finita l'apertura di un'app dalla dock), rimette il gioco
  in primo piano e il cursore al centro del suo schermo. Un Alt+Tab fatto apposta non viene toccato. Si spegne
  con `"torna_al_gioco": false` in `config.json`. Provato: la decisione (`python tests/test_custode.py`) e il
  riconoscimento di FC 27 come finestra a schermo intero; **non ancora provato con un tocco vero** dal tablet.
- L'app si aggiunge ad Apollo con `server/apollo-secondo-monitor.ps1` (da amministratore; copia di
  sicurezza `apps.json.prima-del-pannello`). Le altre app e gli altri dispositivi non cambiano.
  Attenzione: in Vibepollo 1.18.4 il layout globale predefinito è `exclusive` (spegne gli altri schermi).

Tasti validi nelle scorciatoie: ctrl, shift, alt, win, a-z, 0-9, f1-f24, tab, esc, enter, space,
printscreen, left/up/right/down, home, end, pgup, pgdn, insert, delete, volume_up/down/mute,
media_play_pause/next/prev. Le scorciatoie non arrivano alle finestre avviate come amministratore.

## Ricostruire l'app del tablet
Serve se cambia l'IP del PC o il token in `config.json`, o se cambia il codice:

    python android/costruisci.py --installa

Alza prima `--version-code` in `costruisci.py`. L'app è persistent: lo script fa `adb root`, sostituisce
`/system/priv-app/PannelloPC/PannelloPC.apk` e riavvia il tablet (al riavvio vince la versione più alta;
`pm uninstall -k` fallisce con `DELETE_FAILED_DEVICE_POLICY_MANAGER` perché l'app è amministratore, ed è
normale). Un permesso privilegiato nuovo va prima aggiunto a `privapp-permissions-pannello.xml` in
`/system/etc/permissions/` (con il controllo "enforce" un permesso mancante può bloccare l'avvio).

## Note sul tablet (SHIELD Tablet K1, LineageOS 16, 2 GB di RAM)
- Su questo tablet YouTube, Reddit, app Google, Gemini, Foto, Stremio, Chrome e due lettori QR sono
  disattivati (Impostazioni > App per riattivarli): partivano tutte all'avvio e, con 2 GB di RAM,
  bloccavano Android con lo schermo nero (watchdog in `prepareUserStorage`).
- "Apri Android" (in modalità modifica) apre il launcher normale; il tasto Home riporta al pannello.
- Se lo schermo resta nero: Power premuto 15 secondi.

## Abbinamento automatico (APK generico, dal 2026-11-15)
- `python android/costruisci.py --generico` crea in `android/build-generico/` un APK senza IP né chiave: è quello
  pubblicato nelle Release per chiunque. La build personale (`costruisci.py` senza opzioni) resta com'era.
- Il tablet non abbinato cerca il PC ogni 3 s: `GET http://127.0.0.1:47800/api/abbina` (USB con `adb reverse`) e
  broadcast UDP `PANNELLO-PC?` sulla porta 47801. Il server risponde con porta, nome del PC e app di Apollo; la
  chiave del pannello solo con l'abbinamento aperto: 10 minuti all'avvio finché nessun tablet si è mai collegato
  (`"abbinato"` in `config.json`), oppure 10 minuti dal pulsante **📲 Abbina un tablet** nelle impostazioni.
- Un tablet già abbinato che non trova il PC per tre giri di indirizzi lo ricerca in rete (IP cambiato) con la
  chiave che ha già.
- `installa.ps1` (senza amministratore): dipendenze, attività pianificata `Pannello PC`, collegamenti alle
  impostazioni (`pannello.py --collegamenti`) e primo avvio.

## Pubblicazione su GitHub
Ogni miglioramento si pubblica su https://github.com/rikyroky91-commits/symbiodeck (`main`): LEGGIMI aggiornato,
controllo che nel diff non ci siano segreti (config.json, chiave di firma, build e log restano fuori), commit e
push. Se cambia l'app del tablet: nuova versione in `costruisci.py`, APK generico (`--generico`) e release
`vX.Y.Z` con `PannelloPC.apk`. Ultima release: v1.8.1 (2026-11-15).
