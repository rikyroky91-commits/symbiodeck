"""Ingressi e uscite audio di Jarvis, dal PC o dal tablet, con lo stesso formato:
ingresso PCM 16 bit mono 16 kHz a blocchi di 80 ms (quelli che vuole openWakeWord), uscita PCM 16 bit mono 24 kHz.

Il tablet si collega con due richieste HTTP lunghe (vedi principale.py):
  GET  /jarvis/altoparlante  trame (trame.py) PC -> tablet: audio da riprodurre, comandi, "taci"
  POST /jarvis/microfono     PCM grezzo tablet -> PC, finché il microfono serve
"""
import json
import queue
import threading
import time

import numpy as np

from .trame import TRAMA_AUDIO, TRAMA_COMANDO, TRAMA_TACI, TRAMA_VIVO, trama

FREQ_IN, BLOCCO_IN = 16000, 1280
FREQ_OUT = 24000


def dispositivi():
    """Microfoni e casse del PC (solo MME: nomi leggibili, nessun doppione per ogni API)."""
    import sounddevice as sd
    ingressi, uscite = [], []
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] != 0 or "Sound Mapper" in d["name"]:
            continue
        if d["max_input_channels"] > 0:
            ingressi.append({"id": i, "nome": d["name"]})
        if d["max_output_channels"] > 0:
            uscite.append({"id": i, "nome": d["name"]})
    return {"ingressi": ingressi, "uscite": uscite}


def _indice(nome_o_id, ingresso):
    """Il dispositivo si salva per nome (gli indici cambiano quando si collega qualcosa)."""
    if nome_o_id in (None, "", "predefinito"):
        return None
    elenco = dispositivi()["ingressi" if ingresso else "uscite"]
    for d in elenco:
        if d["nome"] == nome_o_id or str(d["id"]) == str(nome_o_id):
            return d["id"]
    return None


class Blocchi:
    """Rimette insieme il PCM che arriva a pezzi qualsiasi in blocchi da BLOCCO_IN campioni."""

    def __init__(self, su_blocco):
        self.buf = bytearray()
        self.su_blocco = su_blocco

    def aggiungi(self, dati):
        self.buf += dati
        n = BLOCCO_IN * 2
        while len(self.buf) >= n:
            blocco = np.frombuffer(bytes(self.buf[:n]), dtype=np.int16)
            del self.buf[:n]
            self.su_blocco(blocco)


class MicPC:
    def __init__(self, su_blocco):
        self.su_blocco = su_blocco
        self.flusso = None
        self.nome = None

    def avvia(self, nome):
        import sounddevice as sd
        if self.flusso and self.nome == nome:
            return
        self.ferma()
        self.nome = nome

        def richiamo(dati, _n, _t, _stato):
            self.su_blocco(dati[:, 0].copy())
        self.flusso = sd.InputStream(samplerate=FREQ_IN, channels=1, dtype="int16", blocksize=BLOCCO_IN,
                                     device=_indice(nome, True), callback=richiamo)
        self.flusso.start()

    def ferma(self):
        f, self.flusso = self.flusso, None
        if f:
            try:
                f.stop()
                f.close()
            except Exception:
                pass


# ---------------------------------------------------------------- uscite

class CassePC:
    """Casse del PC: il richiamo di sounddevice pesca da un buffer; taci() lo svuota subito."""

    def __init__(self):
        self.buf = bytearray()
        self.blocco = threading.Lock()
        self.flusso = None
        self.nome = None

    def avvia(self, nome):
        import sounddevice as sd
        if self.flusso and self.nome == nome:
            return
        self.ferma()
        self.nome = nome

        def richiamo(uscita, n, _t, _stato):
            with self.blocco:
                dati = bytes(self.buf[:n * 2])
                del self.buf[:n * 2]
            uscita[:len(dati)] = dati
            uscita[len(dati):] = b"\x00" * (n * 2 - len(dati))
        self.flusso = sd.RawOutputStream(samplerate=FREQ_OUT, channels=1, dtype="int16", blocksize=1200,
                                         device=_indice(nome, False), callback=richiamo)
        self.flusso.start()

    def scrivi(self, pcm):
        with self.blocco:
            self.buf += pcm

    def taci(self):
        with self.blocco:
            self.buf.clear()

    def ferma(self):
        f, self.flusso = self.flusso, None
        if f:
            try:
                f.stop()
                f.close()
            except Exception:
                pass


