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
  per interromperlo.
- **Cancellazione dell'eco sul PC** (2026-11-24, `server/jarvis/eco.py`, impostazione "Cancellazione dell'eco sul
  PC", `aec_pc`, accesa di serie). Jarvis sa esattamente cosa manda alle casse, quindi lo toglie dal microfono come un
  vivavoce. Tutto quello che Jarvis ascolta (parola chiave, interruzione, trascrizione) passa dal segnale pulito.
  - **Filtro**: lineare a blocchi in frequenza (14 x 256 campioni = 224 ms), due copie: una impara sempre, l'altra
    pulisce e copia la prima solo quando cancella meglio, così chi parla sopra non lo rovina. Meno dell'1% di un core.
    Provato prima WebRTC (AEC3, pacchetto `livekit`): con l'eco forte schiaccia anche la voce di chi parla sopra
    (misurato 4117 → 91) e "Hey Jarvis" detto sopra la sua voce non veniva più riconosciuto (0 casi su 12; con il
    filtro lineare 11 su 12).
  - **Tempi**: il microfono si data contando i campioni (l'ora di arrivo dei pacchetti oscilla di decine di ms) e il
    riferimento con l'orario teorico in cui suona. `Uscita` ora ha un solo thread che manda l'audio su un orologio
    continuo e, tra una frase e l'altra e per 1,5 s dopo l'ultima, silenzio: il buffer del tablet (~0,5 s) non si
    svuota e il ritardo casse → microfono resta identico. Prima a ogni frase il tablet ripartiva da buffer vuoto con
    qualche ms o decina di ms di differenza, il filtro (allineato al campione) perdeva l'eco e scattavano falsi allarmi.
  - **Ritardo**: primo aggancio con una correlazione PHAT microfono/riferimento, precisa al ms (prima gli inviluppi
    sbagliavano di decine di ms e cambiavano idea a ogni risposta, facendo ripartire il filtro); si ricorda in
    `jarvis-dati/eco-ritardo.json`. Dopo, se l'eco si sposta (nuova risposta, rete), ogni blocco controlla se nel
    pulito resta qualcosa di simile all'eco prevista e forte (meno di 12 dB tolti): allora quel blocco non vale per le
    decisioni e, al secondo di fila, si misura la nuova posizione del percorso diretto rispetto a una "fotografia" del
    filtro di quando andava bene, la si verifica sui dati e si riallinea al campione rimettendo la fotografia
    (corretti in simulazione salti da -40 a +120 ms in ~250 ms). La stima grossolana interviene solo se la
    cancellazione fallisce per secondi.
  - **Decisioni**: interruzione a voce solo a cancellazione agganciata (10 dB o più tolti, mediana degli ultimi ~5 s,
    valida fino a 1,5 s dopo perché chi parla sopra abbassa la misura) e senza perdite in corso, con un'`Interruzione`
    che impara solo dal segnale pulito. Se le casse saturano il microfono, per quel tratto resta solo "Hey Jarvis" (il
    registro lo dice una volta: "volume troppo alto?"). Dopo ogni suono o frase, senza cancellazione agganciata, la
    coda d'eco (ritardo + 0,3 s) non si ascolta: l'eco del suono di attivazione era diventata un "Sì!". All'interruzione
    si tengono 1,6 s di audio già pulito, così la frase non perde l'inizio.
  - **Trascrizione**: il suggerimento per Whisper era "Jarvis, apri Spotify. Sì, confermo." e Whisper sul rumore lo
    ripeteva: un "Sì, confermo." inventato durante una richiesta di conferma avrebbe approvato un'azione irreversibile.
    Ora il suggerimento non è un comando né una risposta e una trascrizione che lo ripete si scarta; scartate anche le
    frasi tipiche dei sottotitoli ("Grazie per la visione", "Iscriviti al canale"...) e i segmenti che Whisper stesso
    dà per silenzio.
  - **Provato** (2026-11-24) con il Jarvis intero (Claude vero) e un tablet finto che fa eco come quello vero
    (buffer, ritardo 0,3 s, stanza, casse che distorcono, ripartenze con 0-80 ms di differenza): eco al 10% e al 30%,
    7 prove su 7 (risponde; nessun falso allarme sulla sua eco; si ferma quando gli parli sopra in ~0,5 s e trascrive
    la frase intera, "Aspetta, fermati un attimo, parlami solo dei primi computer."; "Hey Jarvis" sopra la sua voce lo
    ferma, non riprende la risposta vecchia e risponde alla richiesta nuova). Eco al 60% (satura il microfono): nessun
    falso allarme, "Hey Jarvis" funziona, l'interruzione a voce spesso no. Eco tolta 25-29 dB.
    `tests/test_jarvis_eco.py`: 12 casi (spostamenti, risposte nuove, casse che distorcono, interruzioni...).
    **Da misurare dal vivo con il tablet vero** (`jarvis.log`: "eco della voce dopo la cancellazione" dopo ogni
    risposta, con ritardo e dB tolti).
- **Modalità gioco** (2026-11-24, richiesta dell'utente: è un PC da gioco, scheda video e processore non vanno tenuti
  occupati). Misurato a riposo: Jarvis usa ~0,4% del processore e 85 MB di RAM (parola chiave e rilevamento della voce
  sulla CPU, leggerissimi); il peso è Whisper large-v3-turbo sulla scheda video (2-3 GB). Ogni 5 s `rileva_gioco()`
  guarda i dati del pannello (`/api/stato`): FPS di RivaTuner presenti = gioco; altrimenti finestra a schermo intero
  con la GPU oltre il 40%, o GPU oltre il 70% (ma non se a caricarla è llama-server). Entra dopo 2 rilevamenti di fila,
  esce dopo un minuto senza gioco. In gioco: Whisper passa alla CPU (`medium` int8, 4 thread: più preciso di `small`,
  che resta sceglibile come "veloce" in `stt_cpu`) e la scheda resta tutta al
  gioco; il modello locale dell'emergenza non si accende ("lo accendo quando finisci") e, se era acceso da Jarvis, si
  spegne. Impostazione `modalita_gioco` (pannello: "Modalità gioco"). **Non provato con un gioco vero.**
- **Modalità emergenza** (2026-11-24, `server/jarvis/emergenza.py`): senza Internet (2 controlli di fila falliti
  verso api.anthropic.com e 1.1.1.1, ogni 20 s) o quando Claude risponde con un errore di limite d'uso (per 30
  minuti, poi riprova) Jarvis passa al cervello locale e lo dice a voce; torna a Claude da solo quando è possibile.
  Impostazioni: `emergenza` auto | sempre | mai, `modello_emergenza` 27b | 27b-mtp.
  - Cervello: Qwen 3.8 27B del worker su llama-server (`C:\Users\<utente>\llm\start-server.ps1`, porta 8099). Scelto
    il profilo "27b" (32K di contesto, 40-46 token/s): è lo stesso modello del profilo MTP (~93 token/s) ma con il
    doppio di memoria, che per un agente con strumenti conta più della velocità. Lo spegne quando esce, se l'ha
    acceso lui.
  - Un solo `claude --bare -p` (`EsecutoreLocale`) fa voce ed esecutore, puntato al server locale con le stesse
    variabili del worker; strumenti essenziali, guardia, schermo; personalità in `persona_locale.md`.
  - Voce: Piper (`piper-tts`, voce `it_IT-paola-medium` in `server/jarvis-dati/piper/`, ~60 MB, fuori da git): pronta
    in 0,04-0,15 s dopo il primo caricamento. Usata anche quando edge-tts non risponde.
  - Scheda video: quando gira llama-server (worker o emergenza) Whisper passa da solo alla CPU (`small`) e torna
    sulla GPU dopo; prima il worker restava senza VRAM ed era lentissimo.
  **Provato**: voce Piper; cervello locale (con il modello MoE del worker: "quanti file ci sono in tests?" → PowerShell
  e risposta in italiano in 42 s); entrata e uscita con un Jarvis finto (`tests/test_jarvis_emergenza.py`). Non
  ancora provata la commutazione automatica dal vivo (Internet staccato o limite vero).
  - **Misure del 2026-11-24** (Claude Code 2.1.331 `--bare` con gli stessi parametri di `EsecutoreLocale`, contro un
    server finto che registra le richieste; non sul PC di Jarvis). Il prompt mandato al modello locale è piccolo:
    ~2.150 caratteri di istruzioni (persona compresa) + 12 strumenti (Bash, Edit, Read e i 9 dello schermo) per
    ~6.700 caratteri, in tutto **~2.500 token**. Quindi i 42 s della prova non venivano dal prompt di Claude Code
    e un ciclo d'agente più leggero (idea presa da OpenJarvis) non serve. Trovati due problemi veri, corretti:
    1. Claude Code manda `thinking: adaptive` anche a llama-server: Qwen ragiona prima di ogni risposta, e a 40-46
       token/s mille token di ragionamento sono ~25 s. Ora `MAX_THINKING_TOKENS=0` (provato: il campo sparisce dalla
       richiesta). Impostazione `ragionamento_locale` (falso) per riaccenderlo. Se llama-server ragiona lo stesso per
       conto suo (dipende da come lo avvia `start-server.ps1`), va spento lì: `--reasoning-budget 0`.
    2. Claude Code crede che il modello "worker" abbia **1.000.000 di token** di contesto e non riassumeva mai la
       conversazione: dopo un po' di turni (o pochi screenshot) andava oltre i 32K (16K con MTP) di llama-server.
       Ora `CLAUDE_CODE_AUTO_COMPACT_WINDOW` = 65% del contesto vero (letto da `/props` di llama-server, se no dal
       profilo). Provato col server finto: con la soglia il riassunto parte ("compact_boundary"), senza no. Il
       riassunto scatta dopo il turno che supera la soglia, per questo il margine è largo.
    In `--bare` questa versione di Claude Code non dà Write, Glob, Grep e WebFetch anche se chiesti: la persona
    locale ora dice di usare PowerShell al loro posto.
  - **Da misurare sul PC di Jarvis**: tempo di una richiesta locale prima e dopo (registro di llama-server: righe
    `prompt eval` e `eval`), se Qwen ragiona ancora, e il profilo `27b-mtp`: con il prompt a ~2.500 token e il
    riassunto automatico, i 16K bastano probabilmente per la voce e la velocità raddoppia.
