# Jarvis da iPhone e Apple Watch

Jarvis gira sul PC; iPhone e Apple Watch gli mandano la richiesta e leggono la risposta. Servono due dati, che trovi
sul PC nella pagina delle impostazioni del pannello, sezione **Jarvis → App Jarvis per iPhone e Apple Watch**:

- **Indirizzo**: in casa quello del PC in rete locale (es. `192.168.1.96`); da fuori casa quello Tailscale (vedi sotto).
- **Chiave**: la chiave del pannello.

## Da fuori casa: Tailscale (una volta sola)

1. Installa [Tailscale](https://tailscale.com/download) sul PC e sull'iPhone, ed entra con lo stesso account.
2. Sul PC, nell'icona di Tailscale, leggi l'indirizzo del PC: `100.x.y.z`. Usa quello al posto dell'indirizzo di casa.
3. Se Windows chiede di consentire Python sulla rete di Tailscale, consenti.

Tailscale è una rete privata tra i tuoi dispositivi: nessuna porta aperta sul router.

## Apple Watch: Comando Rapido "Jarvis" (consigliato)

Funziona sull'orologio senza installare niente: il comando si crea sull'iPhone e passa da solo sul Watch.

1. Apri **Comandi** sull'iPhone → **+** → chiama il comando **Jarvis**.
2. Aggiungi **Detta testo** (lingua: Italiano; Interrompi ascolto: Dopo una pausa).
3. Aggiungi **Ottieni contenuto dell'URL**:
   - URL: `http://INDIRIZZO:47802/jarvis/chiedi` (es. `http://192.168.1.96:47802/jarvis/chiedi`)
   - Metodo: **POST**
   - Intestazioni: chiave `X-Token`, valore = la chiave del pannello
   - Corpo della richiesta: **JSON**, con tre campi:
     - `testo` (Testo) = *Testo dettato*
     - `attesa` (Numero) = `40`
     - `parla_a_casa` (Booleano) = *No*
4. Aggiungi **Ottieni valore del dizionario**: valore per la chiave `risposta` in *Contenuti dell'URL*.
5. Aggiungi **Mostra risultato** con *Valore del dizionario* (se il comando parte con Siri, Siri lo legge ad alta voce).
6. Nei dettagli del comando attiva **Mostra su Apple Watch**.

Uso: **"Ehi Siri, Jarvis"** sull'orologio, poi detti la richiesta. Puoi anche mettere il comando su un quadrante
(complicazione Comandi) o, su Apple Watch Ultra, sul tasto Azione.

- **Lavori lunghi**: Jarvis risponde subito ("ci guardo"); l'esito arriva sul tablet a casa, nell'app Jarvis
  dell'iPhone o, se lo chiedi di nuovo ("a che punto sei?"), dal comando.
- **Conferme**: se Jarvis risponde *"Devo … Confermi?"*, rilancia il comando entro un minuto e mezzo e di' **"sì"** o
  **"no"**.

## iPhone: app Jarvis

L'IPA è nelle release `jarvis-ios-N` del repository. Si installa in sideload (Sideloadly, AltStore o SideStore) con il
tuo Apple ID: con un account gratuito va rinnovata ogni 7 giorni. Nelle impostazioni dell'app metti indirizzo e
chiave. Conversazione, pulsante per parlare, risposte lette ad alta voce, pulsanti **Sì / No** per le conferme.

L'IPA contiene anche un'app per Apple Watch, ma con il sideload e un Apple ID gratuito l'orologio di solito non è nel
profilo di firma e l'app non si installa sul Watch: per l'orologio usa il Comando Rapido qui sopra.