class Tablet:
    """Collegamenti del tablet. Ogni GET /jarvis/altoparlante ha la sua coda di trame; il tablet riceve subito
    lo stato del microfono e ogni suo cambiamento."""

    def __init__(self, su_blocco):
        self.clienti = set()
        self.blocco = threading.Lock()
        self.microfono_voluto = False
        self.comandi = {}
        self.ingresso = Blocchi(su_blocco)
        self.ultimo_microfono = 0.0
        self.aec = False   # il tablet ha la cancellazione dell'eco attiva
        self.ultimo_altoparlante = 0.0

    # -- uscita
    def _a_tutti(self, dati):
        with self.blocco:
            for c in list(self.clienti):
                try:
                    c.put_nowait(dati)
                except queue.Full:
                    pass

    def scrivi(self, pcm):
        self._a_tutti(trama(TRAMA_AUDIO, pcm))

    def taci(self):
        self._a_tutti(trama(TRAMA_TACI))

    def comando(self, **valori):
        self.comandi.update(valori)
        self._a_tutti(trama(TRAMA_COMANDO, json.dumps(valori).encode("utf-8")))

    def collegato(self):
        return bool(self.clienti) or time.time() - self.ultimo_altoparlante < 3

    def servi_altoparlante(self, scrivi):
        """Gira nel thread della richiesta HTTP finché il tablet resta collegato."""
        coda = queue.Queue(maxsize=400)
        with self.blocco:
            self.clienti.add(coda)
        try:
            scrivi(trama(TRAMA_COMANDO, json.dumps(dict(self.comandi, microfono=self.microfono_voluto))
                         .encode("utf-8")))
            while True:
                try:
                    dati = coda.get(timeout=2)
                except queue.Empty:
                    dati = trama(TRAMA_VIVO)
                scrivi(dati)
                self.ultimo_altoparlante = time.time()
        finally:
            with self.blocco:
                self.clienti.discard(coda)

    def vuoi_microfono(self, si):
        if si != self.microfono_voluto:
            self.microfono_voluto = si
            self.comando(microfono=si)

    # -- ingresso
    def dal_microfono(self, dati):
        self.ultimo_microfono = time.time()
        self.ingresso.aggiungi(dati)

    def microfono_attivo(self):
        return time.time() - self.ultimo_microfono < 2


class Uscita:
    """Riproduce sulle casse scelte con il ritmo del tempo reale (al massimo ANTICIPO in coda): così fermare
    Jarvis a metà frase è immediato e si sa quando ha finito di parlare."""
    ANTICIPO = 0.35
    PEZZO = FREQ_OUT // 10  # 100 ms

    def __init__(self, casse_pc, tablet):
        self.casse_pc = casse_pc
        self.tablet = tablet
        self.dove = "tablet"
        self.volume = 1.0
        self.interrotta = threading.Event()
        self.parla = False
        self.livelli = []   # (ora, volume) dei pezzi mandati alle casse: serve a riconoscere l'eco

    def livello_uscita(self, finestra=1.0):
        """Il volume più alto di quello che è uscito dalle casse nell'ultimo secondo (copre il ritardo dell'eco)."""
        ora = time.monotonic()
        recenti = [v for t, v in self.livelli if ora - t <= finestra]
        return max(recenti) if recenti else 0.0

    def _destinazione(self):
        return self.tablet if self.dove == "tablet" else self.casse_pc

    def riproduci(self, pcm):
        """Blocca finché l'audio è stato riprodotto (circa). False se è stato interrotto."""
        if not pcm:
            return True
        campioni = np.frombuffer(pcm, dtype=np.int16)
        if self.volume != 1.0:
            campioni = np.clip(campioni.astype(np.float32) * self.volume, -32768, 32767).astype(np.int16)
        dest = self._destinazione()
        self.parla = True
        inizio, inviati = time.monotonic(), 0
        try:
            for i in range(0, len(campioni), self.PEZZO):
                if self.interrotta.is_set():
                    return False
                pezzo = campioni[i:i + self.PEZZO]
                dest.scrivi(pezzo.tobytes())
                self.livelli.append((time.monotonic(), float(np.sqrt(np.mean((pezzo.astype(np.float32) / 32768) ** 2)))))
                del self.livelli[:-30]
                inviati += len(pezzo)
                ritardo = inizio + inviati / FREQ_OUT - self.ANTICIPO - time.monotonic()
                if ritardo > 0:
                    if self.interrotta.wait(ritardo):
                        return False
            fine = inizio + inviati / FREQ_OUT - time.monotonic()
            return not (fine > 0 and self.interrotta.wait(fine))
        finally:
            self.parla = False

    def taci(self):
        self.interrotta.set()
        self.casse_pc.taci()
        self.tablet.taci()

    def riprendi(self):
        self.interrotta.clear()


# ---------------------------------------------------------------- suoni di Jarvis

def _tono(frequenze, durata, volume=0.25):
    t = np.linspace(0, durata, int(FREQ_OUT * durata), endpoint=False)
    f = np.linspace(frequenze[0], frequenze[1], len(t))
    onda = np.sin(2 * np.pi * np.cumsum(f) / FREQ_OUT) + 0.3 * np.sin(4 * np.pi * np.cumsum(f) / FREQ_OUT)
    inviluppo = np.minimum(1, np.minimum(t / 0.01, (durata - t) / 0.04).clip(0))
    return (onda * inviluppo * volume * 32767 / 1.3).astype(np.int16)


def suono(nome):
    pausa = np.zeros(int(FREQ_OUT * 0.03), dtype=np.int16)
    if nome == "attivo":      # due note che salgono
        s = np.concatenate([_tono((880, 990), 0.07), pausa, _tono((1320, 1480), 0.09)])
    elif nome == "capito":    # nota breve che scende
        s = _tono((1180, 900), 0.08, 0.18)
    elif nome == "fine":      # torna a riposo
        s = np.concatenate([_tono((990, 880), 0.06, 0.15), pausa, _tono((660, 600), 0.08, 0.15)])
    else:                     # errore
        s = np.concatenate([_tono((330, 300), 0.12), pausa, _tono((300, 260), 0.16)])
    return s.tobytes()
