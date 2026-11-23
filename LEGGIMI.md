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

## Spegnere il PC dal pannello
Tasto rosso ⏻ in alto a sinistra (dal 2026-11-15): chiede **Sospendi**, **Iberna**, **Riavvia** o **Spegni**
(spegnimento completo, `shutdown /s` senza avvio rapido). Il server aspetta 1,5 s, così la risposta arriva al
tablet, poi esegue (`COMANDI_ENERGIA` in `server/pannello.py`; sospensione e ibernazione con `SetSuspendState`
di .NET, che abilita da sé il privilegio di spegnimento). La pagina è servita dal PC, quindi l'app del tablet
non cambia. Provato: aspetto del menu (anteprima 960x600), rifiuto di un comando sconosciuto, comandi generati;
**non provato** eseguendo davvero sospensione, ibernazione, riavvio o spegnimento.

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
- **Posizione del monitor del tablet** (dal 2026-11-15): Windows metteva il monitor virtuale a destra dello
  schermo principale, ma il tablet sta a sinistra, quindi il mouse andava dalla parte sbagliata e non tornava su
  FIFA. Con `"lato_tablet": "sinistra"` (o `"destra"`) in `config.json` il server, a ogni comparsa del monitor,
  lo mette da quel lato allineato in basso (`sposta_tablet`, una volta sola per comparsa). Sul PC di casa è
  impostato `sinistra`; senza la chiave non si sposta niente. Provato: il monitor è passato a (-2880, 540).
- **Spazio della dock** (dal 2026-11-15): la registrazione come appbar non riduceva l'area di lavoro del tablet
  e le finestre massimizzate finivano sotto la dock. Ora la dock ogni 2 s controlla l'area di lavoro e, se
  serve, la reimposta (`riserva_spazio`, anche con `SPI_SETWORKAREA`); è anche più bassa (7,5% dello schermo,
  prima 10%). Provato: area di lavoro fino a 2039, dock da 2039 a 2160, Claude massimizzato sopra la dock.