- **Memoria a lungo termine** (2026-11-24, `server/jarvis/memoria.py`, idea da OpenJarvis). Dopo ogni scambio, in un
  thread a parte, Haiku (`claude -p --model haiku --tools ""`, prompt minimo) legge domanda e risposta e propone i
  fatti stabili da ricordare e quelli superati (es. "i giochi ora sono sul K" toglie "sul J"); in emergenza lo fa il
  modello locale, durante il gioco niente. Se l'esecutore sta lavorando si aspetta il turno con l'esito. Le richieste
  sotto le 4 parole non contano. I fatti (massimo 80, in `server/jarvis-dati/fatti.json`) entrano in fondo ai
  prompt di voce, esecutore e cervello locale quando il processo parte (`componi`): così anche il modello locale sa
  dove tieni le cose. La voce ha due strumenti in più: `ricorda` ("ricordati che…", fiducia "utente") e `dimentica`.
  Sul tablet: Impostazioni > Memoria (interruttore, elenco con ✕, svuota). API: `GET/POST /jarvis/memoria`.
  - **Quarantena** (`server/jarvis/sicurezza.py`): ogni fatto passa da uno scanner di istruzioni nascoste (italiano e
    inglese: "ignora le istruzioni", "d'ora in poi esegui", marcatori di ruolo, "senza chiedere conferma", comandi di
    rete, indirizzi web nei fatti automatici) e di credenziali. Se scatta, il fatto resta nel file ma non entra mai
    nei prompt: una pagina web o una finestra letta dall'esecutore non può diventare un ordine permanente. Le
    credenziali vengono tolte anche dal testo mandato a Haiku.
  - Provato con 13 test (`tests/test_jarvis_memoria.py`) e un estrattore finto; **non provato con Haiku vero**.
