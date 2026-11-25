package it.rikyr.pannello;

import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Tiene il tablet "in simbiosi" con il PC, indipendentemente dalla pagina web:
 *  - PC acceso            -> schermo acceso
 *  - PC spento/sospeso    -> schermo spento: subito se il PC avvisa; se l'avviso non arriva (la sospensione
 *    è più veloce del messaggio), quando il flusso dei dati, che arriva ogni secondo, tace da SILENZIO;
 *    se il PC non si è mai fatto vivo, dopo ATTESA_ASSENZA.
 *  - PC spento -> il tablet resta acceso a schermo spento (a batteria la CPU dorme). Da spento del tutto non si
 *    riaccenderebbe da solo: la schermata della batteria la disegna il bootloader NVIDIA e Android non parte.
 *  - Lo schermo si riaccende quando il PC si fa vivo: flusso del server, oppure collegamento dati USB (il PC
 *    acceso "configura" il tablet come dispositivo), oppure un caricatore da muro. La sola corrente USB NON basta:
 *    la porta del PC dà corrente anche da spento/sospeso e la toglie e la ridà; Android a ogni cambio di corrente
 *    accende lo schermo, e se il PC non risponde lo si rispegne (rispegniDopoCorrente).
 *  - Dopo la ripresa dalla sospensione il collegamento adb può restare "offline" (niente adb reverse, niente
 *    dati): se il PC si è ricollegato via USB ma i dati non arrivano, si riavvia il debug USB (come staccare e
 *    riattaccare il cavo), al massimo TENTATIVI_ADB volte. Lo stesso se i dati arrivano solo dalla rete mentre il
 *    cavo USB è collegato a un PC acceso (2026-11-15: al risveglio il tablet ha ritrovato il PC via Wi-Fi e la
 *    riparazione non scattava, mentre la pagina del pannello restava ferma sull'indirizzo USB).
 *  - batteria sotto BATTERIA_MINIMA senza PC e senza corrente da PRIMA_DI_SPEGNERE -> spegnimento completo
 * Ascolta lo stesso flusso SSE del pannello (/api/flusso), provando USB e poi rete.
 */
final class Simbiosi extends Thread {
    interface Azioni {
        void spegniSchermo();
        void accendiSchermo();
        void spegniTablet();
        void riavviaAdb();
        /** La parte "sentinella" del flusso: impostazioni e comandi dal PC (Sentinella.dalPc). */
        void sentinella(JSONObject s);
    }

    private static final String TAG = "PannelloSimbiosi";
    private static final long ATTESA_ASSENZA = 45_000;    // PC mai visto (o sparito prima di mandare dati)
    private static final long ATTESA_AVVIO = 150_000;     // dopo l'accensione del tablet il PC può essere ancora in avvio
    private static final long SILENZIO = 15_000;          // il server manda un dato al secondo: 15 s di silenzio = PC giù
    private static final long PRIMA_DI_SPEGNERE = 60_000; // senza PC e senza corrente, con la batteria quasi scarica
    private static final int BATTERIA_MINIMA = 10;        // %: sotto si spegne del tutto per proteggere la batteria
    private static final long DOPO_AVVISO = 90_000;       // dopo "spegnimento/sospensione" i sobbalzi dell'USB non contano
    private static final long ATTESA_ADB = 40_000;        // PC ricollegato via USB ma nessun dato: si riavvia il debug USB
    private static final long FINESTRA_ADB = 240_000;     // ...solo entro questo tempo dal ricollegamento (PC sveglio)
    private static final int TENTATIVI_ADB = 2;

    private final String[] flussi;
    private final Azioni azioni;
    private final long avvio = SystemClock.elapsedRealtime();
    private volatile boolean attivo = true;
    private volatile boolean alimentato = true;
    private volatile int batteria = 100;
    private volatile long collegatoAlle = 0;             // PC appena ricomparso: gli si concede di nuovo l'attesa d'avvio
    private volatile long avvisoSpentoAlle = 0;          // quando il PC ha annunciato spegnimento o sospensione
    private volatile boolean usbPc = false;              // collegamento dati USB con un PC acceso
    private volatile long usbRicollegataAlle = 0;        // ultimo collegamento USB "vero" (PC che si accende o riprende)
    private volatile long ultimoContatto = SystemClock.elapsedRealtime();
    private volatile long ultimoDato = 0;                // ultima riga "data:" ricevuta dal server
    private volatile String ultimoStato = "";
    private volatile long pcAssenteDa = 0;
    private volatile boolean schermoSpentoDaNoi = false;
    private volatile boolean tabletInSpegnimento = false;
    private volatile int tentativiAdb = 0;
    private volatile long soloReteDa = 0;                // dati che arrivano solo dalla rete con il cavo USB collegato
    private volatile long ultimaProvaUsb = 0;
    private volatile long ultimoRiavvioAdb = 0;

    Simbiosi(String[] pagine, boolean schermoSpento, Azioni azioni) {
        super("simbiosi");
        // se il processo riparte (Android lo riavvia) a schermo spento, lo si considera spento "per via del PC":
        // così quando il PC torna lo si riaccende
        schermoSpentoDaNoi = schermoSpento;
        setDaemon(true);
        this.azioni = azioni;
        flussi = new String[pagine.length];
        for (int i = 0; i < pagine.length; i++) flussi[i] = pagine[i].replace("/?t=", "/api/flusso?t=");
    }

    void ferma() { attivo = false; interrupt(); }

    /** Il PC sta mandando dati adesso (non spento, non sospeso, non muto). */
    private boolean pcVivo(long ora) {
        return "acceso".equals(ultimoStato) && ultimoDato > 0 && ora - ultimoDato < SILENZIO;
    }

    private boolean inAttesaAvvio(long ora) {
        return ora - Math.max(avvio, collegatoAlle) < ATTESA_AVVIO;
    }

    /**
     * Android ha appena acceso lo schermo per un cambio di corrente: se il PC non c'è va rispento, chiunque
     * l'avesse spento prima (l'app o il tasto di accensione). true se l'ha rispento.
     */
    synchronized boolean rispegniDopoCorrente() {
        long ora = SystemClock.elapsedRealtime();
        boolean pcGiu = schermoSpentoDaNoi
                || (!pcVivo(ora) && !inAttesaAvvio(ora) && (ultimoDato > 0 || ora - avvio > ATTESA_ASSENZA));
        if (!pcGiu) return false;
        if (pcAssenteDa == 0) pcAssenteDa = ora;
        schermoSpentoDaNoi = true;
        Log.i(TAG, "schermo acceso dal cambio di corrente con il PC giù: lo rispengo");
        azioni.spegniSchermo();
        return true;
    }

    /** Cambio di alimentazione. Riaccende lo schermo solo un caricatore da muro (AC): la corrente USB da sola no. */
    synchronized void alimentazione(boolean collegato, boolean daMuro, int livello) {
        boolean prima = alimentato;
        alimentato = collegato;
        batteria = livello;
        if (collegato && !prima) {
            if (daMuro) risveglia("caricatore collegato");
            else Log.i(TAG, "corrente USB: aspetto che il PC si colleghi davvero");
        }
        valuta();
    }

    /** Stato del collegamento dati USB: "configurato" vuol dire che dall'altra parte c'è un PC acceso. */
    synchronized void usb(boolean configurato) {
        boolean prima = usbPc;
        usbPc = configurato;
        if (!configurato || prima) return;
        long ora = SystemClock.elapsedRealtime();
        if (avvisoSpentoAlle != 0 && ora - avvisoSpentoAlle < DOPO_AVVISO) {
            Log.i(TAG, "USB ricollegata subito dopo l'avviso di spegnimento: la ignoro");
            return;
        }
        if (ora - ultimoRiavvioAdb > 15_000) {   // il ricollegamento provocato dal riavvio del debug non conta
            usbRicollegataAlle = ora;
            if (ora - ultimoRiavvioAdb > 600_000) tentativiAdb = 0;
        }
        risveglia("PC collegato via USB");
    }

    private void risveglia(String motivo) {
        collegatoAlle = SystemClock.elapsedRealtime(); // il PC si sta accendendo: gli si dà il tempo di avviare il server
        ultimoContatto = collegatoAlle;
        if (schermoSpentoDaNoi) {
            schermoSpentoDaNoi = false;
            Log.i(TAG, motivo + ": riaccendo lo schermo");
            azioni.accendiSchermo();
        }
    }

    @Override public void run() {
        Thread guardiano = new Thread("simbiosi-guardiano") {
            @Override public void run() {
                while (attivo) {
                    try { Thread.sleep(2000); } catch (InterruptedException e) { return; }
                    provaUsb();
                    controlla();
                }
            }
        };
        guardiano.setDaemon(true);
        guardiano.start();
        while (attivo) {
            boolean collegato = false;
            for (String f : flussi) {
                if (!attivo) return;
                if (ascolta(f)) { collegato = true; break; }
            }
            if (!collegato) stato("assente");
            try { Thread.sleep(5000); } catch (InterruptedException e) { guardiano.interrupt(); return; }
        }
    }

    /** Legge il flusso finché resta aperto. true se ha ricevuto almeno un dato. */
    private boolean ascolta(String indirizzo) {
        HttpURLConnection c = null;
        boolean ricevuto = false;
        boolean viaUsb = indirizzo.contains("://127.0.0.1");
        try {
            c = (HttpURLConnection) new URL(indirizzo).openConnection();
            c.setConnectTimeout(3000);
            c.setReadTimeout(20000); // il server manda un dato al secondo e comunque un ping ogni 15 s
            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"));
            String riga;
            while (attivo && (riga = r.readLine()) != null) {
                if (riga.startsWith("data: ")) {
                    ricevuto = true;
                    ultimoDato = SystemClock.elapsedRealtime();
                    if (viaUsb) {
                        tentativiAdb = 0;
                        soloReteDa = 0;
                    } else if (usbPc && soloReteDa == 0) {
                        soloReteDa = ultimoDato;
                        Log.i(TAG, "dati dal PC solo via rete con il cavo USB collegato: controllo il collegamento USB");
                    }
                    JSONObject dati = new JSONObject(riga.substring(6));
                    stato(dati.optString("pc", "acceso"));
                    JSONObject sentinella = dati.optJSONObject("sentinella");
                    if (sentinella != null) {
                        try { azioni.sentinella(sentinella); } catch (Exception e) { Log.w(TAG, "sentinella: " + e); }
                    }
                } else if (riga.startsWith(":")) {
                    ultimoContatto = SystemClock.elapsedRealtime();
                }
            }
        } catch (Exception e) {
            Log.d(TAG, "flusso " + indirizzo + ": " + e);
        } finally {
            if (c != null) c.disconnect();
        }
        return ricevuto;
    }

    /** Con i dati che arrivano solo dalla rete, ogni 10 s si prova se il PC risponde di nuovo anche via USB. */
    private void provaUsb() {
        long ora = SystemClock.elapsedRealtime();
        if (soloReteDa == 0 || ora - ultimaProvaUsb < 10_000) return;
        ultimaProvaUsb = ora;
        for (String f : flussi) {
            if (!f.contains("://127.0.0.1")) continue;
            HttpURLConnection c = null;
            try {
                URL u = new URL(f);
                c = (HttpURLConnection) new URL(u.getProtocol(), u.getHost(), u.getPort(), "/").openConnection();
                c.setConnectTimeout(2000);
                c.setReadTimeout(3000);
                if (c.getResponseCode() == 200) {
                    soloReteDa = 0;
                    tentativiAdb = 0;
                    Log.i(TAG, "il PC risponde di nuovo anche via USB");
                }
            } catch (Exception e) {
                Log.d(TAG, "prova USB: " + e);
            } finally {
                if (c != null) c.disconnect();
            }
            return;
        }
    }

    /** Ogni 2 s: il PC che mandava dati tace? il collegamento adb è rimasto bloccato dopo la ripresa? */
    private synchronized void controlla() {
        long ora = SystemClock.elapsedRealtime();
        if ("acceso".equals(ultimoStato) && ultimoDato > 0 && ora - ultimoDato > SILENZIO && !inAttesaAvvio(ora)) {
            ultimoStato = "muto";
            spegniSchermo("PC muto da " + (ora - ultimoDato) / 1000 + " s (sospeso o spento)");
        }
        if (usbPc && usbRicollegataAlle > 0 && ultimoDato < usbRicollegataAlle
                && ora - usbRicollegataAlle > ATTESA_ADB && ora - usbRicollegataAlle < FINESTRA_ADB
                && ora - ultimoRiavvioAdb > 60_000 && tentativiAdb < TENTATIVI_ADB) {
            tentativiAdb++;
            ultimoRiavvioAdb = ora;
            Log.i(TAG, "PC ricollegato via USB ma nessun dato: riavvio il debug USB (tentativo " + tentativiAdb + ")");
            azioni.riavviaAdb();
        }
        // il PC è sicuramente sveglio (manda dati via rete) ma l'USB non risponde: si riavvia il debug USB
        if (usbPc && soloReteDa > 0 && ora - soloReteDa > 20_000 && ora - ultimoRiavvioAdb > 120_000
                && tentativiAdb < TENTATIVI_ADB) {
            tentativiAdb++;
            ultimoRiavvioAdb = ora;
            Log.i(TAG, "dati solo via rete da " + (ora - soloReteDa) / 1000 + " s: riavvio il debug USB (tentativo "
                    + tentativiAdb + ")");
            azioni.riavviaAdb();
        }
        if (tentativiAdb >= TENTATIVI_ADB && ora - ultimoRiavvioAdb > 1_800_000) tentativiAdb = 0; // si riprova fra 30 min
        valuta();
    }

    private synchronized void stato(String pc) {
        long ora = SystemClock.elapsedRealtime();
        if (!"assente".equals(pc)) ultimoStato = pc; // solo gli stati detti dal server: "assente" lo deduciamo noi
        if ("acceso".equals(pc)) {
            ultimoContatto = ora;
            pcAssenteDa = 0;
            avvisoSpentoAlle = 0;
            if (schermoSpentoDaNoi) {
                schermoSpentoDaNoi = false;
                Log.i(TAG, "PC acceso: riaccendo lo schermo");
                azioni.accendiSchermo();
            }
        } else if ("spegnimento".equals(pc) || "sospensione".equals(pc)) {
            ultimoContatto = ora;
            if (avvisoSpentoAlle == 0) avvisoSpentoAlle = ora; // conta dal primo avviso, non da quelli ripetuti
            spegniSchermo("PC in " + pc);
        } else { // assente
            long attesa = inAttesaAvvio(ora) ? ATTESA_AVVIO : ATTESA_ASSENZA;
            if (ora - ultimoContatto > attesa) spegniSchermo("PC irraggiungibile");
        }
        valuta();
    }

    private void spegniSchermo(String motivo) {
        if (pcAssenteDa == 0) pcAssenteDa = SystemClock.elapsedRealtime();
        if (!schermoSpentoDaNoi) {
            schermoSpentoDaNoi = true;
            Log.i(TAG, motivo + ": spengo lo schermo");
            azioni.spegniSchermo();
        }
    }

    private void valuta() {
        if (tabletInSpegnimento || pcAssenteDa == 0 || alimentato || batteria > BATTERIA_MINIMA) return;
        if (SystemClock.elapsedRealtime() - pcAssenteDa > PRIMA_DI_SPEGNERE) {
            tabletInSpegnimento = true;
            Log.i(TAG, "PC spento, niente corrente e batteria al " + batteria + "%: spengo il tablet");
            azioni.spegniTablet();
        }
    }
}