- **Giochi a schermo intero** (dal 2026-11-15, `CustodeGioco` in `server/pannello.py`): un tocco sul tablet con
  Artemis sposta il mouse del PC sul monitor virtuale e clicca lì, e il gioco perdeva i comandi (in FC 27 serviva
  Alt+Tab). Ora, se la finestra davanti era a schermo intero su uno schermo vero e il primo piano passa altrove
  mentre il cursore è sul monitor virtuale, il server si ricorda il gioco e gli ridà il primo piano (con il
  cursore al centro del suo schermo) **appena usi il controller** (XInput: tasto, grilletto o levetta oltre la zona
  morta). Finché il controller resta fermo puoi scrivere sul tablet quanto vuoi. Prima la regola era "dito fermo
  1,2 s" e, scrivendo sul tablet con FIFA aperto, la tastiera tornava al gioco dopo pochi secondi (log del
  2026-11-15: sei ritorni in un minuto). Senza controller collegato il gioco torna quando il cursore lascia il
  monitor del tablet. Si aspetta la fine dell'apertura di un'app dalla dock; un Alt+Tab fatto apposta non viene
  toccato. Il ritorno riprova fino a 3 volte (l'ultima con `SwitchToThisWindow`, come Alt+Tab). Si spegne con
  `"torna_al_gioco": false` in `config.json`. Provato: la decisione (`python tests/test_custode.py`), la lettura
  del controller e il riconoscimento di FC 27; un tocco vero ha funzionato con la regola vecchia, **la regola del
  controller non è ancora provata dal vero**.
- **Caratteri più grandi sul tablet** (dal 2026-11-15): con `"scala_tablet": 250` in `config.json` il server, a
  ogni comparsa del monitor virtuale, imposta il ridimensionamento di Windows di quel solo display
  (`scala_display`, la stessa impostazione di Impostazioni > Schermo > Scala, via `DisplayConfigSetDeviceInfo`).
  Prima era al 175%. Sul PC di casa è impostato 250; senza la chiave non si cambia niente. Provato: la lettura
  della scala (schermo principale 200%, uguale a `GetDpiForMonitor`); l'impostazione sul tablet **non ancora
  provata** (il monitor virtuale non c'era).
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

## Jarvis: comandi vocali (dal 2026-11-23, app 1.9.0)
Si dice **"Hey Jarvis"** vicino al tablet (anche a schermo spento), si sente un doppio suono, poi si parla:
"apri Spotify", "quanto spazio c'è sul disco C?", "cancella i file temporanei"… Claude Code esegue sul PC e
Jarvis risponde a voce dal tablet. Pannello di Jarvis sul tablet: **scorrimento dal bordo sinistro verso destra**
(o la linguetta luminosa a sinistra), con la sfera animata, la conversazione, un campo per scrivere e le
impostazioni. Dal PC: sezione 🎙️ Jarvis nella pagina delle impostazioni.

- **Pronuncia**: il modello della parola chiave (`hey_jarvis` di openWakeWord) è inglese. Detto all'inglese
  ("Hei Giàrvis") scatta con punteggio 0,98–1,00; "Ehi Jarvis" letto all'italiana ("Iàrvis") solo 0,10 (prova con
  voci sintetiche, 2026-11-23). Se non scatta: pronuncia inglese, oppure "Sensibilità" più alta nel pannello.
- **Come è fatto** (`server/jarvis/`, processo a parte avviato e tenuto in vita da `pannello.py`, porta 47802,
  stesso token del pannello; `adb reverse` inoltra anche la 47802):
  - `principale.py`: stati (riposo, ascolto, penso, lavoro, parlo, conferma, seguito), parola chiave sempre
    attiva (interrompe Jarvis mentre parla), rilevamento del parlato (Silero), server HTTP per il tablet;
  - `voce_claude.py`: faster-whisper (large-v3-turbo sulla GPU con le DLL CUDA di pip, se no `small` sulla CPU:
    ~2 s per una frase) e voce edge-tts (it-IT-DiegoNeural, serve Internet);
  - `esecutore.py`: un processo `claude -p` sempre aperto (stream-json): la conversazione continua da un comando
    all'altro, la risposta si dice frase per frase mentre arriva. Personalità in `persona.md`;
  - `guardia.py`: hook PreToolUse di Claude Code. Azioni irreversibili (cancellare, spegnere, registro,
    disinstallare, `git push`, inviare messaggi…) → Jarvis chiede "Confermi?" a voce (o Sì/No sul tablet), 45 s
    senza risposta = no. Livelli: irreversibili (predefinito), sempre, mai;
- **Solo Claude** (deciso il 2026-11-23): la modalità Gemini Live è stata tolta. L'abbonamento Google AI Pro alza i
  limiti dell'app Gemini, di AI Studio sul web e di Gemini CLI, ma non dà quota API a un programma: servirebbe una
  chiave a parte, gratuita con limiti o a pagamento. Jarvis usa solo Claude Code con l'abbonamento Claude.
- **Tablet** (`JarvisServizio.java`): servizio in primo piano (notifica "Jarvis") nel processo persistente; manda il
  microfono al PC (PCM 16 kHz) e suona la voce che arriva (trame `[tipo][lunghezza][dati]`, vedi `trame.py`).
  Wakelock parziale e Wi-Fi ad alte prestazioni mentre il PC è collegato, anche a batteria; fuori da Doze
  (`allow-in-power-save` in `privapp-permissions-pannello.xml` e `dumpsys deviceidle whitelist`). Permesso del
  microfono concesso dall'installazione (`pm grant`).
- Impostazioni in `server/jarvis.json` (fuori da git), registro in `server/jarvis.log`.
  Si disattiva del tutto con `"jarvis": false` in `config.json`. Dipendenze: `requirements-jarvis.txt` (~3 GB con
  i modelli; `installa.ps1` lo chiede). Claude Code deve aver fatto l'accesso: `claude auth login`.
- **Voce e compiti separati** (2026-11-23): la sessione principale di Claude Code è la *voce* (predefinito
  Sonnet, ragionamento basso: risponde subito) e i compiti li fa il sotto-agente *esecutore* (`--agents`, prompt in
  `server/jarvis/esecutore.md`; predefinito Opus, ragionamento alto). La separazione è obbligata dalla guardia: con
  `JARVIS_DELEGA=1` ogni strumento chiamato dalla voce (hook senza `agent_type`) viene rifiutato con l'invito ad
  affidarlo all'esecutore. Modelli e ragionamento si impostano a parte nel pannello e sul PC. Provato: la voce ha
  provato Bash, è stata respinta, ha passato il compito, l'esecutore (Opus) l'ha fatto e la voce ha riassunto.
- **Lavori in sottofondo e aggiornamenti** (2026-11-23): l'esecutore parte in sottofondo, la voce resta libera
  (si può continuare a parlarle) e riporta da sola il risultato quando arriva. Jarvis segue i lavori con gli eventi
  `background_tasks_changed` / `task_progress` / `task_notification` di Claude Code (stream-json) e legge le ultime
  azioni dal registro del sotto-agente (`<sessione>/subagents/agent-*.jsonl`). Se lavora e Jarvis tace da 30 s
  (impostazione "Ogni quanto"), una frase di aggiornamento scritta al volo da Haiku (`claude -p --model haiku
  --tools ""`, ~6 s), con una frase fissa di riserva. Il pannello mostra "Lavoro in corso". Provato: servizi
  automatici fermi, ~1 minuto di lavoro, un aggiornamento e poi il risultato.
- **Testo dicibile** (2026-11-23): la sintesi vocale leggeva i simboli alla lettera ("#cosa ti propongo#" →
  "cancelletto…"). `pulisci_per_voce` (`trame.py`) ora toglie ogni simbolo di formattazione, trasforma elenchi e
  tabelle in frasi (una pausa per voce), frecce in "poi", unità in parole ("37 GB" → "37 giga", "72°C" → "72
  gradi", "3840x2160" → "3840 per 2160"), e i due punti non spezzano più la frase. In più la persona chiede alla
  voce di raccontare i risultati invece di leggerli (le due o tre cose che contano, il resto su richiesta).
  Il registro `jarvis.log` ora contiene ogni frase detta (`detto:`).
- **Riavviare Jarvis da amministratore**: un processo senza privilegi non può fermarlo. `POST /jarvis/riavvia`
  (token del pannello) lo fa chiudere da solo con codice 3; lo fa ripartire l'attività o `pannello.py` (entro un
  minuto). Prima di questa versione: rieseguire `server\jarvis-amministratore.ps1`, che lo ferma e lo riavvia.
- **Interromperlo mentre parla** (2026-11-23, app 1.9.1): mentre Jarvis parla il microfono resta in ascolto; se una
  voce supera di 1,6 volte l'eco attesa per ~400 ms, Jarvis si zittisce, butta il resto della risposta (anche le frasi
  già sintetizzate: contatore `gen_voce`) e ascolta la frase dall'inizio (800 ms tenuti da parte). L'eco attesa è
  il volume di quello che Jarvis sta facendo uscire (massimo dell'ultimo secondo) per un rapporto imparato nei primi
  480 ms di ogni frase (`Interruzione` in `trame.py`). Simulato con voci sintetiche, eco al 10-90% e ritardi di
  150-350 ms: mai scattato sulla sola eco; con eco debole (10%) si ferma 0,4-0,8 s dopo che la persona parla, con
  eco forte (35% e oltre) la persona non viene distinta. Per questo il tablet usa la modalità "chiamata"
  (`VOICE_COMMUNICATION`, `MODE_IN_COMMUNICATION`, impostazione "Cancellazione dell'eco"): lo SHIELD Tablet **non**
  ha l'effetto `AcousticEchoCanceler` ("AEC non disponibile"), da misurare se la modalità chiamata riduce comunque
  l'eco (`jarvis.log`: "eco della voce" dopo ogni frase, efficace sotto 0,15). La parola chiave resta sempre attiva
  per interromperlo. **Non provato dal vivo.**
- **Token: voce e esecutore in due processi** (2026-11-23, `server/jarvis/cervello.py`). Misurato: con un solo
  Claude Code ogni turno della voce si portava dietro ~44.600 token di istruzioni (strumenti, skill, plugin) che la
  voce non usa, e ogni aggiornamento ~18.000. Limitare gli strumenti nello stesso processo non va (provato:
  l'esecutore perde anche PowerShell). Ora:
  - la **voce** è un `claude -p` minimo: `--system-prompt-file persona.md` (al posto del prompt di Claude Code),
    `--tools ""`, `--strict-mcp-config` con il solo server `voce_mcp.py` (esegui, stato_lavoro, ferma_lavoro),
    `--disable-slash-commands`, `--setting-sources ""`, `--no-session-persistence`. Una chiacchiera costa ~4.100 token
    (-91%) e risponde in 1,4 s. Ogni messaggio porta data e ora (la voce non ha più il prompt di sistema con la data);
  - l'**esecutore** (`esecutore.py`) è il Claude Code completo (Opus, guardia, schermo, Chrome, skill) con
    `esecutore.md`; lavora solo quando la voce chiama `esegui`, riceve le parole dell'utente e le istruzioni della
    voce, e alla fine l'esito torna alla voce come messaggio "[Esito del lavoro …]" che lei racconta. La sua sessione
    è quella che compare nell'app sotto pc-panel;
  - aggiornamenti: il modello locale (`locale.py`, llama-server del worker su 127.0.0.1:8099) se è già acceso, se no
    Haiku con prompt minimo (~900 token invece di ~18.000).
  Provato: "Quanto spazio libero c'è sul disco C?" → la voce risponde subito, l'esecutore usa PowerShell, la voce
  racconta l'esito con un'opinione (18 s in tutto). La delega obbligata della guardia (`JARVIS_DELEGA`) non serve più.
- **Ascolto continuo** (2026-11-23): Jarvis partiva prima che l'utente finisse di parlare (frase chiusa dopo 0,8 s
  di silenzio) e poi non ascoltava più mentre lavorava. Ora: la frase si chiude dopo 1,4 s di silenzio (impostazione
  "Pausa per finire la frase", 0,6-3 s; simulato: un'esitazione di 0,7 s non spezza più la frase, una di 1 s o più
  sì); dopo "Hey Jarvis" la conversazione resta aperta e il microfono ascolta anche mentre pensa, lavora o
  l'esecutore è in sottofondo (finché c'è lavoro in corso non scade); quello che aggiungi arriva subito a Claude.
  Se aggiungi un pezzo entro 10 s dall'inizio della risposta, la risposta in corso si butta (interruzione del turno)
  e Claude riceve il pezzo nuovo con "(continuo quello che stavo dicendo)"; più tardi il pezzo si accoda e basta.
  Le frasi fisse non dicono più "signore". **Non provato dal vivo.**