- **"Sì, sempre" e "No, mai" per le conferme** (2026-11-24, `server/jarvis/permessi.py`, idea da OpenJarvis). Alla
  domanda "Confermi?" si può rispondere "sì, sempre" (o "d'ora in poi", "ogni volta") e "no, mai"; sul tablet ci
  sono i due pulsanti. L'impronta è stretta apposta: tipo di azione + comando esatto (o percorso esatto) + cartella,
  quindi "sì, sempre" vale solo per lo stesso identico comando (es. la pulizia di %TEMP%). Non è ammesso per dischi,
  registro, sicurezza, spegnimento, servizi, cancellazioni definitive, disinstallazioni, `git reset`/`clean`, email,
  app collegate (MCP) e file di sistema: lì Jarvis procede solo questa volta e lo dice. "No, mai" vale per tutto.
  Il suggerimento "puoi dire anche sì, sempre" si sente una volta per avvio. Le decisioni (`jarvis-dati/permessi.json`)
  si tolgono da Impostazioni > Memoria. "Sì ma non sempre" vale come sì per questa volta.
- **Registro senza credenziali** (2026-11-24): ogni riga di `jarvis.log` passa da `sicurezza.oscura`: chiavi
  Anthropic/OpenAI/AWS/Google/GitHub/Slack/Telegram, JWT, Bearer, chiavi private e "password: …" diventano
  `[NASCOSTO:tipo]`.
