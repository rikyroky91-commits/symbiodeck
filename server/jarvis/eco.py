"""Cancellazione dell'eco sul PC: toglie dal microfono la voce di Jarvis che rientra dalle casse.

Serve per poter interrompere Jarvis parlandogli sopra: lo SHIELD Tablet non ha la cancellazione dell'eco di Android,
e le sue casse sono a pochi centimetri dal microfono. Jarvis conosce esattamente il segnale che manda alle casse,
quindi lo può sottrarre, come fanno i vivavoce: qui con il modulo audio di WebRTC (AEC3, pacchetto `livekit`).

Il problema è il ritardo: tra l'invio al tablet e l'eco che torna dal microfono passano ~0,3-1 s, e cambia con la
rete. Il riferimento (la voce mandata) si conserva con l'ora di invio; il ritardo si stima confrontando l'inviluppo
del microfono con quello del riferimento mentre Jarvis parla, e il riferimento si passa a WebRTC già allineato
(al suo stimatore interno resta solo un piccolo scarto).
"""
import threading
import time
from collections import deque

import numpy as np

FREQ = 16000
TRAMA = 160            # 10 ms: la misura che vuole WebRTC
MAX_RITARDO = 1.6      # secondi di riferimento conservati


class CancellaEco:
    def __init__(self, log=print):
        from livekit import rtc
        self.rtc = rtc
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=False,
                                             high_pass_filter=True, auto_gain_control=False)
        self.log = log
        self.blocco = threading.Lock()
        # riferimento: una linea del tempo continua (16 kHz) che parte da self.t0
        self.t0 = None
        self.rif = np.zeros(0, dtype=np.float32)
        self.ritardo = 0.55          # stima iniziale, poi si aggiorna da sola
        self.stime = deque(maxlen=7)
        self.mic_storia = deque(maxlen=int(3.0 * FREQ / 1280) + 1)   # (t_arrivo, blocco) degli ultimi ~3 s
        self.ultimo_aggiornamento = 0.0
        self.attivo_fino = 0.0       # fino a quando c'è stata voce di Jarvis (+ coda dell'eco)
        self.ora = time.time         # sostituibile nelle prove (orologio virtuale)

    # ------------------------------------------------------------ riferimento (quello che esce dalle casse)
    def riferimento(self, pcm24k: np.ndarray, t_invio: float, anticipo: float):
        """Chiamato da Uscita per ogni pezzo mandato alle casse. `anticipo`: quanto prima del suo momento viene
        mandato (Uscita manda al ritmo del tempo reale con un po' di anticipo)."""
        from scipy.signal import resample_poly
        campioni = resample_poly(pcm24k.astype(np.float32), 2, 3).astype(np.float32)
        with self.blocco:
            ora = t_invio + anticipo   # quando questo pezzo comincia davvero a suonare (circa, lato PC)
            if self.t0 is None:
                self.t0 = ora
            inizio = int((ora - self.t0) * FREQ)
            if inizio < 0:
                return
            fine = inizio + len(campioni)
            if fine > len(self.rif):
                self.rif = np.concatenate([self.rif, np.zeros(fine - len(self.rif), dtype=np.float32)])
            self.rif[inizio:fine] = campioni
            self.attivo_fino = max(self.attivo_fino, self.t0 + fine / FREQ + 1.0)
            # si tiene solo il riferimento recente
            taglio = len(self.rif) - int(MAX_RITARDO * 2 * FREQ)
            if taglio > FREQ:
                self.rif = self.rif[taglio:]
                self.t0 += taglio / FREQ

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
        """Blocco del microfono (int16, 16 kHz, 80 ms) -> stesso blocco senza la voce di Jarvis."""
        with self.blocco:
            n = len(blocco)
            self.mic_storia.append((t_arrivo, blocco.astype(np.float32)))
            if self.ora() < self.attivo_fino and self.ora() - self.ultimo_aggiornamento > 1.0:
                self._stima_ritardo()
            rif = self._rif_per(t_arrivo - n / FREQ - self.ritardo, n)
            uscita = np.empty(n, dtype=np.int16)
            for k in range(0, n, TRAMA):
                r = np.clip(rif[k:k + TRAMA], -32768, 32767).astype(np.int16)
                m = blocco[k:k + TRAMA].astype(np.int16)
                fr = self.rtc.AudioFrame(r.tobytes(), FREQ, 1, TRAMA)
                fm = self.rtc.AudioFrame(m.tobytes(), FREQ, 1, TRAMA)
                self.apm.process_reverse_stream(fr)
                self.apm.process_stream(fm)
                uscita[k:k + TRAMA] = np.frombuffer(bytes(fm.data), dtype=np.int16)[:TRAMA]
            return uscita

    def _stima_ritardo(self):
        """Correlazione tra l'inviluppo del microfono (ultimi ~3 s) e quello del riferimento, per ritardi 0,1-1,5 s."""
        self.ultimo_aggiornamento = self.ora()
        if len(self.mic_storia) < 20:
            return
        t_fine = self.mic_storia[-1][0]
        mic = np.concatenate([b for _t, b in self.mic_storia])
        t_inizio = t_fine - len(mic) / FREQ
        passo = 160   # inviluppo a 10 ms
        env_m = np.sqrt(np.mean(mic[:len(mic) // passo * passo].reshape(-1, passo) ** 2, axis=1))
        miglior, ritardo_migliore = 0.0, None
        for r_ms in range(100, int(MAX_RITARDO * 1000), 10):
            rif = self._rif_per(t_inizio - r_ms / 1000, len(mic))
            env_r = np.sqrt(np.mean(rif[:len(rif) // passo * passo].reshape(-1, passo) ** 2, axis=1))
            if env_r.std() < 1e-3:
                continue
            c = float(np.corrcoef(env_m, env_r)[0, 1])
            if c > miglior:
                miglior, ritardo_migliore = c, r_ms / 1000
        if ritardo_migliore is not None and miglior > 0.5:
            self.stime.append(ritardo_migliore)
            nuovo = float(np.median(self.stime))
            if abs(nuovo - self.ritardo) > 0.03:
                self.log(f"eco: ritardo stimato {nuovo * 1000:.0f} ms (correlazione {miglior:.2f})")
            self.ritardo = nuovo
