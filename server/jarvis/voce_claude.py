"""Voce della modalità Claude: dal parlato al testo (faster-whisper, sul PC) e dal testo alla voce (edge-tts,
voci neurali italiane di Microsoft; serve Internet)."""
import asyncio
import os
import sys
import re
import threading
import time
from pathlib import Path

import numpy as np

from .audio import FREQ_OUT

VOCI = ["it-IT-DiegoNeural", "it-IT-GiuseppeMultilingualNeural", "it-IT-ElsaNeural", "it-IT-IsabellaNeural"]
MODELLI_STT = ["auto", "small", "medium", "large-v3-turbo", "large-v3"]


def _librerie_cuda():
    """Le DLL di CUDA installate con pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) non sono nel PATH di Windows."""
    for base in sys.path:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            for cartella in nvidia.glob("*/bin"):
                try:
                    os.add_dll_directory(str(cartella))
                    os.environ["PATH"] = str(cartella) + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


def llm_locale_acceso():
    """True se gira llama-server (worker o modalità emergenza): anche mentre carica il modello."""
    try:
        import psutil
        return any((pr.info.get("name") or "").lower() == "llama-server.exe" for pr in psutil.process_iter(["name"]))
    except Exception:
        return False


class Trascrittore:
    """Riconoscimento vocale con due modelli tenuti pronti:
    - sulla scheda video: large-v3-turbo (preciso, ~0,2 s a frase) quando la scheda è libera;
    - sul processore: medium int8, 4 thread (preciso, ~3,5 s a frase) quando la scheda serve al gioco o al modello
      locale (worker, emergenza). Misurato: lo small sul processore è più veloce (1,3 s) ma sbaglia frasi comuni
      ("Che ore sono" -> "Chiuri sono"); il medium no.
    Il modello del processore resta caricato in RAM (~1 GB, niente scheda video), così a inizio partita il passaggio
    è immediato; quello della scheda video si scarica appena serve e si ricarica a fine partita."""

    def __init__(self, imp, log):
        self.imp = imp
        self.log = log
        self.modelli = {}          # (nome, dispositivo) -> WhisperModel
        self.modello = None
        self.caricato = None
        self.dispositivo = None
        self.blocco = threading.Lock()
        self.in_gioco = False      # lo imposta Jarvis (modalità gioco)
        self.gpu_guasta = False    # la GPU non è andata una volta: non si riprova a ogni giro

    @staticmethod
    def _gpu_presente():
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    def serve_cpu(self):
        return self.in_gioco or llm_locale_acceso()

    def _scelta(self):
        scelto = self.imp.get("stt") or "auto"
        gpu = self._gpu_presente() and not self.gpu_guasta and not self.serve_cpu()
        if scelto == "auto":
            return ("large-v3-turbo", "cuda") if gpu else (self.imp.get("stt_cpu") or "medium", "cpu")
        return scelto, ("cuda" if gpu else "cpu")

    def _carica(self, nome, disp):
        chiave = (nome, disp)
        if chiave in self.modelli:
            return self.modelli[chiave]
        from faster_whisper import WhisperModel
        _librerie_cuda()
        t0 = time.time()
        if disp == "cuda":
            m = WhisperModel(nome, device="cuda", compute_type="float16")
            # prova vera (transcribe è pigro: senza list() non calcola niente): le DLL di CUDA ci sono?
            list(m.transcribe(np.zeros(16000, dtype=np.float32), language="it")[0])
        else:
            m = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=4)
        self.modelli[chiave] = m
        self.log("stt pronto", nome, disp, f"{time.time() - t0:.1f}s")
        return m

    def prepara(self):
        with self.blocco:
            nome, disp = self._scelta()
            if self.caricato == nome and self.dispositivo == disp and self.modello:
                return
            try:
                self.modello = self._carica(nome, disp)
            except Exception as e:
                if disp != "cuda":
                    raise
                self.gpu_guasta = True
                self.log("stt su GPU non disponibile, uso la CPU:", repr(e)[:200])
                nome, disp = self.imp.get("stt_cpu") or "medium", "cpu"
                self.modello = self._carica(nome, disp)
            self.caricato, self.dispositivo = nome, disp

    def precarica_cpu(self):
        """All'avvio, in sottofondo: il modello del processore pronto per la prima partita."""
        try:
            with self.blocco:
                self._carica(self.imp.get("stt_cpu") or "medium", "cpu")
        except Exception as e:
            self.log("stt: modello per la CPU non precaricato:", repr(e)[:160])

    def libera_gpu_se_serve(self):
        """Se la scheda video serve (gioco, modello locale) Whisper la lascia subito; quando torna libera ci rientra."""
        if self.serve_cpu():
            if self.dispositivo == "cuda" or any(d == "cuda" for _n, d in self.modelli):
                with self.blocco:
                    for chiave in [k for k in self.modelli if k[1] == "cuda"]:
                        del self.modelli[chiave]
                    if self.dispositivo == "cuda":
                        self.modello, self.caricato, self.dispositivo = None, None, None
                import gc
                gc.collect()
                self.log("stt: scheda video lasciata libera (gioco o modello locale)")
                self.prepara()
        elif self.dispositivo == "cpu" and not self.gpu_guasta and (self.imp.get("stt") or "auto") == "auto" \
                and self._gpu_presente():
            self.prepara()   # torna sulla scheda video

    def trascrivi(self, pcm16k):
        """pcm16k: array int16 a 16 kHz. Restituisce il testo (vuoto se non ha capito niente)."""
        self.prepara()
        audio = pcm16k.astype(np.float32) / 32768
        segmenti, _info = self.modello.transcribe(
            audio, language="it", beam_size=1 if self.dispositivo == "cpu" else 5, vad_filter=False,
            condition_on_previous_text=False, initial_prompt=SUGGERIMENTO)
        testo = " ".join(s.text.strip() for s in segmenti if not _vuoto(s)).strip()
        pulito = testo.lower().strip(" .,!?")
        if ALLUCINAZIONI.match(pulito) or (len(pulito) > 3 and pulito in SUGGERIMENTO.lower()):
            return ""
        return testo

    def dice_jarvis(self, pcm16k):
        """Seconda verifica della parola chiave: c'è davvero "Jarvis" negli ultimi secondi? Trascrizione senza
        suggerimento (quello normale contiene "Jarvis" e sul rumore Whisper potrebbe ripeterlo).
        Restituisce (sì/no, testo capito)."""
        self.prepara()
        audio = pcm16k.astype(np.float32) / 32768
        segmenti, _info = self.modello.transcribe(audio, language="it", beam_size=1, vad_filter=False,
                                                  condition_on_previous_text=False)
        testo = " ".join(s.text.strip() for s in segmenti if not _vuoto(s)).strip()
        return bool(NOME.search(testo)), testo