- **Il meglio degli altri Jarvis** (2026-11-24). Analizzati i 238 repo del topic GitHub `jarvis-ai`; letti i 19 attivi
  nel 2026 o più popolari. Quasi tutti sono esercizi (`if "apri youtube" in frase`). Idee prese, riscritte qui:
  - **Resoconti come dati** (regola R4 di jarvis-desktop, lopadova): l'esito dell'esecutore arriva alla voce tra
    `<resoconto>…</resoconto>` con l'avviso che è un dato; se contiene frasi che sembrano ordini al modello
    (`sicurezza.ordini_nascosti`) la voce lo dice all'utente. Stessa regola nelle persone di esecutore e locale.
  - **Azioni delicate solo col tocco** (jarvis-desktop, jarvis-fr): dischi, registro, sicurezza, servizi,
    cancellazioni definitive e file di sistema (`guardia.ALTO_RISCHIO`). Un "sì" a voce non basta (una TV o un'altra
    persona non deve poter formattare un disco): Jarvis aspetta Sì sul tablet; "no" a voce vale.
    Impostazione `conferma_alta`: tocco (predefinito) | voce.
  - **Dimenticare per numero** (regola R12 di jarvis-desktop): ogni fatto ha un numero, la voce vede `[n]` nel
    prompt e `dimentica(numero)` toglie solo quello; i fatti superati dall'estrattore si tolgono solo se quasi
    identici. Prima una parola sbagliata poteva cancellare più fatti.
  - **Modalità privata** (`privata`, jarvis-desktop): cervello locale, Whisper, Piper, memoria e aggiornamenti sul
    PC; Haiku ed edge-tts bloccati (`online_permesso`). Se il cervello locale non c'è (gioco in corso, avvio) Jarvis
    lo dice e **non** passa a Claude. A voce: "modalità privata" / "esci dalla modalità privata" (riconosciute sul
    PC, non arrivano mai a un modello). Sul tablet: chip "PRIVATA · SOLO PC". Resta il controllo di rete
    dell'emergenza (contatta api.anthropic.com e 1.1.1.1 senza mandare dati).
  - **Promemoria e timer senza IA** (`server/jarvis/promemoria.py`, idea da jarvis-desktop, pi-esp-jarvis,
    jarvis-fr): "ricordami tra 20 minuti di…", "avvisami alle 18:30 di…", "domani alle 9", "tra due ore e mezza",
    "metti un timer di 5 minuti", "che promemoria ho?", "cancella i promemoria". Capiti prima di Claude: subito,
    gratis, anche in privata. Le frasi difficili ("venerdì dopo pranzo") vanno alla voce, che usa lo strumento
    `promemoria(quando, testo)`. "Ricordami che…" senza un'ora resta per la memoria. File
    `jarvis-dati/promemoria.json`; uno scaduto a PC spento si dice alla riaccensione con l'ora a cui era.
  - **Frasi pronte** (DesktopJarvis): la voce delle frasi brevi ("Procedo.", "Ci guardo."…) si prepara all'avvio e
    si tiene (fino a 80 frasi sotto i 60 caratteri): partono senza aspettare la sintesi.
  - **Avvisi di temperatura** (jarvis-fr, con i sensori che il pannello legge già): processore oltre
    `soglia_cpu` (90 °C) o scheda video oltre `soglia_gpu` (85 °C) per 30 s → una frase, al massimo ogni 15 minuti.
  - **Finestre lette come testo** (JARVIS-VORNEX): strumenti `controlli` e `usa_controllo` nel server MCP schermo,
    con UI Automation (`uiautomation`, nuovo in requirements-jarvis.txt). Elenco numerato di pulsanti, campi, voci
    con nome e valore; poi clic, scrivi, spunta, seleziona, espandi per numero. La finestra si rilegge a ogni uso
    e se è cambiata si rifiuta. Utilizzabile anche dal modello locale, che non vede le immagini. Non vale per
    giochi e molte app Electron/Chrome.
  - Provato con 9 test (`tests/test_jarvis_dal_meglio.py`, UI Automation con controlli finti) e il pannello in un
    browser senza schermo. **Non provato sul PC di Jarvis**: UI Automation vera, Piper in privata, i promemoria
    detti a voce, gli avvisi con sensori veri. Dopo `git pull`: `pip install -r requirements-jarvis.txt`.
  - Da fare più avanti: dettatura nell'app in primo piano (akshayaggarwal99), briefing del mattino, Jarvis come
    server MCP per le altre sessioni di Claude Code (`jarvis_speak`, `jarvis_ask_user` di jarvis-desktop), annullare
    le operazioni sui file (jarvis-fr).