- **Conversazioni nell'app Claude, progetto pc-panel** (2026-11-23): Claude Code di Jarvis lavora nella cartella
  pc-panel (impostazione `cartella_lavoro`, vuota = pc-panel), quindi le sessioni stanno sotto questo progetto. Le
  sessioni di `claude -p` non compaiono da sole nell'app: appena c'è la prima risposta e Jarvis è fermo (niente
  lavori in sottofondo, niente gioco a schermo intero davanti), Jarvis chiude un attimo il suo processo (il comando
  rifiuta una sessione aperta: provato, codice 1), esegue `claude --desktop --resume <id>` e alla richiesta dopo
  riprende la stessa sessione con `--resume`. L'app legge anche quello che Jarvis aggiunge dopo (provato: secondo
  turno visibile nell'app; l'orario nell'elenco però non si aggiorna). Se l'utente scrive nella sessione dall'app,
  Jarvis se ne accorge (nel registro ci sono più domande di quelle che ha fatto lui; si contano le domande perché
  l'app riscrive le righe vecchie) e alla richiesta dopo comincia una conversazione nuova. Provato con un
  messaggio mandato alla sessione dall'app. Le sessioni restano "Untitled" nell'elenco.
- **Haiku o Sonnet per la voce**: misurato a processo già avviato, Sonnet comincia a parlare in 1,1–1,3 s e Haiku
  in 1,7–2,1 s (fine in ~5 s per entrambi): Sonnet resta il predefinito, Haiku si può scegliere nel pannello.
- **Personalità** (`persona.md`): dà del tu, niente "signore", simpatica, ironica, dice la sua. Una conversazione
  vecchia ne imita lo stile: dopo il cambio si riparte con "Nuova conversazione".
- **Sostituto completo di Claude Code** (richiesta dell'utente, 2026-11-23: "non deve fare niente di meno"). Il
  Claude Code di Jarvis ha già skill, plugin, sotto-agenti, ricerca in rete e attività programmate come quello
  dell'app; mancavano gli strumenti che l'app aggiunge da sé. Ora:
  - modello **Opus** e ragionamento **alto** per i compiti (vedi sopra);
  - **schermo** (`server/jarvis/schermo_mcp.py`, server MCP passato con `--mcp-config`): screenshot (anche di una
    zona), clic, trascina, scorri, scrivi, tasti, finestre. Il computer use ufficiale di Claude Code a riga di
    comando esiste solo su macOS e non in modalità `-p`, quindi è fatto in casa (mss + SendInput). Provato:
    "che programma ho davanti?" → screenshot → risposta giusta;
  - **Claude in Chrome** con `--chrome` (impostazione "Claude in Chrome"): gli strumenti compaiono anche in `-p`
    (provato che ci sono, non ancora usati per navigare).
  Restano fuori solo gli strumenti propri dell'app (gestione delle sessioni dell'app, browser integrato dell'app).
- **Come amministratore** (dal 2026-11-23): senza privilegi Claude Code non può scrivere in Program Files (es.
  `C:\Program Files\Apollo\config`). `server/jarvis-amministratore.ps1` (tasto destro > Esegui con PowerShell,
  chiede da solo l'UAC, una volta sola) registra l'attività `Pannello PC - Jarvis` (all'accesso, privilegi più alti,
  riavvio automatico se cade) che lancia `avvia-jarvis.vbs`; da quel momento `pannello.py` non avvia più la sua copia
  senza privilegi e rilancia l'attività se Jarvis non risponde per un minuto. Si toglie con `-Togli`. La persona
  chiede di aprire i programmi con `explorer.exe` (partono come utente normale, così le scorciatoie del pannello
  arrivano ancora). Le conferme a voce restano. Provato il 2026-11-23: attività registrata dall'utente (RunLevel Highest), Jarvis ripartito da lì, il pannello non avvia più la sua copia, e Jarvis ha creato e cancellato un file di prova in `C:\Program Files\Apollo\config` (la cancellazione è passata dalla conferma). Nota: da un processo senza privilegi la riga di comando del Jarvis da amministratore non si vede (WMI la nasconde).
- **Conversazioni nell'app Claude per Windows**: le sessioni di `claude -p` non compaiono da sole nell'elenco
  dell'app. Jarvis le ricorda (`server/jarvis-dati/conversazioni.json`, quella in corso in `sessione.txt`, così
  sopravvive ai riavvii) e le apre nell'app con `claude --desktop --resume <id>` (pulsante "⧉ Sul PC" nel pannello
  di Jarvis, elenco in Impostazioni > Conversazioni e nella pagina delle impostazioni del PC). Il comando rifiuta
  l'uscita rediretta: va lanciato con una console nascosta propria. Una conversazione aperta nell'app passa
  all'app: Jarvis alla richiesta successiva ne comincia una nuova (due programmi sulla stessa conversazione
  perderebbero messaggi). Provato: due conversazioni di Jarvis comparse nell'app (cartella `C:\Users\rikyr`).
- Il microfono predefinito del PC di casa (Virtual Desktop) si blocca all'apertura: i dispositivi del PC si aprono
  in un thread a parte e, se non rispondono in 4 s, Jarvis lo segnala invece di bloccarsi.
- **Provato** (2026-11-23): richiesta scritta dal pannello → ricerca in rete → risposta mostrata e detta;
  conferma della guardia con un file di prova sul Desktop (no → il file resta, sì → cancellato); voce dal tablet;
  microfono del tablet collegato via USB; riconoscimento italiano su GPU e CPU; parola chiave con voci sintetiche;
  pannello Jarvis in anteprima 960x600. **Non ancora provato**: parola chiave detta da una persona vera al tablet,
  tablet a schermo spento e a batteria per ore (Doze), casse e microfono del PC. Dopo un riavvio del tablet si è
  ricollegato a Jarvis da solo in Wi-Fi (microfono compreso).

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
`vX.Y.Z` con `PannelloPC.apk`. Ultima release: v1.9.1 (2026-11-23, Jarvis interrompibile).
