"""Voce della modalità Claude: dal parlato al testo (faster-whisper, sul PC) e dal testo alla voce (edge-tts,
voci neurali italiane di Microsoft; serve Internet)."""
import asyncio
import os
import sys
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


class Trascrittore:
    """Carica il modello una volta sola (in un thread, all'avvio) e lo tiene in memoria."""

    def __init__(self, imp, log):
        self.imp = imp
        self.log = log
        self.modello = None
        self.caricato = None
        self.dispositivo = None
        self.blocco = threading.Lock()

    def _scelta(self):
        scelto = self.imp.get("stt") or "auto"
        try:
            import ctranslate2
            gpu = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            gpu = False
        if scelto == "auto":
            return ("large-v3-turbo", "cuda") if gpu else ("small", "cpu")
        return scelto, ("cuda" if gpu else "cpu")

    def prepara(self):
        with self.blocco:
            nome, disp = self._scelta()
            if self.caricato == nome and self.modello:
                return
            from faster_whisper import WhisperModel
            _librerie_cuda()
            t0 = time.time()
            if disp == "cuda":
                try:
                    m = WhisperModel(nome, device="cuda", compute_type="float16")
                    # prova vera (transcribe è pigro: senza list() non calcola niente): le DLL di CUDA ci sono?
                    list(m.transcribe(np.zeros(16000, dtype=np.float32), language="it")[0])
                    self.modello, self.caricato, self.dispositivo = m, nome, "cuda"
                    self.log("stt pronto", nome, "cuda", f"{time.time() - t0:.1f}s")
                    return
                except Exception as e:
                    self.log("stt su GPU non disponibile, uso la CPU:", repr(e)[:200])
                    if nome.startswith("large"):
                        nome = "small" if (self.imp.get("stt") or "auto") == "auto" else nome
            self.modello = WhisperModel(nome, device="cpu", compute_type="int8")
            self.caricato, self.dispositivo = nome, "cpu"
            self.log("stt pronto", nome, "cpu", f"{time.time() - t0:.1f}s")

    def trascrivi(self, pcm16k):
        """pcm16k: array int16 a 16 kHz. Restituisce il testo (vuoto se non ha capito niente)."""
        self.prepara()
        audio = pcm16k.astype(np.float32) / 32768
        segmenti, _info = self.modello.transcribe(
            audio, language="it", beam_size=1 if self.dispositivo == "cpu" else 5, vad_filter=False,
            condition_on_previous_text=False, initial_prompt="Jarvis, apri Spotify. Sì, confermo.")
        testo = " ".join(s.text.strip() for s in segmenti if s.no_speech_prob < 0.8).strip()
        # frasi che Whisper "allucina" sul silenzio
        if testo.lower().strip(" .!") in ("", "grazie", "grazie a tutti", "sottotitoli a cura di qtss",
                                          "sottotitoli creati dalla comunità amara.org", "amen"):
            return ""
        return testo


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