- **App per Quest 3: Jarvis XR** (2026-11-24, `web/xr.html` + `web/xr.js`, three.js r187 in `web/lib/`). Il pannello e
  Jarvis come ologrammi nella stanza, in realtà mista (passthrough), stile Minority Report / Iron Man. È una pagina
  WebXR servita dal pannello del PC: niente APK da compilare né da installare (si può aggiungere alla libreria del
  visore dal browser).
  - **Cosa c'è**: la sfera di Jarvis al centro (colore e anelli seguono lo stato, barre col livello della voce, onda
    a ogni cambio di stato); attorno, ad arco, i pannelli Conversazione (con Stop, Nuova, Privata, Riordina),
    Telemetria (CPU e GPU con carico e temperatura, FPS con la storia di 90 s, RAM, VRAM, programma in primo piano),
    App e scorciatoie (le stesse del tablet, con le icone), Media, Promemoria (con ora, Briefing e Dettatura). Le
    autorizzazioni compaiono grandi a 55 cm davanti agli occhi, opache, con il resto attenuato; gli avvisi volano in
    alto. Anello sul pavimento e pulviscolo luminoso. Suoni brevi e vibrazione dei controller a ogni tocco.
  - **Mani**: tocchi i pulsanti con la punta dell'indice (si preme attraversando il vetro, cerchio-cursore che si
    stringe mentre ti avvicini, al massimo un tocco ogni 350 ms); pizzichi un pannello e lo sposti (si gira sempre
    verso di te); con due mani sullo stesso pannello lo ingrandisci o rimpicciolisci (0,45-2,6x). Pizzica e tieni
    la sfera per parlare: il microfono del visore registra finché tieni, la frase va a `POST /jarvis/voce` (PCM
    16 kHz) e Jarvis la trascrive sul PC come quelle del tablet; se c'è una conferma aperta, è la risposta. Le
    posizioni restano salvate nel visore; "Riordina" le rimette attorno a te. **Controller**: raggio + grilletto
    (tenuto sulla sfera = parla), impugnatura per spostare.
  - **Aprirla nel visore, in Wi-Fi** (dal 2026-11-24 sera, `server/xr_https.py`): il Quest apre la realtà mista e il
    microfono solo da pagine `https://` (o `localhost`). Il pannello allora serve le stesse pagine e API anche in
    **HTTPS sulla porta 47443**, con un certificato che genera da solo per gli indirizzi del PC (rifatto se cambiano;
    file in `server/xr-certificato/`, fuori da git; serve `cryptography`, in requirements-jarvis.txt). Niente cavo,
    niente Tailscale, niente app da installare. Le chiamate `/jarvis/*` passano dal pannello a Jarvis (anche gli
    eventi in diretta e i video a pezzi), così la pagina usa un indirizzo solo.
    **Indirizzo corto**: il server HTTPS prova prima la porta **443** e, se riesce, apre anche la porta 80 che
    rimanda a https: nel Browser del Quest basta scrivere l'**IP del PC** (es. `192.168.1.50`), senza porta né chiave.
    Se la 443 è occupata usa la 47443 (`https://192.168.1.50:47443`). L'indirizzo nudo apre Jarvis XR.
    **Senza chiave da copiare** (`AbbinaVisore` in pannello.py): il visore chiede di collegarsi e mostra un codice di
    4 cifre; Jarvis lo chiede sul tablet a tutto schermo ("Un visore vuole collegarsi, il codice è 4 7 1 9: lo
    consento?", Consenti/Rifiuta, anche a voce), oppure si consente dalle impostazioni del PC. La chiave arriva al
    visore una volta sola e resta salvata lì. Una richiesta per volta, al massimo 6 ogni 10 minuti, valide 3 minuti.
    1. Nel Browser del Quest scrivi l'IP del PC (è scritto grande nelle impostazioni del PC > Jarvis XR).
    2. Solo la prima volta: il visore avvisa che il certificato non è verificato, **Avanzate** > **Procedi**.
    3. Il visore mostra il codice: sul tablet rispondi **Consenti**.
    4. **ENTRA IN REALTÀ MISTA**, concedi microfono e mani. Le volte dopo entra da solo.
    Il QR nelle impostazioni del PC fa lo stesso saltando il codice (contiene già la chiave).
    Conviene dare al PC un IP fisso nel router (prenotazione DHCP): l'indirizzo non cambia e il certificato nemmeno.
    Se Windows chiede il permesso del firewall per Python sulla porta nuova, va dato per le reti private.
    Alternative se servisse: cavo USB con `adb reverse tcp:47800 tcp:47800` e `http://localhost:47800/xr`, oppure il
    flag `unsafely-treat-insecure-origin-as-secure` in `chrome://flags` del Browser del Quest con `http://<IP>:47800`.
    Sul PC la stessa pagina (`http://localhost:47800/xr`) è un'**anteprima** col mouse.
  - Provato in Chrome senza visore con pannello e Jarvis finti: rendering, pulsanti col mouse (app, media, privata,
    nuova), autorizzazione e risposta, e **mani simulate** (giunti finti): il tocco preme Privata, il pizzico sposta
    la Telemetria e salva la posizione, due mani ingrandiscono le App. **Non provato su un Quest 3 vero**: tracciamento
    delle mani reale, passthrough, microfono del visore. HTTPS e inoltro provati in Python
    (`tests/test_xr_https.py`: certificato, stretta di mano nel thread, eventi in diretta, voce, video a pezzi,
    abbinamento col codice, rimando da http a https);
    **non provato** che il Browser del Quest, dopo "Procedi", tratti la pagina come sicura per la realtà mista
    (secondo la documentazione di NVIDIA CloudXR succede).
- **Il PC dentro il visore** (2026-11-24, `server/jarvis/visore.py`, `visore_mcp.py`, pannelli nuovi in `web/xr.js`).
  "Ehi Jarvis, aprimi la cartella delle foto sul desktop" → la voce chiama `visore_cartella("foto", "desktop")`,
  Jarvis trova la cartella sul PC (`risolvi_cartella`: Desktop, Immagini, Documenti, Download, Video, Musica anche
  se spostati in OneDrive, poi cartella utente e altri dischi, fino a 3 livelli, con sinonimi come foto/photos/
  immagini) e il visore la apre: il pannello esce dalla sfera e vola davanti agli occhi. Il messaggio alla voce dice
  "[l'utente indossa il visore…]" quando l'app è aperta (`/jarvis/flusso?xr=1` conta i visori collegati).
  - **Esploratore**: griglia 6×3 con anteprime vere delle foto (JPEG ridotti sul PC, girati secondo l'EXIF, in
    cache), cartelle, video, testi; ⌂ (Questo PC), ⬆, pagine col gesto o con ◀ ▶.
  - **Foto, video, testi**: la foto grande con la striscia di miniature sotto (tocca per saltare); **scorri con la
    mano** davanti al pannello, senza toccarlo, per la successiva o la precedente (un gesto = una foto, si riarma
    quando la mano si ferma). Video riprodotti nel visore (mp4, webm, mov; a pezzi con Range, si può saltare
    toccando la barra); testi con ▲ ▼. "Apri sul PC" per tutto il resto (mai programmi o script).
  - **Finestre del PC in diretta**: "aprimi Chrome nel visore", oppure 🪟 FINESTRE nella barra sopra la sfera.
    L'immagine arriva ~5 volte al secondo anche se la finestra è coperta (PrintWindow); il pannello prende le
    proporzioni della finestra. **Tocchi l'immagine e il clic avviene nello stesso punto della finestra vera**; modo
    DOPPIO e DESTRO per il tocco dopo, ▲ ▼ rotella (anche scorrendo con la mano), INVIO, ⟳ DAVANTI. Per scrivere:
    tocca il campo e usa la dettatura. 🖥 SCHERMO mostra lo schermo intero allo stesso modo.
  - **Disposizione alla Iron Man**: il pannello più recente davanti, gli altri si spostano ai lati ad arco, più
    piccoli; toccandone uno torna davanti. Quelli spostati con le mani restano dove li hai messi. Al massimo 5.
  - **Barra sopra la sfera**: 📁 FILE, 🪟 FINESTRE, 🖥 SCHERMO, ✕ CHIUDI (tutti i pannelli aperti).
  - Strumenti per voce, esecutore e cervello locale: `visore_cartella`, `visore_file`, `visore_finestra`,
    `visore_schermo`, `visore_elenco_finestre`, `visore_chiudi`. Se la cartella non si trova la voce chiede il
    percorso all'esecutore e richiama; se il visore è spento la cosa si apre sul PC come prima.
  - API (chiave del pannello): `GET /jarvis/file/elenco|anteprima|dati`, `POST /jarvis/file/apri`, `GET /jarvis/finestre`,
    `/jarvis/finestre/immagine`, `/jarvis/schermo/immagine`, `POST /jarvis/finestre/azione`. **Cosa si vede**: solo la
    cartella utente e i dischi diversi da quello di sistema; mai Windows, Program Files, ProgramData, AppData, cartelle
    con il punto (.ssh…) e file nascosti o di sistema. Sola lettura, tranne "apri sul PC" (non per .exe, .bat, .ps1,
    .lnk e simili) e i comandi alle finestre, che con la chiave equivalgono a mouse e tastiera.
  - Provato in Chrome con Jarvis finto: cartella aperta "a voce" con le anteprime, foto aperta toccando, scorrimento
    con la mano simulata (una foto per gesto), disposizione a ventaglio, finestra in diretta con tocco → clic nel
    punto giusto e rotella, elenco delle finestre. In Python (`tests/test_jarvis_visore.py`, 8 test): elenco e
    permessi, cartelle a parole, anteprime, file a pezzi, niente programmi, richieste della voce. **Non provato sul PC
    di Jarvis né sul Quest**: cattura vera delle finestre (PrintWindow, DPI, più monitor), clic veri, video nel visore.
- **Autorizzazioni a tutto schermo sul tablet** (2026-11-24, richiesta dell'utente): la conferma occupa tutto lo
  schermo con SÌ e NO alti 190 px, "Sì, sempre" / "No, mai" sotto, il comando in piccolo, una barra col tempo che
  resta (`scade` nell'evento) e i pulsanti attivi solo dopo 450 ms (un tocco partito prima non conferma). Le azioni
  delicate hanno l'etichetta "SOLO TOCCO"; le domande delle altre sessioni mostrano le opzioni come pulsanti grandi.
  Si apre da sola anche a pannello chiuso.
- **Animazioni del pannello Jarvis** (2026-11-24): messaggi che entrano scorrendo, schede in dissolvenza, chip che
  lampeggiano quando cambiano, onda d'urto e particelle in orbita sulla sfera, stato che "respira" mentre lavora,
  avvisi (promemoria, temperature) che scendono dall'alto, barra "DETTATURA" in basso, bordo verde in privata.
  Solo transform e opacità: leggere anche per il WebView 88 dello SHIELD.
- **Dettatura** (2026-11-24, `server/jarvis/tastiera.py`, idea da akshayaggarwal99/jarvis-ai-assistant): "dettatura" /
  "detta" e quello che dici si scrive nella finestra in primo piano sul PC (SendInput, caratteri Unicode), trascritto
  da Whisper sul PC e **mai mandato a un modello**. "a capo", "cancella l'ultima parola", "fine dettatura" (o "stop").
  Durante la dettatura l'ascolto non scade.
- **Briefing del mattino** (2026-11-24): "buongiorno" / "briefing" / "com'è la giornata". Il PC raccoglie promemoria
  di oggi, meteo (Open-Meteo, gratis e senza chiave, con la città di Impostazioni > Privacy e sicurezza; mai in
  privata), spazio libero sul disco di sistema e temperature, e la voce ne fa un buongiorno di 30 secondi; se
  l'esecutore ha calendario e posta collegati, li aggiunge.
- **Jarvis per le altre sessioni di Claude Code** (2026-11-24, `server/jarvis/jarvis_mcp.py`, idea da jarvis-desktop):
  server MCP con `jarvis_parla(testo)`, `jarvis_chiedi(domanda, opzioni)` (domanda a voce, risposta detta o toccata
  sul tablet o nel visore, fino a 10 minuti) e `jarvis_promemoria`. Si registra una volta:
  `claude mcp add --scope user jarvis -- python "<cartella>\server\jarvis\jarvis_mcp.py"` (il comando esatto è
  nelle impostazioni del PC). API: `POST /jarvis/annuncia`, `/jarvis/domanda`, `/jarvis/promemoria-esterno`. Conferme
  e domande ora passano una alla volta (`blocco_domande`): due insieme si sovrascrivevano.
- **Annulla l'ultima modifica** (2026-11-24, `server/jarvis/annulla.py`, idea da jarvis-fr): la guardia copia ogni file
  prima che l'esecutore lo scriva o modifichi (Write, Edit) e annota i file nuovi. "Annulla l'ultima modifica" /
  "rimetti com'era" chiede conferma e rimette com'erano i file dell'ultimo lavoro (stessa sessione, senza pause oltre
  10 minuti); quelli nuovi vanno in `jarvis-dati/annulla/tolti`, mai cancellati. Le cancellazioni l'esecutore ora le
  fa nel Cestino (esecutore.md). Si tengono le ultime 300 modifiche.
- Test: 57 (`tests/test_jarvis_extra.py` +10). Non provati dal vivo: dettatura in un'app vera, il meteo reale,
  jarvis_mcp registrato in Claude Code, annulla dopo un lavoro vero dell'esecutore.
- **Valutato e lasciato fuori da OpenJarvis** (2026-11-24): il framework intero (agenti, motori, Rust, training: il
  cervello resta Claude Code), il suo "motore" per l'emergenza (stesso llama-server, ma senza la commutazione
  automatica di `emergenza.py`), i canali di messaggistica, il router e il loop guard. Da valutare più avanti: il
  briefing del mattino e Kokoro (voci `if_sara`/`im_nicola`) come voce locale al posto di Piper.
- **Accesso da iPhone, Apple Watch e app esterne**: `POST /jarvis/chiedi` (testo → prima risposta della voce, attende
  fino a 60 s) e `GET /jarvis/eventi?dopo=N` (messaggi successivi, per l'esito dei lavori lunghi), con il token del
  pannello. Accettati anche gli indirizzi Tailscale (100.64.0.0/10) oltre alla rete locale, così da fuori casa basta
  Tailscale sul PC e sull'iPhone (il firewall di Windows deve consentire Python anche su quella rete).
- **App per iPhone e Apple Watch** (`apple/`, SwiftUI): sull'iPhone conversazione, pulsante per parlare
  (riconoscimento italiano di iOS), risposta letta ad alta voce, impostazioni (indirizzo del PC e chiave del pannello);
  sull'Apple Watch "Parla a Jarvis" (dettatura dell'orologio), risposta letta dall'orologio e, per 10 minuti, l'esito
  dei lavori lunghi. Indirizzo e chiave passano dall'iPhone all'orologio (WatchConnectivity). Il progetto Xcode si
  genera con XcodeGen e lo compila GitHub Actions su macOS (`.github/workflows/jarvis-ios.yml`, come Moonlight XR):
  IPA non firmato nelle release `jarvis-ios-N` (non "latest"), da installare con Sideloadly, AltStore o SideStore.
  L'icona dell'orologio per ora è quella standard (il formato a 1024 px non passava actool).
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
  Prova del Jarvis intero su una porta a parte (`/jarvis/chiedi`): risposta veloce in 5 s, esito in 14 s.
  Errore trovato e corretto: un file `server/jarvis/locale.py` copriva il modulo `locale` di Python e i server MCP
  (avviati da quella cartella) non partivano; la voce allora scriveva la chiamata come testo e Jarvis la leggeva ad
  alta voce. Ora il file è `llm_locale.py`, i tag non vengono mai letti e una chiamata scritta come testo viene
  eseguita lo stesso.
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
- **Unione di stamattina e del pomeriggio** (2026-11-24 sera, ramo `jarvis-unione`): il lavoro "in prova" della
  mattina (eco sul PC, conferme da lontano, Whisper medium sulla CPU, frasi spezzate unite) era solo sul ramo
  `jarvis-in-prova`; ora è unito a `main` + Jarvis XR senza cavo. Nelle conferme da lontano il conto alla rovescia
  mostrato è di 90 s come l'attesa vera (prima diceva 45), e le azioni delicate chiedono il tocco "sul telefono o
  sull'orologio" se sei fuori casa. Test: tutti passano sul PC di casa (Windows); i test della dettatura su Windows
  premevano tasti veri nella finestra davanti e ora registrano soltanto; i test non scrivono più in `jarvis.log` e
  `pannello.log` veri; quelli del visore e dell'HTTPS si lanciano con `python -m pytest`. **Provato sul PC**: pannello
  riavviato in ascolto su 47800, 443 (HTTPS, risponde 200) e 80 (rimando a https); Jarvis da amministratore
  ripartito con `/jarvis/riavvia`, eco sul PC attiva, Claude Code trovato, tablet ricollegato (altoparlante e
  microfono). **Non provato**: Quest vero, conferma da telefono/orologio dal vivo, interruzione a voce con l'eco.

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
`vX.Y.Z` con `PannelloPC.apk`. Ultima release: v1.9.1 (2026-11-23, Jarvis interrompibile); app iPhone/Watch: `jarvis-ios-N`.
