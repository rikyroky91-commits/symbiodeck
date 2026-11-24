"""Modalità emergenza: senza Internet o fuori dai limiti d'uso di Claude, Jarvis continua con un cervello locale.

- Cervello: Qwen 3.8 27B su llama-server (C:\\Users\\<utente>\\llm, lo stesso del worker), sulla scheda video. Profilo
  predefinito "27b" (32K di contesto, ~40-46 token/s): stesso modello del profilo veloce "27b-mtp" (~93 token/s) ma con
  il doppio di memoria per la conversazione, che a un agente con strumenti serve più della velocità. Si cambia con
  l'impostazione `modello_emergenza`.
- Un solo Claude Code (`--bare`, puntato al server locale) fa da voce ed esecutore, con gli strumenti essenziali, la
  guardia (conferme) e lo schermo. Niente token Claude.
- Voce: Piper (it_IT-paola-medium, sul PC) al posto di edge-tts, che vuole Internet.
- Riconoscimento: Whisper passa da solo sulla CPU quando gira il modello locale (voce_claude.llm_locale_acceso).
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .esecutore import QUI, SENZA_FINESTRA, Esecutore
from .llm_locale import CARTELLA_LLM, attivo, contesto
from .memoria import componi

LIMITE = re.compile(r"usage limit|limit reached|rate.?limit|exceeded.*(quota|limit)|out of (extra )?usage|"
                    r"overloaded|credit balance|429|529", re.I)
CONNESSIONE = re.compile(r"connection|network|ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|getaddrinfo|"
                         r"socket hang up|fetch failed|unable to connect", re.I)
VOCE_PIPER = Path(__file__).resolve().parent.parent / "jarvis-dati" / "piper" / "it_IT-paola-medium.onnx"


def internet(attesa=3):
    for host in ("api.anthropic.com", "1.1.1.1"):
        try:
            with socket.create_connection((host, 443), timeout=attesa):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- voce locale

class SintesiLocale:
    """Piper sul PC: PCM 24 kHz come edge-tts."""

    def __init__(self, log):
        self.log = log
        self.voce = None
        self.blocco = threading.Lock()

    def disponibile(self):
        return VOCE_PIPER.exists()

    def pcm(self, testo):
        from scipy.signal import resample_poly
        with self.blocco:
            if self.voce is None:
                from piper import PiperVoice
                self.voce = PiperVoice.load(str(VOCE_PIPER))
            campioni = [c.audio_int16_array for c in self.voce.synthesize(testo)]
        if not campioni:
            return b""
        audio = np.concatenate(campioni).astype(np.float32)
        audio = resample_poly(audio, 160, 147)   # 22050 -> 24000 Hz
        return np.clip(audio, -32768, 32767).astype(np.int16).tobytes()


# ---------------------------------------------------------------- cervello locale

CONTESTO_PROFILI = {"27b": 32768, "27b-mtp": 16384}


def finestra_riassunto(imp, n_ctx=None):
    """Token oltre i quali Claude Code riassume la conversazione. Claude Code crede che il modello "worker" abbia
    1.000.000 di token di contesto (misurato 2026-11-24) e senza questo non riassumerebbe mai: la conversazione
    andrebbe a sbattere contro il limite di llama-server. Si tiene il 65% del contesto vero (da /props, altrimenti dal
    profilo): il riassunto scatta dopo il turno che supera la soglia, e un turno con uno screenshot pesa parecchio."""
    n = n_ctx or contesto() or CONTESTO_PROFILI.get(imp.get("modello_emergenza") or "27b", 32768)
    return max(8000, int(n * 0.65))


class EsecutoreLocale(Esecutore):
    """Claude Code --bare puntato a llama-server: voce ed esecutore insieme, senza account Anthropic."""

    def __init__(self, dati: Path, *a, **k):
        dati.mkdir(parents=True, exist_ok=True)
        super().__init__(dati, *a, **k)

    def _comando(self, exe):
        cmd = [exe, "--bare", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions", "--model", "worker",
               "--effort", "low", "--append-system-prompt-file",
               str(componi(QUI / "persona_locale.md", self.dati / "persona-locale-con-memoria.md")),
               "--settings", str(self._file_impostazioni()), "--mcp-config", str(self._file_mcp()),
               "--tools", "Read,Edit,Write,Glob,Grep,Bash,PowerShell,WebFetch"]
        if self.sessione:
            cmd += ["--resume", self.sessione]
        return cmd

    def _ambiente(self):
        env = dict(os.environ, ANTHROPIC_BASE_URL="http://127.0.0.1:8099", ANTHROPIC_API_KEY="jarvis-locale",
                   ANTHROPIC_MODEL="worker", ANTHROPIC_DEFAULT_HAIKU_MODEL="worker",
                   ANTHROPIC_DEFAULT_SONNET_MODEL="worker", ANTHROPIC_DEFAULT_OPUS_MODEL="worker",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1",
                   CLAUDE_CODE_MAX_OUTPUT_TOKENS="4096",
                   CLAUDE_CODE_AUTO_COMPACT_WINDOW=str(finestra_riassunto(self.imp)))
        if not self.imp.get("ragionamento_locale"):
            # Claude Code manda "thinking: adaptive" anche a llama-server: con 0 non lo chiede (misurato 2026-11-24)
            env["MAX_THINKING_TOKENS"] = "0"
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

    def _firma(self):
        return ("locale",)

    def mostra_nell_app(self):
        return None   # le conversazioni locali non vanno nell'app (l'app vuole l'account Claude)

    def nell_app(self, sessione=None):
        return True


class CervelloLocale:
    """Stessa interfaccia del Cervello normale (cervello.py), con un solo processo locale."""

    def __init__(self, dati: Path, imp, porta, chiave_interna, file_config, log=print):
        self.log = log
        self.esec = EsecutoreLocale(dati / "locale", imp, porta, chiave_interna, file_config)
        self._testo = self._azione = self._fine = lambda *a: None
        self.esec.ascoltatori(lambda p: self._testo(p), self._e_azione, lambda r, e: self._fine(r, e))
        self.azioni, self.richiesta_lavoro, self.inizio_lavoro = [], "", 0.0
        self.locale = True
        self.contesto = lambda: ""

    def ascoltatori(self, testo, azione, fine):
        self._testo, self._azione, self._fine = testo, azione, fine

    occupato = property(lambda self: self.esec.occupato)
    sessione = property(lambda self: self.esec.sessione)
    progressi = property(lambda self: self.esec.progressi)

    def chiedi(self, testo):
        from .cervello import adesso
        self.richiesta_lavoro, self.inizio_lavoro, self.azioni = testo, time.time(), []
        self.esec.chiedi(f"[{adesso()}]{self.contesto()} {testo}")

    def interrompi(self):
        self.esec.interrompi()

    def ferma_lavoro(self):
        self.esec.interrompi()

    def lavora_in_sottofondo(self):
        return False

    def ultime_azioni_esecutore(self, dopo=0.0, quante=8):
        return [d for t, d in self.azioni if t > dopo][-quante:]

    def stato_lavoro(self):
        return {"occupato": self.esec.occupato, "richiesta": self.richiesta_lavoro[:300],
                "minuti": round((time.time() - self.inizio_lavoro) / 60) if self.inizio_lavoro else 0,
                "azioni": [d for _t, d in self.azioni][-6:]}

    def nuova_conversazione(self):
        self.esec.nuova_conversazione()

    def ferma(self):
        self.esec.ferma()

    def conversazioni(self):
        return self.esec.conversazioni()

    def apri_nell_app(self, sessione=None):
        raise ValueError("in modalità emergenza le conversazioni restano locali")

    def mostra_nell_app(self):
        return None

    def nell_app(self, sessione=None):
        return True

    def _e_azione(self, descrizione):
        self.azioni = (self.azioni + [(time.time(), descrizione)])[-40:]
        self._azione(descrizione)


# ---------------------------------------------------------------- sorveglianza

class Emergenza:
    """Decide quando passare al cervello locale e quando tornare a Claude."""

    def __init__(self, jarvis, log):
        self.j = jarvis
        self.log = log
        self.attiva = False
        self.motivo = ""
        self.limite_fino = 0.0     # fino a quando si considera Claude fuori dai limiti
        self.offline = 0           # controlli di fila senza Internet
        self.server_nostro = False
        self.rimandata = False     # serviva, ma si gioca: si aspetta la fine della partita
        self.blocco = threading.Lock()

    def errore_claude(self, testo):
        """Chiamato con il testo di un errore di Claude: se parla di limiti si passa all'emergenza per 30 minuti; se
        parla di rete si controlla subito Internet (senza aspettare il giro di 20 s). True se ci pensa l'emergenza."""
        if testo and CONNESSIONE.search(testo) and self.j.imp.get("emergenza", "auto") == "auto":
            if not internet():
                self.offline = max(self.offline, 2)
                self.log("Claude irraggiungibile e Internet assente:", testo[:120])
                threading.Thread(target=self.valuta, daemon=True).start()
                return True
            return False
        if testo and LIMITE.search(testo) and self.j.imp.get("emergenza", "auto") == "auto":
            self.limite_fino = time.time() + 30 * 60
            self.log("Claude fuori dai limiti:", testo[:160])
            threading.Thread(target=self.valuta, daemon=True).start()
            return True
        return False

    def ciclo(self):
        while True:
            self.offline = 0 if internet() else self.offline + 1
            self.valuta()
            time.sleep(20)

    def valuta(self):
        scelta = self.j.imp.get("emergenza", "auto")
        privata = bool(self.j.imp.get("privata"))
        if privata:
            voluta, motivo = True, "modalità privata"
        elif scelta == "sempre":
            voluta, motivo = True, "attivata dalle impostazioni"
        elif scelta == "mai":
            voluta, motivo = False, ""
        elif self.offline >= 2:
            voluta, motivo = True, "Internet non c'è"
        elif time.time() < self.limite_fino:
            voluta, motivo = True, "Claude è fuori dai limiti d'uso"
        else:
            voluta, motivo = False, ""
        if voluta and getattr(self.j, "in_gioco", False) and (scelta != "sempre" or privata):
            # stai giocando: il modello locale (12 GB di scheda video) non si accende, e se c'è si spegne. In
            # modalità privata intanto Jarvis non risponde alle domande (principale._comando_locale): mai Claude
            if not self.rimandata:
                self.rimandata = True
                self.log("emergenza rimandata: si sta giocando")
                self.j.di_subito(f"{motivo.capitalize()}, ma stai giocando: il cervello locale lo accendo quando "
                                 "finisci, per non rallentarti.")
            voluta = False
        else:
            self.rimandata = False
        with self.blocco:
            if voluta == self.attiva:
                return
            self.attiva, self.motivo = voluta, motivo
        if voluta:
            self._entra(motivo)
        else:
            self._esci()

    def _entra(self, motivo):
        self.log("EMERGENZA:", motivo)
        self.j.evento(emergenza={"attiva": True, "motivo": motivo})
        if motivo == "modalità privata":
            self.j.di_subito("Accendo il cervello locale: un minuto e ci sono.")
        else:
            self.j.di_subito(f"{motivo.capitalize()}: passo al cervello locale. Sarò più lento e un po' meno "
                             "sveglio, ma ci sono.")
        try:
            self.avvia_server()
        except Exception as e:
            self.log("modello locale non avviato:", repr(e))
            self.j.di_subito("Non riesco ad accendere il cervello locale.")
            return
        self.j.cambia_cervello(locale=True)

    def _esci(self):
        self.log("fine emergenza: torno a Claude")
        self.j.cambia_cervello(locale=False)
        self.j.evento(emergenza={"attiva": False, "motivo": ""})
        if not self.rimandata and not self.j.imp.get("privata"):
            self.j.di_subito("Ok, Claude è di nuovo raggiungibile: torno al cervello normale.")
        if self.server_nostro:
            self.ferma_server()

    def avvia_server(self):
        if attivo():
            return
        script = CARTELLA_LLM / "start-server.ps1"
        if not script.exists():
            raise RuntimeError(f"manca {script}")
        modo = self.j.imp.get("modello_emergenza") or "27b"
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                            "-Mode", modo], capture_output=True, text=True, timeout=300,
                           creationflags=SENZA_FINESTRA)
        if not attivo(5):
            raise RuntimeError((r.stdout + r.stderr)[-300:])
        self.server_nostro = True
        self.log("modello locale pronto:", modo)

    def ferma_server(self):
        script = CARTELLA_LLM / "stop-server.ps1"
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, timeout=60, creationflags=SENZA_FINESTRA)
        self.server_nostro = False
        self.log("modello locale spento")