# "Jarvis" come lo scrive Whisper, detto all'inglese o all'italiana (provato con voce sintetica il 2026-11-24:
# "e giarvis", "e i GARVIS", "e i JARVIS", "Jarvis"; nessuna frase comune lo contiene). Con la voce vera al tablet
# (registro del 2026-11-24/26) Whisper ha scritto anche "Ehi Tarvis", "Ehi Cervis", "e carvis", "E carvissi",
# "E' già visto": la seconda forma accetta qualunque lettera prima di "-arvis"/"-ervis" (con la "r", così
# "Travis", "Davis", "avvisi", "servizio" restano fuori)
NOME = re.compile(r"\b(?:j|gi|g|dj|dg|ch|ci|y|i)[aàe]h?r?\s?[vb]i[sz]|[aàeè]h?r\s?[vb]is", re.I)


# suggerimento per Whisper (lingua, nome "Jarvis"). Sul rumore Whisper a volte lo ripete: non deve mai essere un
# comando né una risposta (provato il 2026-11-24: con "Sì, confermo." nel suggerimento, da un residuo d'eco è uscito
# "Sì, confermo.", che durante una richiesta di conferma avrebbe approvato un'azione irreversibile)
SUGGERIMENTO = "Conversazione in italiano con Jarvis, l'assistente vocale del computer."


# frasi che Whisper "allucina" sul silenzio o sul fruscio (imparate dai sottotitoli dei video): provato il
# 2026-11-24, un residuo d'eco è diventato "Grazie per la visione."
ALLUCINAZIONI = re.compile(
    r"^(|grazie( mille| a tutti)?( per (la |l')?(visione|attenzione|ascolto))?|buona visione|ciao a tutti"
    r"|alla prossima|iscriviti( al canale)?.*|sottotitoli.*|.*amara\.org.*|amen|\.+)$")


def _vuoto(segmento):
    """Segmento senza parlato: Whisper stesso lo dice probabile silenzio, o è molto incerto e probabile silenzio."""
    return segmento.no_speech_prob >= 0.8 or (segmento.no_speech_prob >= 0.5 and segmento.avg_logprob < -1.0)


class Sintesi:
    """edge-tts in un suo ciclo asyncio; restituisce PCM 24 kHz pronto per Uscita."""

    def __init__(self, imp, log):
        self.imp = imp
        self.log = log
        self.ciclo = asyncio.new_event_loop()
        threading.Thread(target=self.ciclo.run_forever, daemon=True, name="tts").start()

    async def _mp3(self, testo):
        import edge_tts
        velocita = int(self.imp.get("velocita", 5))
        tono = int(self.imp.get("tono", -2))
        c = edge_tts.Communicate(testo, self.imp.get("voce") or VOCI[0],
                                 rate=f"{velocita:+d}%", pitch=f"{tono:+d}Hz")
        dati = bytearray()
        async for pezzo in c.stream():
            if pezzo["type"] == "audio":
                dati += pezzo["data"]
        return bytes(dati)

    def pcm(self, testo, attesa=20):
        import miniaudio
        mp3 = asyncio.run_coroutine_threadsafe(self._mp3(testo), self.ciclo).result(attesa)
        if not mp3:
            return b""
        suono = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                 sample_rate=FREQ_OUT)
        return suono.samples.tobytes()
