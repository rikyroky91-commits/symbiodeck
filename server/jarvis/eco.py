"""Cancellazione dell'eco sul PC: toglie dal microfono la voce di Jarvis che rientra dalle casse.

Serve per poter interrompere Jarvis parlandogli sopra (a voce o con "Hey Jarvis"): lo SHIELD Tablet non ha la
cancellazione dell'eco di Android e le sue casse sono a pochi centimetri dal microfono. Jarvis conosce esattamente
il segnale che manda alle casse, quindi lo può sottrarre, come fanno i vivavoce.

Cose imparate con le simulazioni (2026-11-24, voci vere, stanza, casse che distorcono, rete che oscilla):
- WebRTC (AEC3) toglie molta eco ma, quando l'eco è forte, schiaccia anche la voce di chi parla sopra: "Hey Jarvis"
  detto sopra la sua voce non veniva più riconosciuto. Qui si usa quindi un filtro solo lineare (FiltroLineare):
  toglie 16 dB dopo ~3 s di voce e ~25 dB dopo ~10 s, resta imparato tra una risposta e l'altra e lascia intatta
  la voce di chi parla, che poi riconoscono la parola chiave, il rilevatore di voce e la trascrizione;
- l'allineamento si rovina se si usa l'ora di arrivo dei pacchetti (la rete la fa oscillare di decine di ms): il
  microfono è un flusso continuo a 16 kHz, quindi il tempo si conta dai campioni; il riferimento usa l'orario
  teorico di riproduzione deciso da Uscita;
- il ritardo (invio al tablet -> eco nel microfono, ~0,3-1 s) si stima confrontando gli inviluppi; il filtro copre
  ~220 ms, quindi il riferimento gli si dà 60 ms prima dell'eco; la stima si cambia (e il filtro riparte) solo se
  la cancellazione non funziona e tre stime di fila concordano su un ritardo diverso;
- se le casse sono così forti da saturare il microfono, la distorsione non si può sottrarre: per quel tratto non si
  decide niente (ma se a saturare è chi parla forte sopra, non conta);
- l'interruzione a voce si accetta solo con la cancellazione agganciata (almeno 10 dB tolti, misurati in continuo):
  prima di allora resta "Hey Jarvis", così non ci sono falsi allarmi.
"""
import json
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

FREQ = 16000
MAX_RITARDO = 1.6      # secondi di riferimento conservati


class FiltroLineare:
    """Filtro adattivo a blocchi nel dominio della frequenza (PBFDAF, overlap-save) con due copie (schema di Ochiai):
    quella di sfondo si adatta sempre; quella in uso, che produce l'uscita, la copia solo quando cancella meglio.
    Se una persona parla sopra (doppio parlato) lo sfondo si rovina, ma l'uscita resta buona e lo sfondo torna alla
    copia buona. Lunghezza: P blocchi da B campioni (14 x 256 = 224 ms a 16 kHz)."""

    def __init__(self, B=256, P=14, passo=1.0):
        self.B, self.P, self.passo = B, P, passo
        self.reset()

    def reset(self):
        B, P = self.B, self.P
        self.Wb = np.zeros((P, B + 1), complex)     # sfondo
        self.Wf = np.zeros((P, B + 1), complex)     # in uso
        self.X = np.zeros((P, B + 1), complex)      # spettri degli ultimi P blocchi di riferimento
        self.xbuf = np.zeros(2 * B)
        self.pot = np.zeros(B + 1)                  # potenza del riferimento per frequenza
        self.Sb = self.Sf = self.Sd = 0.0           # energie medie: errore sfondo, errore in uso, microfono
        self.meglio = 0

    def _stima(self, W):
        return np.fft.irfft((W * self.X).sum(0))[self.B:]

    def blocco(self, x, d):
        """x: riferimento, d: microfono (B campioni, float). Ritorna d senza l'eco stimata."""
        B = self.B
        self.xbuf = np.concatenate([self.xbuf[B:], x])
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = np.fft.rfft(self.xbuf)
        eb = d - self._stima(self.Wb)
        ef = d - self._stima(self.Wf)
        self.pot = 0.8 * self.pot + 0.2 * np.abs(self.X[0]) ** 2
        if np.mean(x ** 2) > 1e4:      # adattamento solo quando Jarvis parla (riferimento sopra rms 100)
            E = np.fft.rfft(np.concatenate([np.zeros(B), eb]))
            G = self.passo * E / (self.P * self.pot + 1e4 * 2 * B)
            g = np.fft.irfft(np.conj(self.X) * G, axis=1)
            g[:, B:] = 0                               # vincolo: risposta lunga B per blocco
            self.Wb += np.fft.rfft(g, axis=1)
            a = 0.85
            self.Sb = a * self.Sb + (1 - a) * float(np.sum(eb ** 2))
            self.Sf = a * self.Sf + (1 - a) * float(np.sum(ef ** 2))
            self.Sd = a * self.Sd + (1 - a) * float(np.sum(d ** 2))
            if self.Sb < 0.8 * self.Sf:
                self.meglio += 1
                if self.meglio >= 3:                   # lo sfondo cancella meglio da 3 blocchi: si usa quello
                    self.Wf = self.Wb.copy()
                    self.Sf = self.Sb
                    self.meglio = 0
                    ef = eb
            else:
                self.meglio = 0
            if self.Sb > 4 * self.Sf and self.Sb > 0.5 * self.Sd:   # sfondo rovinato dal doppio parlato
                self.Wb = self.Wf.copy()
                self.Sb = self.Sf
            if self.Sf > 2 * self.Sd:                  # il filtro in uso peggiora le cose: si riparte da zero
                self.Wf[:] = 0
                self.Wb[:] = 0
                self.Sf = self.Sb = self.Sd
        return ef


class CancellaEco:
    SATURO_FRAZIONE = 0.005   # oltre questa parte di campioni al massimo il blocco è saturo
    SATURO_ATTESA = 0.6       # secondi senza decisioni dopo un blocco saturo
    MARGINE = 0.06            # secondi di anticipo del riferimento sull'eco (il filtro copre il resto): la stima
                              # del ritardo può sbagliare di qualche decina di ms in più

    def __init__(self, log=print, file_ritardo: Path = None):
        self.log = log
        self.blocco = threading.Lock()
        self.file_ritardo = file_ritardo
        self.filtro = FiltroLineare()
        # riferimento: una linea del tempo continua (16 kHz) che parte da self.t0
        self.t0 = None
        self.rif = np.zeros(0, dtype=np.float32)
        self.ritardo, self.ritardo_noto = self._ritardo_salvato()
        self.stime = deque(maxlen=7)
        self.mic_storia = deque(maxlen=int(3.0 * FREQ / 1280) + 1)   # (t, blocco) degli ultimi ~3 s
        self.misure = deque(maxlen=60)   # (rms grezzo, rms pulito, Jarvis stava parlando) degli ultimi ~5 s
        self.ultimo_aggiornamento = 0.0
        self.attivo_fino = 0.0           # fino a quando c'è voce di Jarvis (+ coda dell'eco)
        self.instabile_fino = 0.0        # dopo un cambio di ritardo il filtro si sta riadattando
        self.saturo_fino = 0.0           # microfono saturo (casse troppo forti): la cancellazione non regge
        self.ora = time.monotonic        # stesso orologio di Uscita e del microfono; sostituibile nelle prove
        self.mic_t0 = None               # orologio del microfono contato dai campioni
        self.mic_campioni = 0
        self.avvisi = set()
        self.agganciata_a = -1e9         # ultima volta in cui la cancellazione toglieva almeno 10 dB
        self.epoca = 0                   # cresce quando il filtro riparte da zero (chi impara dall'uscita riparte)
        self.storia_eco = deque(maxlen=8)  # (microfono, eco stimata) degli ultimi ~640 ms
        self.perdita_fino = 0.0          # eco sfuggita al filtro (si è spostata): niente decisioni fino a qui
        self.perdite = 0                 # blocchi di fila con l'eco sfuggita
        self.foto, self.foto_picco, self.foto_quando = None, 0, -1e9   # filtro di quando funzionava bene

    def _ritardo_salvato(self):
        try:
            return float(json.loads(self.file_ritardo.read_text(encoding="utf-8"))["ritardo"]), True
        except Exception:
            return 0.55, False

    def _salva_ritardo(self):
        if self.file_ritardo is None:
            return
        try:
            self.file_ritardo.write_text(json.dumps({"ritardo": round(self.ritardo, 3)}), encoding="utf-8")
        except OSError:
            pass

    def _avviso(self, chiave, *testo):
        if chiave not in self.avvisi:
            self.avvisi.add(chiave)
            self.log(*testo)

    # ------------------------------------------------------------ riferimento (quello che esce dalle casse)
    def riferimento(self, pcm24k: np.ndarray, t_suono: float, anticipo: float = 0.0):
        """Chiamato da Uscita per ogni pezzo mandato alle casse, con l'orario teorico in cui comincia a suonare."""
        from scipy.signal import resample_poly
        campioni = resample_poly(pcm24k.astype(np.float32), 2, 3).astype(np.float32)
        with self.blocco:
            ora = t_suono + anticipo
            if self.t0 is None:
                self.t0 = ora
            inizio = int(round((ora - self.t0) * FREQ))
            if inizio < 0:
                return
            fine = inizio + len(campioni)
            if fine > len(self.rif):
                self.rif = np.concatenate([self.rif, np.zeros(fine - len(self.rif), dtype=np.float32)])
            self.rif[inizio:fine] = campioni
            self.attivo_fino = max(self.attivo_fino, self.t0 + fine / FREQ + MAX_RITARDO)
            taglio = len(self.rif) - int(MAX_RITARDO * 2 * FREQ)
            if taglio > FREQ:
                self.rif = self.rif[taglio:]
                self.t0 += taglio / FREQ

    def taci(self):
        """Jarvis interrotto: il tablet butta quello che aveva ricevuto e non ancora suonato (fino a ANTICIPO). Si
        toglie anche dal riferimento, se no il filtro sottrarrebbe un'eco che non c'è, proprio sull'inizio della
        frase di chi l'ha interrotto."""
        with self.blocco:
            if self.t0 is None:
                return
            i = max(0, int(round((self.ora() - self.t0) * FREQ)))
            self.rif[i:] = 0
            self.attivo_fino = min(self.attivo_fino, self.ora() + MAX_RITARDO)

    def _rif_per(self, t_inizio, n):
        """n campioni di riferimento a partire dall'istante t_inizio (zeri dove non c'è)."""
        if self.t0 is None:
            return np.zeros(n, dtype=np.float32)
        i = int(round((t_inizio - self.t0) * FREQ))
        out = np.zeros(n, dtype=np.float32)
        a, b = max(i, 0), min(i + n, len(self.rif))
        if b > a:
            out[a - i:b - i] = self.rif[a:b]
        return out

    # ------------------------------------------------------------ microfono
    def pulisci(self, blocco: np.ndarray, t_arrivo: float) -> np.ndarray:
        """Blocco del microfono (int16, 16 kHz) -> stesso blocco senza la voce di Jarvis."""
        n = len(blocco)
        if n % self.filtro.B:
            self._avviso("misura", f"eco: blocchi da {n} campioni non previsti, cancellazione saltata")
            return blocco
        with self.blocco:
            t_atteso = None if self.mic_t0 is None else self.mic_t0 + (self.mic_campioni + n) / FREQ
            if t_atteso is None or abs(t_arrivo - t_atteso) > 0.5:
                # primo blocco o flusso ripartito (pausa, ricollegamento): si riaggancia l'orologio
                self.mic_t0, self.mic_campioni = t_arrivo - n / FREQ, 0
            self.mic_campioni += n
            t = self.mic_t0 + self.mic_campioni / FREQ
            d = blocco.astype(np.float64)
            self.mic_storia.append((t, d.astype(np.float32)))
            if self.ora() < self.attivo_fino and self.ora() - self.ultimo_aggiornamento > 1.0:
                self._stima_ritardo()
            # riferimento un po' prima dell'eco (margine): l'uscita deve precedere il microfono, mai il contrario
            rif = self._rif_per(t - n / FREQ - self.ritardo + self.MARGINE, n).astype(np.float64)
            if not rif.any() and not self.filtro.X.any():
                uscita = d                               # Jarvis zitto da un po': niente da togliere
            else:
                B = self.filtro.B
                uscita = np.concatenate([self.filtro.blocco(rif[k:k + B], d[k:k + B]) for k in range(0, n, B)])
            # misure per sapere quanto sta togliendo (solo dove Jarvis stava parlando)
            parla = float(np.sqrt(np.mean(rif ** 2))) > 300
            grezzo = float(np.sqrt(np.mean(d ** 2)))
            pulito = float(np.sqrt(np.mean(uscita ** 2)))
            self.misure.append((grezzo, pulito, parla))
            stimata = d - uscita
            self.storia_eco.append((d, stimata))
            if parla:
                self._controlla_allineamento(t, d, uscita, stimata)
            # saturo per colpa dell'eco (non di chi parla forte sopra): l'eco stimata stessa è vicina al massimo
            if np.mean(np.abs(blocco.astype(np.int32)) >= 32000) > self.SATURO_FRAZIONE and                     float(np.max(np.abs(stimata))) > 20000:
                self.saturo_fino = self.ora() + self.SATURO_ATTESA
                self._avviso("saturo", "eco: le casse saturano il microfono (volume troppo alto?): finché succede "
                                       "l'interruzione a voce non è affidabile")
            return np.clip(uscita, -32768, 32767).astype(np.int16)

    # ------------------------------------------------------------ allineamento fine
    def _controlla_allineamento(self, t, d, uscita, stimata):
        """Il ritardo casse -> microfono può spostarsi di qualche ms o decina di ms (il tablet riparte da buffer
        vuoto, la rete): il filtro allora non toglie più l'eco e nell'uscita resta qualcosa di molto simile all'eco
        stimata. La voce di chi parla sopra invece con l'eco stimata non c'entra (correlazione ~0,1 a filtro
        agganciato). Se succede, il blocco non vale per decidere un'interruzione e, al secondo blocco di fila, si
        misura dove è finito il percorso diretto casse -> microfono e si riallinea al campione."""
        e2, y2 = float(np.dot(uscita, uscita)), float(np.dot(stimata, stimata))
        if y2 < (300.0 ** 2) * len(stimata) or e2 <= 0 or not self.filtro.Wf.any():
            return                       # pausa della voce di Jarvis: niente da confrontare (il conto resta)
        n = len(uscita)
        L = 1 << (2 * n - 1).bit_length()
        r = np.fft.irfft(np.fft.rfft(uscita, L) * np.conj(np.fft.rfft(stimata, L)), L)
        massimo = float(np.max(np.abs(np.concatenate([r[:n // 2], r[-(n // 2):]]))))
        rho = massimo / (np.sqrt(e2 * y2) + 1e-9)
        # quanto ha tolto in questo blocco: con casse che distorcono il piccolo residuo somiglia sempre un po'
        # all'eco (provato: correlazione 0,2-0,4 con 28 dB tolti), quindi conta solo un residuo anche forte
        tolti = 10 * np.log10((float(np.dot(d, d)) + 1) / (e2 + 1))
        if rho < 0.2 or tolti >= 12:     # blocco pulito: l'eco è a posto
            self.perdite = 0
            db = self.eco_tolta_db()
            if db is not None and db >= 15 and self.ora() - self.foto_quando > 0.5:
                self._fotografa()
            return
        self.perdite += 1
        self.perdita_fino = self.ora() + 0.6
        if self.perdite >= 2 and self.foto is not None:
            self.perdite = 0
            self._riallinea(t)

    def _risposta(self, W):
        """Coefficienti del filtro -> risposta nel tempo (P*B campioni)."""
        return np.concatenate([np.fft.irfft(W[k])[:self.filtro.B] for k in range(self.filtro.P)])

    def _fotografa(self):
        """Filtro che funziona bene: se ne tiene una copia e la posizione del percorso diretto (il picco)."""
        self.foto = self.filtro.Wf.copy()
        self.foto_picco = int(np.argmax(np.abs(self._risposta(self.foto))))
        self.foto_quando = self.ora()

    def _riallinea(self, t):
        """Dove è adesso il percorso diretto: correlazione (PHAT) tra il microfono degli ultimi blocchi e il
        riferimento allineato come lo vede il filtro. Non si usa l'eco stimata: il filtro si adatta in ~50 ms e la
        insegue, così dopo un attimo non mostra più lo spostamento."""
        f = self.filtro
        lung = f.P * f.B
        blocchi = list(self.mic_storia)[-3:]
        d = np.concatenate([b for _t, b in blocchi]).astype(np.float64)
        inizio = blocchi[-1][0] - len(d) / FREQ                       # istante del primo campione di d
        x = self._rif_per(inizio - self.ritardo + self.MARGINE - lung / FREQ, len(d) + lung).astype(np.float64)
        N = 1 << (len(x) + len(d)).bit_length()
        X = np.fft.rfft(d, N) * np.conj(np.fft.rfft(x, N))
        r = np.fft.irfft(X / (np.abs(X) + 1e-6), N)
        # r[k] = somma d[m+k] x[m] e d[m] ~ x[m + lung - p]: il percorso con ritardo p (0..lung) sta a k = p - lung
        cand = np.concatenate([r[N - lung:], r[:1]])                  # cand[p] = r[(p - lung) mod N]
        p = int(np.argmax(cand))
        lontano = np.delete(cand, np.arange(max(0, p - 40), min(len(cand), p + 41)))
        chiaro = cand[p] >= 10 * float(np.median(np.abs(cand))) and cand[p] >= 1.5 * float(np.max(lontano))
        sposta = p - self.foto_picco                                  # >0: l'eco arriva più tardi
        if not chiaro or abs(sposta) < 8:
            return                       # nessuno spostamento, o non si capisce: ci pensa l'adattamento
        # prova sui dati: con il filtro buono, il nuovo allineamento deve togliere molto più eco del vecchio
        from scipy.signal import fftconvolve
        h = self._risposta(self.foto)

        def resto(ritardo):
            xx = self._rif_per(inizio - ritardo + self.MARGINE - lung / FREQ, len(d) + lung).astype(np.float64)
            return float(np.sum((d - fftconvolve(xx, h)[lung:lung + len(d)]) ** 2))
        prima, dopo = resto(self.ritardo), resto(self.ritardo + sposta / FREQ)
        if dopo > 0.5 * prima:
            return
        self.log(f"eco: allineamento corretto di {sposta / FREQ * 1000:+.1f} ms")
        self.ritardo += sposta / FREQ
        f.Wf = self.foto.copy()          # i coefficienti di quando andava bene, ora di nuovo allineati
        f.Wb = self.foto.copy()
        f.Sb = f.Sf = 0.0
        self._ricostruisci_storia(t)
        self._salva_ritardo()

    def _ricostruisci_storia(self, t):
        """Dopo un cambio di ritardo, il filtro ritrova la storia del riferimento con il nuovo allineamento:
        i coefficienti imparati restano validi (la stanza è la stessa, è cambiato solo quando arriva)."""
        f = self.filtro
        B = f.B
        fine = t - self.ritardo + self.MARGINE           # istante dell'ultimo campione di riferimento già usato
        for k in range(f.P):
            seg = self._rif_per(fine - (k * B + 2 * B) / FREQ, 2 * B).astype(np.float64)
            f.X[k] = np.fft.rfft(seg)
            if k == 0:
                f.xbuf = seg
        f.pot = np.abs(f.X[0]) ** 2

    # ------------------------------------------------------------ qualità
    def eco_tolta_db(self):
        """Quanta eco sta togliendo: mediana per blocco sui blocchi in cui Jarvis parlava (ultimi ~5 s); None se
        non si sa. La mediana, perché i blocchi in cui qualcuno gli parla sopra (pulito alto) non la spostino."""
        rapporti = [20 * np.log10((g + 1) / (p + 1)) for g, p, parla in self.misure if parla and g > 200]
        return float(np.median(rapporti)) if len(rapporti) >= 8 else None

    def affidabile(self):
        """La cancellazione è agganciata: si può decidere un'interruzione dalla voce sopra quella di Jarvis.
        Basta che lo fosse fino a 1,5 s fa: chi parla sopra abbassa la misura proprio quando si deve decidere."""
        if not (self.ritardo_noto or len(self.stime) >= 2):
            return False
        ora = self.ora()
        if ora < self.instabile_fino or ora < self.saturo_fino or ora < self.perdita_fino:
            return False
        db = self.eco_tolta_db()
        if db is not None and db >= 10:
            self.agganciata_a = ora
            return True
        return ora - self.agganciata_a <= 1.5

    def stato(self):
        db = self.eco_tolta_db()
        return {"ritardo_ms": round(self.ritardo * 1000), "eco_tolta_db": None if db is None else round(db, 1),
                "affidabile": self.affidabile()}

    def _stima_ritardo(self):
        """Ritardo grossolano (primo aggancio, o cancellazione che fallisce per secondi): correlazione tra il
        microfono degli ultimi ~3 s e il riferimento, per ritardi 0,1-1,6 s."""
        self.ultimo_aggiornamento = self.ora()
        db = self.eco_tolta_db()
        if db is not None and db >= 10:
            # il filtro toglie già 10 dB o più: con un allineamento sbagliato non ci riuscirebbe, quindi è giusto
            if not self.ritardo_noto:
                self.ritardo_noto = True
                self._salva_ritardo()
            return
        if self.foto is not None and (db is None or db >= 6):
            # il filtro ha già funzionato con questo allineamento: i piccoli spostamenti li corregge
            # _riallinea al campione; la stima grossolana (che sbaglia di qualche decina di ms e fa ripartire
            # il filtro da zero) serve solo se la cancellazione fallisce per secondi (provato: a ogni risposta
            # nuova cambiava idea, 260 <-> 320 ms, e l'interruzione a voce restava spenta per secondi)
            return
        if len(self.mic_storia) < 20:
            return
        t_fine = self.mic_storia[-1][0]
        mic = np.concatenate([b for _t, b in self.mic_storia]).astype(np.float64)
        t_inizio = t_fine - len(mic) / FREQ
        # correlazione PHAT tra il microfono e quello che è uscito: picco stretto al ritardo del percorso diretto,
        # preciso al campione (gli inviluppi sbagliavano di decine di ms e cambiavano idea: provato 250 -> 310 -> 220)
        massimo = int(MAX_RITARDO * FREQ)
        x = self._rif_per(t_inizio - MAX_RITARDO, len(mic) + massimo).astype(np.float64)
        if float(np.sqrt(np.mean(x ** 2))) < 100:
            return
        N = 1 << (len(x) + len(mic)).bit_length()
        X = np.fft.rfft(mic, N) * np.conj(np.fft.rfft(x, N))
        r = np.fft.irfft(X / (np.abs(X) + 1e-6), N)
        cand = np.concatenate([r[N - massimo:], r[:1]])          # cand[k] = correlazione al ritardo k campioni
        cand[:int(0.1 * FREQ)] = 0                                # sotto 0,1 s non è un'eco del tablet
        k = int(np.argmax(cand))
        lontano = np.delete(cand, np.arange(max(0, k - 80), min(len(cand), k + 81)))
        miglior = float(cand[k]) / (float(np.median(np.abs(cand))) + 1e-12)
        if miglior < 10 or cand[k] < 1.5 * float(np.max(lontano)):
            return                                                # picco non chiaro: si riprova più avanti
        ritardo_migliore = k / FREQ
        r_attuale = int(round(self.ritardo * FREQ))
        vicino = cand[max(0, r_attuale - 16):r_attuale + 17]
        attuale = float(np.max(vicino)) / float(cand[k]) if len(vicino) else 0.0
        # il ritardo attuale spiega l'eco quasi quanto il migliore: si tiene (la voce ha un ritmo, e la
        # correlazione ha picchi quasi uguali a distanza di una sillaba; ripartire costa secondi di eco)
        if self.ritardo_noto or len(self.stime) >= 2:
            if attuale >= 0.8:           # il ritardo attuale spiega l'eco quasi quanto il migliore
                ritardo_migliore = self.ritardo
        self.stime.append(ritardo_migliore)
        ultime = list(self.stime)[-3:]
        nuovo = float(np.median(ultime))
        # si cambia solo se le ultime 3 stime concordano tra loro (entro 20 ms) e non con il ritardo attuale
        concordano = len(ultime) == 3 and max(ultime) - min(ultime) <= 0.02
        if (concordano or not (self.ritardo_noto or len(self.stime) >= 2)) and abs(nuovo - self.ritardo) > 0.03:
            # allineamento cambiato oltre quello che il filtro copre: si riparte e per 2 s non ci si fida
            self.log(f"eco: ritardo {self.ritardo * 1000:.0f} -> {nuovo * 1000:.0f} ms (picco {miglior:.0f} volte il fondo)")
            self.ritardo = nuovo
            self.filtro.reset()
            self.foto = None
            self.epoca += 1
            self.instabile_fino = self.ora() + 2.0
            self.agganciata_a = -1e9
            self.misure.clear()
            self._salva_ritardo()
