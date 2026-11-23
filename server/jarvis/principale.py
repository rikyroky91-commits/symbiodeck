"""Jarvis: il cuore. Ascolta la parola chiave, capisce la richiesta, la passa a Claude Code e risponde a voce.

    cd server
    python -m jarvis                 avvio normale (lo fa pannello.py)
    python -m jarvis --scrivi "..."  manda una richiesta scritta e dice la risposta, poi esce (prova)

Stati: spento, riposo (aspetta "Ehi Jarvis"), ascolto, penso (trascrizione), lavoro (Claude Code),
parlo, conferma (aspetta sì/no), seguito (dopo una risposta ascolta ancora qualche secondo senza parola chiave).
"""
import ipaddress
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import audio, llm_locale
from .cervello import Cervello
from .emergenza import CervelloLocale, Emergenza, SintesiLocale
from .esecutore import trova_claude
from .trame import Frasi, Interruzione, si_o_no
from .voce_claude import MODELLI_STT, VOCI, Sintesi, Trascrittore

SERVER = Path(__file__).resolve().parent.parent
CONFIG_PANNELLO = SERVER / "config.json"
CONFIG = SERVER / "jarvis.json"
DATI = SERVER / "jarvis-dati"
REGISTRO = SERVER / "jarvis.log"
PORTA = 47802

PREDEFINITE = {
    "attivo": True,
    "modello_voce": "sonnet",      # chi parla: risponde subito (opus | sonnet | haiku)
    "effort_voce": "low",          # low | medium | high | xhigh | max
    "modello_compiti": "opus",     # l'esecutore dei compiti veri (sotto-agente): come Claude Code nell'app
    "effort_compiti": "high",
    "chrome": True,                # Claude in Chrome (--chrome): navigare e usare siti nel Chrome dell'utente
    "microfono": "tablet",         # tablet | pc
    "microfono_pc": "",
    "casse": "tablet",             # tablet | pc
    "casse_pc": "",
    "volume": 90,
    "voce": VOCI[0],
    "velocita": 5,
    "tono": -2,
    "sensibilita": 0.5,            # soglia di openWakeWord: più bassa = si attiva più facilmente
    "seguito": 8,                  # secondi in cui ascolta ancora dopo aver risposto
    "pausa": 1.4,                  # secondi di silenzio che chiudono una frase (più lunga: puoi pensare)
    "conferme": "irreversibili",   # irreversibili | sempre | mai
    "stt": "auto",
    "stt_cpu": "medium",           # modello del processore (gioco, modello locale): medium preciso, small veloce
    "accendi_schermo": False,
    "aec_pc": False,               # cancellazione dell'eco sul PC (WebRTC): in prova, si decide con le misure
    "interrompibile": True,        # mentre parla continua ad ascoltare: se gli parli sopra si ferma
    "eco": True,                   # cancellazione dell'eco sul tablet (modalità "chiamata" di Android)
    "emergenza": "auto",           # auto (senza Internet o fuori dai limiti) | sempre | mai
    "modello_emergenza": "27b",    # 27b (32K di contesto) | 27b-mtp (più veloce, 16K)
    "modalita_gioco": True,        # quando giochi: scheda video libera, niente modello locale
    "consegna_app": True,          # ogni conversazione compare subito nell'app Claude (progetto pc-panel)
    "cartella_lavoro": "",         # dove lavora Claude Code (vuoto: la cartella pc-panel)
    "aggiornamenti": True,         # mentre l'esecutore lavora in silenzio, ogni tanto dice a che punto è
    "aggiornamenti_ogni": 30,      # secondi di silenzio prima di un aggiornamento
    "claude_exe": "",
}
SCELTE = {
    "modello_voce": ["opus", "sonnet", "haiku"], "modello_compiti": ["opus", "sonnet", "haiku"],
    "effort_voce": ["low", "medium", "high", "xhigh", "max"], "effort_compiti": ["low", "medium", "high", "xhigh", "max"],
    "microfono": ["tablet", "pc"], "casse": ["tablet", "pc"], "conferme": ["irreversibili", "sempre", "mai"],
    "stt": MODELLI_STT, "stt_cpu": ["medium", "small"], "voce": VOCI, "emergenza": ["auto", "sempre", "mai"],
    "modello_emergenza": ["27b", "27b-mtp"],
}


def log(*parti):
    riga = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parti)
    try:
        with open(REGISTRO, "a", encoding="utf-8") as f:
            f.write(riga + "\n")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(riga, flush=True)
        except (OSError, ValueError):
            pass


def carica_impostazioni():
    imp = dict(PREDEFINITE)
    try:
        salvate = json.loads(CONFIG.read_text(encoding="utf-8"))
        # impostazioni di prima della divisione voce/compiti
        if "modello_claude" in salvate:
            salvate.setdefault("modello_compiti", salvate["modello_claude"])
        if "effort" in salvate:
            salvate.setdefault("effort_compiti", salvate["effort"])
        imp.update({k: v for k, v in salvate.items() if k in PREDEFINITE})  # chiavi vecchie ignorate
    except (OSError, ValueError):
        pass
    return imp


def salva_impostazioni(imp):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(imp, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG)


class Jarvis:
    def __init__(self):
        DATI.mkdir(exist_ok=True)
        self.imp = carica_impostazioni()
        if not CONFIG.exists():
            salva_impostazioni(self.imp)
        self.chiave_interna = secrets.token_urlsafe(18)
        self.stato = "riposo" if self.imp["attivo"] else "spento"
        self.eventi = []           # (id, dict) per il flusso SSE del pannello
        self.cond = threading.Condition()
        self.n_evento = 0
        self.blocchi = queue.Queue(maxsize=200)
        self.tablet = audio.Tablet(lambda b: self._blocco("tablet", b))
        self.mic_pc = audio.MicPC(lambda b: self._blocco("pc", b))
        self.casse_pc = audio.CassePC()
        self.uscita = audio.Uscita(self.casse_pc, self.tablet)
        self.stt = Trascrittore(self.imp, log)
        self.tts = Sintesi(self.imp, log)
        self.emergenza = Emergenza(self, log)
        self.tts_locale = SintesiLocale(log)
        self.claude = None
        self.blocco_cervello = threading.Lock()
        self.cambia_cervello(locale=False)
        self.frasi = Frasi()
        self.da_dire = queue.Queue()
        self.richieste = queue.Queue()
        self.conferma = None       # {"id", "descrizione", "esito": Event, "ok"}
        self.livello = 0.0
        self.parola = None
        self.risposta = ""
        self.ultima_azione = ""
        self.fine_turno = threading.Event()
        self.domanda = False       # l'ultima frase detta era una domanda
        self.richiesta = ""        # ultima richiesta dell'utente (per gli aggiornamenti)
        self.azioni = []           # (ora, descrizione) delle azioni del turno in corso
        self.fine_parlato = 0.0    # quando Jarvis ha smesso di parlare l'ultima volta
        self.detti = []            # aggiornamenti già detti in questo turno
        self.gen_voce = 0          # cresce a ogni interruzione: le frasi già pronte di prima si buttano
        self.scarta = False        # turno interrotto: il resto della risposta di Claude non si dice
        self.pronte = queue.Queue(maxsize=2)
        self.ultima_attivita = time.time()  # ultima richiesta o parola chiave
        self.conversazione = False          # dopo "Hey Jarvis": ascolta sempre finché la conversazione è viva
        self.in_gioco = False               # modalità gioco (ciclo_gpu)
        self.inizio_turno = 0.0             # quando è partita l'ultima richiesta a Claude
        self.solo_remoto = False            # richiesta da telefono/orologio: a casa non si parla
        self.utente_parla = False           # il microfono sta raccogliendo una frase (per unire le esitazioni)

    # ------------------------------------------------------------ eventi per il pannello
    def evento(self, **d):
        with self.cond:
            self.n_evento += 1
            self.eventi = (self.eventi + [(self.n_evento, dict(d, t=time.time()))])[-200:]
            self.cond.notify_all()

    def imposta_stato(self, stato):
        if stato != self.stato:
            self.stato = stato
            self.evento(stato=stato)
            self._aggiorna_microfono()

    def istantanea(self):
        return {"stato": self.stato, "impostazioni": self.impostazioni_pubbliche(),
                "tablet": {"altoparlante": self.tablet.collegato(), "microfono": self.tablet.microfono_attivo()},
                "conferma": self.conferma and {"id": self.conferma["id"], "descrizione": self.conferma["descrizione"]},
                "claude": bool(trova_claude(self.imp.get("claude_exe", ""))),
                "azione": self.ultima_azione, "sottofondo": self.claude.lavora_in_sottofondo(),
                "emergenza": {"attiva": self.emergenza.attiva, "motivo": self.emergenza.motivo},
                "gioco": self.in_gioco}

    def impostazioni_pubbliche(self):
        imp = dict(self.imp)
        return imp

    # ------------------------------------------------------------ impostazioni
    def cambia(self, nuove):
        for k, v in nuove.items():
            if k not in PREDEFINITE:
                continue
            if k in SCELTE and v not in SCELTE[k]:
                raise ValueError(f"valore non valido per {k}: {v}")
            tipo = type(PREDEFINITE[k])
            self.imp[k] = tipo(v) if tipo is not bool else bool(v)
        self.imp["volume"] = max(0, min(150, int(self.imp["volume"])))
        self.imp["sensibilita"] = max(0.1, min(0.95, float(self.imp["sensibilita"])))
        self.imp["seguito"] = max(0, min(30, int(self.imp["seguito"])))
        self.imp["pausa"] = max(0.6, min(4.0, float(self.imp["pausa"])))
        salva_impostazioni(self.imp)
        self._applica()
        self.evento(impostazioni=self.impostazioni_pubbliche())
        if "stt" in nuove:
            threading.Thread(target=self._prepara_stt, daemon=True).start()

    def _applica(self):
        self.uscita.dove = self.imp["casse"]
        self.uscita.volume = self.imp["volume"] / 100
        self.tablet.comando(accendi_schermo=self.imp["accendi_schermo"], eco=self.imp["eco"])
        if not self.imp["attivo"]:
            self.imposta_stato("spento")
        elif self.stato == "spento":
            self.imposta_stato("riposo")
        self._dispositivi_pc()
        self._aec()
        self._aggiorna_microfono()

    def _aec(self):
        voluta = bool(self.imp.get("aec_pc"))
        if voluta and self.uscita.aec is None:
            try:
                from .eco import CancellaEco
                self.uscita.aec = CancellaEco(log)
                log("cancellazione dell'eco sul PC: attiva")
            except Exception as e:
                log("cancellazione dell'eco sul PC non disponibile:", repr(e))
        elif not voluta and self.uscita.aec is not None:
            self.uscita.aec = None
            log("cancellazione dell'eco sul PC: spenta")

    def _dispositivi_pc(self):
        """Apre o chiude microfono e casse del PC in un thread a parte: certi dispositivi virtuali (es. il
        microfono di Virtual Desktop) restano appesi per sempre all'apertura e bloccherebbero Jarvis."""
        def lavoro(cosa, apri, oggetto, nome):
            try:
                oggetto.avvia(nome) if apri else oggetto.ferma()
            except Exception as e:
                log(cosa, "del PC non disponibile:", repr(e))
                self.evento(errore=f"{cosa.capitalize()} del PC non disponibile")
        for cosa, apri, oggetto, nome in (
                ("microfono", self.imp["microfono"] == "pc" and self.imp["attivo"], self.mic_pc, self.imp["microfono_pc"]),
                ("casse", self.imp["casse"] == "pc", self.casse_pc, self.imp["casse_pc"])):
            t = threading.Thread(target=lavoro, args=(cosa, apri, oggetto, nome), daemon=True)
            t.start()
            t.join(4)
            if t.is_alive():
                log(cosa, "del PC: il dispositivo non risponde")
                self.evento(errore=f"{cosa.capitalize()} del PC: il dispositivo non risponde, scegline un altro")

    def _aggiorna_microfono(self):
        self.tablet.vuoi_microfono(self.imp["attivo"] and self.imp["microfono"] == "tablet")

    def _prepara_stt(self):
        try:
            self.stt.prepara()
            self.stt.precarica_cpu()
        except Exception as e:
            log("stt non disponibile:", repr(e))
            self.evento(errore="Riconoscimento vocale non disponibile")

    # ------------------------------------------------------------ audio in ingresso
    def _blocco(self, da, blocco):
        if da != self.imp["microfono"] or not self.imp["attivo"]:
            return
        if self.uscita.aec is not None:
            try:
                blocco = self.uscita.aec.pulisci(blocco, time.time())
            except Exception as e:
                log("cancellazione dell'eco non riuscita, la spengo:", repr(e))
                self.uscita.aec = None
        try:
            self.blocchi.put_nowait(blocco)
        except queue.Full:
            try:
                self.blocchi.get_nowait()  # in ritardo: si butta il più vecchio
            except queue.Empty:
                pass

    def ciclo_ascolto(self):
        """Thread unico che consuma il microfono: parola chiave sempre, frase intera quando serve."""
        from openwakeword.model import Model
        from openwakeword.vad import VAD
        try:
            import openwakeword.utils
            openwakeword.utils.download_models(model_names=["hey_jarvis"])
        except Exception as e:
            log("modelli openWakeWord non scaricati:", repr(e))
        self.parola = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        vad = VAD()
        frase, parlato, silenzio, inizio_ascolto, ultimo_livello = [], False, 0, 0.0, 0.0
        dopo_attivazione = 0.0
        sopra_voce, interruzione = [], Interruzione()
        while True:
            try:
                blocco = self.blocchi.get(timeout=1)
            except queue.Empty:
                continue
            ora = time.time()
            rms = float(np.sqrt(np.mean((blocco.astype(np.float32) / 32768) ** 2)))
            self.livello = max(rms * 6, self.livello * 0.6)
            if ora - ultimo_livello > 0.1:
                ultimo_livello = ora
                self.evento(livello=round(min(1.0, self.livello), 3))

            # parola chiave (anche mentre parla o lavora: "Ehi Jarvis" lo interrompe)
            punteggio = self.parola.predict(blocco).get("hey_jarvis", 0)
            # mentre parla la parola chiave è più severa, se la sua voce rientra dal microfono (senza cancellazione)
            soglia = self.imp["sensibilita"] + (0.25 if self.uscita.parla and self.imp["microfono"] ==
                                                self.imp["casse"] and not self.tablet.aec else 0)
            if punteggio >= soglia and ora - dopo_attivazione > 1.5 and self.stato != "spento":
                dopo_attivazione = ora
                self.parola.reset()
                log("parola chiave", round(float(punteggio), 2))
                self._attivato()
                frase, parlato, silenzio, inizio_ascolto = [], False, 0, ora
                vad.reset_states()
                continue

            # mentre Jarvis parla si continua ad ascoltare: una voce vera, più forte dell'eco, lo interrompe
            # (anche nelle pause tra una frase e l'altra: lo stato resta "parlo" finché la risposta non è finita)
            if self.stato == "parlo" and self.imp.get("interrompibile", True):
                prob = float(vad.predict(blocco, frame_size=640))
                sopra_voce.append(blocco)
                del sopra_voce[:-10]                    # 800 ms: l'inizio della frase non si perde
                if interruzione.passo(prob, rms, self.uscita.livello_uscita(), self._fattore_eco()):
                    log("interrotto mentre parlava", f"(voce {rms:.3f}, eco {interruzione.eco:.3f})")
                    self._interrompi_voce()
                    frase, parlato, silenzio, inizio_ascolto = list(sopra_voce), True, 0, ora
                    sopra_voce = []
                continue
            if interruzione.blocchi > 10:   # ha appena finito di parlare: quanto della sua voce è rientrato
                log(f"eco della voce: {interruzione.eco:.3f} (microfono/uscita; con cancellazione efficace < 0,15)")
            interruzione.azzera()
            ascolta = ("ascolto", "seguito", "conferma") + (("penso", "lavoro") if self.conversazione else ())
            if self.stato not in ascolta:
                self.utente_parla = False
                continue
            if self.uscita.parla:
                continue  # suoni brevi (attivazione, conferma): non si ascoltano
            if not inizio_ascolto:
                inizio_ascolto = ora
            voce = vad.predict(blocco, frame_size=640) > 0.5
            frase.append(blocco)
            if voce:
                parlato, silenzio = True, 0
                self.utente_parla = True
            elif parlato:
                silenzio += 1
            if not parlato:
                frase = frase[-4:]  # 320 ms prima dell'inizio, per non tagliare la prima sillaba
                if self.stato in ("penso", "lavoro") or self.claude.lavora_in_sottofondo():
                    inizio_ascolto = ora  # mentre lavora la conversazione non scade: puoi aggiungere quando vuoi
                    continue
                # dopo una domanda di Jarvis ("Mi conferma?") si aspetta di più la risposta
                seguito = max(self.imp["seguito"], 12) if self.domanda else self.imp["seguito"]
                attesa = {"ascolto": 6, "conferma": 10}.get(self.stato, seguito)
                if ora - inizio_ascolto > attesa:
                    if self.stato == "conferma":
                        self._risposta_conferma(None)
                    else:
                        self.conversazione = False
                        self.imposta_stato("riposo")
                        self._suona("fine")
                    inizio_ascolto = 0
                continue
            durata = len(frase) * audio.BLOCCO_IN / audio.FREQ_IN
            if silenzio >= round(self.imp["pausa"] / 0.08) or durata > 40:  # la frase è finita
                pcm = np.concatenate(frase)
                frase, parlato, silenzio, inizio_ascolto = [], False, 0, 0
                self.utente_parla = False
                vad.reset_states()
                if self.stato == "conferma":
                    threading.Thread(target=self._capisci_conferma, args=(pcm,), daemon=True).start()
                else:
                    if self.stato not in ("lavoro",):
                        self.imposta_stato("penso")
                    self._suona("capito")
                    self.richieste.put(("voce", pcm))

    def _fattore_eco(self):
        """Quanto più forte dell'eco deve essere una voce per interrompere. Con la cancellazione dell'eco del
        tablet (microfono e casse sullo stesso tablet) basta meno."""
        # simulato con voci vere (2026-11-23): 1,6 non scatta mai sulla sola eco, né con né senza la
        # cancellazione del tablet; con la cancellazione si ferma 0,4-0,8 s dopo che la persona parla
        return 1.6

    def _interrompi_voce(self):
        self.gen_voce += 1
        self.uscita.taci()
        self._svuota(self.da_dire)
        self._svuota(self.pronte)
        self.frasi = Frasi()
        if self.claude.occupato:          # stava ancora scrivendo la risposta: il resto non serve
            self.scarta = True
            self.claude.interrompi()
        self.uscita.riprendi()
        self.conversazione = True
        self.imposta_stato("ascolto")
        self.evento(interrotto=True)

    def _suona(self, nome):
        threading.Thread(target=self.uscita.riproduci, args=(audio.suono(nome),), daemon=True).start()

    def _attivato(self):
        if self.conferma:
            return
        if self.stato == "parlo" or self.claude.occupato:
            self.uscita.taci()   # interrotto: smette di parlare e ascolta
            self._svuota(self.da_dire)
        self.uscita.riprendi()
        self.conversazione = True
        self.solo_remoto = False
        self.imposta_stato("ascolto")
        self.tablet.comando(attivato=time.time())
        # solo un suono, niente "Dimmi" a voce: mentre parla non ascolta, e si perderebbe l'inizio
        self._suona("attivo")

    @staticmethod
    def _svuota(q):
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    # ------------------------------------------------------------ richieste (voce o testo)
    def ciclo_richieste(self):
        while True:
            tipo, dato = self.richieste.get()
            try:
                if tipo == "voce":
                    t0 = time.time()
                    testo = self.stt.trascrivi(dato)
                    log("capito", f"({time.time() - t0:.1f}s):", testo)
                    testo = self._unisci_seguito(testo)
                    if not testo:
                        if not self.claude.occupato and not self.claude.lavora_in_sottofondo():
                            self.imposta_stato("seguito" if self.conversazione else "riposo")
                        continue
                else:
                    testo = dato
                self.evento(utente=testo)
                if testo.lower().strip(" .!") in ("stop", "fermati", "basta", "annulla", "lascia stare"):
                    self.claude.interrompi()
                    self.claude.ferma_lavoro()
                    self.conversazione = False
                    self.imposta_stato("riposo")
                    self.di("Va bene, mi fermo.")
                    continue
                # hai continuato a parlare dopo una pausa mentre la voce aveva appena cominciato a rispondere:
                # quella risposta si butta e Claude riceve anche il pezzo nuovo (la richiesta di prima resta nella
                # conversazione, interrotta). Se lavora da più tempo, il pezzo nuovo si accoda e basta.
                if self.claude.occupato and time.time() - self.inizio_turno < 10:
                    log("continua a parlare: riparto con la frase intera")
                    self.gen_voce += 1
                    self.uscita.taci()
                    self._svuota(self.da_dire)
                    self._svuota(self.pronte)
                    self.uscita.riprendi()
                    self.scarta = True
                    self.claude.interrompi()
                    limite = time.time() + 6
                    while self.claude.occupato and time.time() < limite:
                        time.sleep(0.1)
                    self.scarta = False
                    testo = "(continuo quello che stavo dicendo) " + testo
                self.risposta, self.ultima_azione = "", ""
                self.fine_turno.clear()
                self.imposta_stato("lavoro")
                self.richiesta, self.azioni, self.detti = testo, [], []
                self.ultima_attivita = time.time()
                self.fine_parlato = time.time()
                self.inizio_turno = time.time()
                self.claude.chiedi(testo)
            except Exception as e:
                log("errore richiesta:", traceback.format_exc())
                self.imposta_stato("riposo")
                self.di("Scusa, qualcosa è andato storto: " + str(e)[:120])

    def _unisci_seguito(self, testo, attesa=8):
        """Esitazione a metà frase: se finita la trascrizione l'utente sta già parlando di nuovo, si aspetta il
        pezzo che sta dicendo e lo si unisce, così Claude riceve una richiesta sola (non mezza frase)."""
        fine = time.time() + attesa
        while time.time() < fine:
            try:
                tipo, dato = self.richieste.get_nowait()
            except queue.Empty:
                if not self.utente_parla:
                    return testo
                time.sleep(0.1)
                continue
            if tipo != "voce":
                self.richieste.put((tipo, dato))   # non è il seguito: torna in coda
                return testo
            seguito = self.stt.trascrivi(dato)
            log("seguito unito:", seguito)
            testo = (testo.rstrip(" .") + " " + seguito).strip() if seguito else testo
            fine = time.time() + attesa
        return testo

    def _claude_testo(self, pezzo):
        if self.scarta:
            return
        self.risposta += pezzo
        self.evento(jarvis_pezzo=pezzo)
        for f in self.frasi.aggiungi(pezzo):
            self.da_dire.put(f)

    def _claude_azione(self, descrizione):
        self.azioni = (self.azioni + [(time.time(), descrizione)])[-30:]
        self.ultima_azione = descrizione
        self.evento(azione=descrizione)
        # la frase in corso prima dello strumento si dice subito ("Do un'occhiata.")
        for f in self.frasi.fine():
            self.da_dire.put(f)

    def _claude_fine(self, risultato, errore):
        if self.scarta:                    # turno interrotto dall'utente: niente resto, niente errore
            self.scarta = False
            self.frasi = Frasi()
            self.evento(fine=True)
            return
        for f in self.frasi.fine():
            self.da_dire.put(f)
        if errore and self.emergenza.errore_claude(errore):
            self.evento(fine=True)
            self.da_dire.put(None)
            return
        if errore:
            log("claude:", errore)
            if "login" in errore.lower() or "logged in" in errore.lower():
                errore = ("Claude Code sul PC non ha ancora fatto l'accesso: esegui claude auth login "
                          "in un terminale.")
            self.da_dire.put(errore if len(errore) < 200 else "Claude Code ha dato un errore.")
        # il testo è già arrivato a pezzi (jarvis_pezzo): si manda solo la fine del turno
        self.evento(fine=True) if self.risposta.strip() else self.evento(jarvis=risultato or "Fatto.", fine=True)
        self.da_dire.put(None)  # segnale di fine turno

    # ------------------------------------------------------------ aggiornamenti durante il lavoro
    def ciclo_aggiornamenti(self):
        """Se l'esecutore lavora e Jarvis tace da un po', una frase su a che punto è (scritta da Haiku, veloce)."""
        while True:
            time.sleep(1)
            lavora = self.claude.occupato or self.claude.lavora_in_sottofondo()
            if not self.imp.get("aggiornamenti") or not lavora or self.stato not in ("lavoro", "riposo", "seguito"):
                continue
            if self.uscita.parla or not self.da_dire.empty() or self.conferma:
                continue
            ogni = max(15, int(self.imp.get("aggiornamenti_ogni", 30)))
            if time.time() - self.fine_parlato < ogni:
                continue
            frase = self._frase_aggiornamento()
            # nel frattempo può aver finito: allora l'aggiornamento non serve più
            ancora = self.claude.occupato or self.claude.lavora_in_sottofondo()
            if frase and ancora and self.stato in ("lavoro", "riposo", "seguito") and not self.uscita.parla:
                self.detti.append(frase)
                log("aggiornamento:", frase)
                self.evento(jarvis=frase, sottofondo=self.claude.lavora_in_sottofondo())
                self.da_dire.put(frase)
                self.da_dire.put(None)  # fine dell'aggiornamento: lo stato torna libero
            self.fine_parlato = time.time()

    def _frase_aggiornamento(self):
        recenti = ([d for t, d in self.claude.progressi if t > self.fine_parlato - 5][-3:]
                   + self.claude.ultime_azioni_esecutore(self.fine_parlato - 5))
        recenti = recenti or [d for t, d in self.azioni if t > self.fine_parlato - 5][-8:] \
            or [d for _t, d in self.azioni][-3:]
        minuti = (time.time() - (self.azioni[0][0] if self.azioni else self.fine_parlato)) / 60
        lavoro = getattr(self.claude, "richiesta_lavoro", "") or self.richiesta
        richiesta = (f"Il compito: {lavoro[:400]}\n"
                     f"Ci stai lavorando da {minuti:.0f} minuti. Le ultime cose che hai fatto: "
                     f"{'; '.join(recenti) or 'stai ancora ragionando, nessuna azione nuova'}.\n"
                     f"Aggiornamenti già detti: {' | '.join(self.detti[-3:]) or 'nessuno'}.")
        sistema = ("Sei Jarvis, l'assistente vocale del PC. Scrivi UNA frase parlata in italiano, in prima persona "
                   "(\"sto...\", \"ho trovato...\"), massimo 20 parole, dando del tu all'utente, tono naturale e un po' "
                   "ironico, che dica a che punto sei. Non ripetere gli aggiornamenti già detti, non dire che hai "
                   "quasi finito, niente nomi di comandi o file lunghi. Solo la frase.")
        # 1) il modello locale, se è già acceso (worker o emergenza): zero token Claude
        if llm_locale.attivo():
            frase = llm_locale.frase(sistema, richiesta)
            if frase and 3 < len(frase) < 220:
                return frase
        # 2) Haiku con il prompt minimo: ~900 token invece dei ~18.000 del prompt completo di Claude Code
        try:
            exe = trova_claude(self.imp.get("claude_exe", ""))
            r = subprocess.run([exe, "-p", richiesta, "--model", "haiku", "--effort", "low", "--tools", "",
                                "--system-prompt", sistema, "--strict-mcp-config", "--disable-slash-commands",
                                "--setting-sources", "", "--no-session-persistence"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                               cwd=str(DATI), creationflags=0x08000000)
            frase = r.stdout.strip().strip('"').splitlines()[0] if r.stdout.strip() else ""
            if 3 < len(frase) < 220:
                return frase
        except (OSError, subprocess.TimeoutExpired, IndexError) as e:
            log("aggiornamento non generato:", repr(e))
        ultima = recenti[-1][0].lower() + recenti[-1][1:] if recenti else ""
        return f"Ci sto ancora lavorando{': ' + ultima if ultima else ''}."

    def ciclo_emergenza(self):
        self.emergenza.ciclo()

    def ciclo_gpu(self):
        """Ogni 5 secondi: modalità gioco (entra dopo 2 rilevamenti di fila, esce dopo un minuto senza gioco) e
        scheda video: Whisper la lascia al gioco o al modello locale (worker, emergenza)."""
        si, no = 0, 0
        while True:
            time.sleep(5)
            gioco = self.imp.get("modalita_gioco", True) and rileva_gioco()
            si, no = (si + 1, 0) if gioco else (0, no + 1)
            if not self.in_gioco and si >= 2:
                self._cambia_gioco(True)
            elif self.in_gioco and no >= 12:
                self._cambia_gioco(False)
            self.stt.in_gioco = self.in_gioco
            self.stt.libera_gpu_se_serve()

    def _cambia_gioco(self, gioco):
        self.in_gioco = gioco
        log("modalità gioco:", "sì" if gioco else "no")
        self.evento(gioco=gioco)
        if gioco and self.emergenza.attiva and self.emergenza.server_nostro:
            # il modello locale occupa quasi tutta la scheda video: si spegne, la partita viene prima
            self.emergenza.valuta()
            self.emergenza.ferma_server()
        elif not gioco and self.emergenza.rimandata:
            self.emergenza.valuta()   # finita la partita: se serve ancora, ora si accende

    # ------------------------------------------------------------ conversazioni nell'app Claude
    def ciclo_consegna(self):
        """Ogni conversazione di Jarvis compare nell'app Claude sotto il progetto pc-panel, appena c'è stata la
        prima risposta e Jarvis è fermo: da lì la si segue e la si riprende. Mai con un gioco a schermo intero
        davanti (l'app potrebbe venire in primo piano): si aspetta che non ci sia più."""
        while True:
            time.sleep(5)
            c = self.claude
            if not self.imp.get("consegna_app") or not c.sessione or c.nell_app():
                continue
            if c.occupato or c.lavora_in_sottofondo() or self.uscita.parla or not self.da_dire.empty():
                continue
            if self.stato not in ("riposo", "seguito", "spento") or schermo_intero():
                continue
            try:
                sessione = c.mostra_nell_app()
                if sessione:
                    log("conversazione visibile nell'app Claude:", sessione)
                    self.evento(nell_app=sessione)
            except Exception as e:
                log("conversazione non mostrata nell'app:", repr(e))
                time.sleep(60)

    def cambia_cervello(self, locale):
        """Claude (voce leggera + esecutore) oppure il cervello locale della modalità emergenza."""
        with self.blocco_cervello:
            vecchio = self.claude
            if vecchio is not None:
                if getattr(vecchio, "locale", False) == locale:
                    return
                vecchio.ferma()
            classe = CervelloLocale if locale else Cervello
            nuovo = classe(DATI, self.imp, PORTA, self.chiave_interna, CONFIG, log)
            nuovo.ascoltatori(self._claude_testo, self._claude_azione, self._claude_fine)
            if not locale:
                nuovo.su_errore = self.emergenza.errore_claude
            self.claude = nuovo
        self.evento(cervello="locale" if locale else "claude")

    def di_subito(self, testo):
        """Una frase di servizio (es. cambio di cervello), detta anche se Jarvis era a riposo."""
        self.uscita.riprendi()
        self.evento(jarvis=testo)
        self.da_dire.put(testo)
        self.da_dire.put(None)

    def di(self, testo):
        self.evento(jarvis=testo)
        self.da_dire.put(testo)
        self.da_dire.put(None)

    def ciclo_voce(self):
        """Dice le frasi in ordine; prepara la sintesi della successiva mentre suona quella corrente."""
        pronte = self.pronte

        def sintetizza():
            while True:
                f = self.da_dire.get()
                gen = self.gen_voce
                if f is None:
                    pronte.put(None)
                    continue
                pcm = None
                if not self.emergenza.attiva:
                    try:
                        pcm = self.tts.pcm(f)
                    except Exception as e:
                        log("sintesi vocale online non riuscita, uso Piper:", repr(e)[:120])
                if pcm is None:
                    try:
                        pcm = self.tts_locale.pcm(f)
                    except Exception as e:
                        log("sintesi vocale locale non riuscita:", repr(e))
                        pcm = audio.suono("errore")
                if gen == self.gen_voce:  # nel frattempo è stato interrotto: la frase non si dice
                    pronte.put((f, pcm, gen))
        threading.Thread(target=sintetizza, daemon=True, name="sintesi").start()
        while True:
            voce = pronte.get()
            if voce is not None and voce[2] != self.gen_voce:
                continue
            if voce is None:
                if not self.claude.occupato and self.stato in ("parlo", "lavoro", "penso"):
                    self.imposta_stato("seguito" if self.imp["seguito"] else "riposo")
                    self.evento(sottofondo=self.claude.lavora_in_sottofondo())
                continue
            if self.uscita.interrotta.is_set():
                continue  # frase vecchia di prima dell'interruzione
            if self.solo_remoto:
                log("detto (solo al telefono/orologio):", voce[0])
                continue   # la risposta l'ha già letta il telefono o l'orologio
            if self.stato in ("lavoro", "penso", "riposo", "seguito"):
                self.imposta_stato("parlo")
            self.domanda = voce[0].rstrip().endswith("?")
            log("detto:", voce[0])
            self.uscita.riproduci(voce[1])
            self.fine_parlato = time.time()
            if self.claude.occupato and self.stato == "parlo":
                self.imposta_stato("lavoro")

    # ------------------------------------------------------------ conferme (dalla guardia)
    def chiedi_conferma(self, descrizione, attesa=45):
        c = {"id": secrets.token_hex(4), "descrizione": descrizione, "esito": threading.Event(), "ok": False}
        self.conferma = c
        self.evento(conferma={"id": c["id"], "descrizione": descrizione})
        self.uscita.riprendi()
        # a voce solo cosa (senza il comando: illeggibile), il dettaglio è sul pannello
        self.da_dire.put(f"Devo {descrizione.split(':')[0]}. Confermi?")
        # si ascolta appena finita la domanda
        threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
        c["esito"].wait(90 if self.solo_remoto else attesa)   # da telefono/orologio si risponde più lentamente
        self.conferma = None
        self.evento(conferma=None)
        log("conferma", descrizione, "->", c["ok"])
        return c["ok"]

    def _ascolta_conferma_dopo_domanda(self, c):
        time.sleep(0.3)
        while self.uscita.parla or not self.da_dire.empty():
            time.sleep(0.1)
        if self.conferma is c:
            self.imposta_stato("conferma")

    def _capisci_conferma(self, pcm):
        testo = self.stt.trascrivi(pcm)
        self.evento(utente=testo)
        self._risposta_conferma(si_o_no(testo) if testo else None, testo)

    def _risposta_conferma(self, ok, testo=""):
        c = self.conferma
        if not c:
            return
        if ok is None and testo and not c.get("richiesto"):
            c["richiesto"] = True
            self.da_dire.put("Non ho capito: sì o no?")
            threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
            return
        c["ok"] = bool(ok)
        self.imposta_stato("lavoro")
        self.da_dire.put("Procedo." if ok else "D'accordo, lascio stare.")
        c["esito"].set()

    def conferma_dal_pannello(self, id_, ok):
        if self.conferma and self.conferma["id"] == id_:
            self.conferma["ok"] = bool(ok)
            self.imposta_stato("lavoro")
            self.conferma["esito"].set()

    # ------------------------------------------------------------ comandi dal pannello
    def scrivi(self, testo):
        self.uscita.riprendi()
        self.richieste.put(("testo", testo))

    def novita(self, dopo):
        """Messaggi di Jarvis e dell'utente dopo l'evento `dopo` (testi completi, non i pezzi)."""
        with self.cond:
            eventi = [(n, e) for n, e in self.eventi if n > dopo]
            ultimo = self.n_evento
        messaggi, pezzi = [], []
        inizio_pezzi = None
        for n, e in eventi:
            if e.get("utente"):
                messaggi.append({"chi": "utente", "testo": e["utente"]})
            if e.get("jarvis_pezzo"):
                if not pezzi:
                    inizio_pezzi = n
                pezzi.append(e["jarvis_pezzo"])
            if e.get("jarvis"):
                messaggi.append({"chi": "jarvis", "testo": e["jarvis"]})
                pezzi, inizio_pezzi = [], None
            elif e.get("fine") and pezzi:
                messaggi.append({"chi": "jarvis", "testo": "".join(pezzi).strip()})
                pezzi, inizio_pezzi = [], None
            if e.get("azione"):
                messaggi.append({"chi": "azione", "testo": e["azione"]})
        if pezzi and inizio_pezzi is not None:
            # una risposta ancora a metà: non si consuma, la prossima richiesta la riceve intera
            ultimo = inizio_pezzi - 1
        c = self.conferma
        return {"ultimo": ultimo, "messaggi": messaggi, "stato": self.stato,
                "lavoro": self.claude.lavora_in_sottofondo(), "emergenza": self.emergenza.attiva,
                "conferma": c and {"id": c["id"], "descrizione": c["descrizione"]}}

    def chiedi_e_aspetta(self, testo, attesa=30, parla_a_casa=False):
        """Per Apple Watch, iPhone e Comandi Rapidi: manda la richiesta e aspetta la prima risposta della voce (di
        solito pochi secondi; un lavoro lungo continua e il suo esito si legge poi con /jarvis/eventi).
        - Se c'è una conferma in attesa e il testo è un sì o un no, è la risposta alla conferma.
        - Se durante l'attesa Jarvis chiede una conferma, la domanda torna subito come risposta.
        - Senza parla_a_casa, le risposte non si leggono dal tablet (sei fuori: a casa nessuno ascolta)."""
        self.solo_remoto = not parla_a_casa
        c = self.conferma
        if c:
            ok = si_o_no(testo)
            if ok is not None:
                self.evento(utente=testo)
                self.conferma_dal_pannello(c["id"], ok)
                return {"risposta": "Ok, procedo." if ok else "D'accordo, lascio stare.", "ultimo": self.n_evento,
                        "lavoro": self.claude.lavora_in_sottofondo(), "conferma": None}
        with self.cond:
            inizio = self.n_evento
        self.scrivi(testo)
        limite = time.time() + attesa
        pezzi, risposta = [], ""
        visto = inizio
        while time.time() < limite and not risposta:
            with self.cond:
                self.cond.wait(timeout=1)
                nuovi = [(n, e) for n, e in self.eventi if n > visto]
            for n, e in nuovi:
                visto = n
                if e.get("jarvis_pezzo"):
                    pezzi.append(e["jarvis_pezzo"])
                if e.get("jarvis"):
                    risposta = e["jarvis"]
                elif e.get("fine") and pezzi:
                    risposta = "".join(pezzi).strip()
                if e.get("conferma"):
                    risposta = f"Devo {e['conferma']['descrizione'].split(':')[0]}. Confermi?"
        c = self.conferma
        return {"risposta": risposta or "".join(pezzi).strip(), "ultimo": visto,
                "lavoro": self.claude.lavora_in_sottofondo(),
                "conferma": c and {"id": c["id"], "descrizione": c["descrizione"]}}

    def tocca(self):
        self._attivato()

    def nuova_conversazione(self):
        self.claude.nuova_conversazione()
        self.evento(nuova=True)

    def prova_voce(self):
        self.uscita.riprendi()
        self.di("Eccomi. Tutto acceso e funzionante, dimmi pure.")

    def avvia(self):
        self._applica()
        for nome in ("ciclo_ascolto", "ciclo_richieste", "ciclo_voce", "ciclo_aggiornamenti", "ciclo_consegna",
                     "ciclo_gpu", "ciclo_emergenza"):
            threading.Thread(target=self._protetto, args=(getattr(self, nome),), daemon=True, name=nome).start()
        threading.Thread(target=self._prepara_stt, daemon=True).start()

    def _protetto(self, f):
        while True:
            try:
                f()
            except Exception:
                log("errore in", f.__name__, traceback.format_exc())
                time.sleep(2)


# ---------------------------------------------------------------- server HTTP per il tablet

def rileva_gioco():
    """Si sta giocando? Con i dati che il pannello legge ogni secondo: FPS di RivaTuner (un gioco agganciato),
    oppure una finestra a schermo intero con la scheda video carica, oppure la scheda video molto carica."""
    import urllib.request
    tablet, _pc = token_pannello()
    try:
        r = urllib.request.Request("http://127.0.0.1:47800/api/stato", headers={"X-Token": tablet})
        with urllib.request.urlopen(r, timeout=3) as risposta:
            d = json.loads(risposta.read())
    except (OSError, ValueError):
        return schermo_intero()
    fps = (d.get("fps") or {}).get("ora")
    carico = (d.get("gpu") or {}).get("carico") or 0
    if fps and fps > 0:
        return True
    from .voce_claude import llm_locale_acceso
    if llm_locale_acceso():
        return False   # la scheda la sta usando il modello locale (worker, emergenza), non un gioco
    return (schermo_intero() and carico > 40) or carico > 70


def schermo_intero():
    """True se in primo piano c'è una finestra a schermo intero (un gioco, un video): non va disturbata."""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return False
    classe = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(h, classe, 64)
    if classe.value in ("Progman", "WorkerW", "Shell_TrayWnd") or u.IsZoomed(h):
        return False  # desktop, barra, finestre solo ingrandite (i loro bordi escono dallo schermo)
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    u.GetMonitorInfoW(u.MonitorFromWindow(h, 2), ctypes.byref(mi))
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


TAILSCALE = ipaddress.ip_network("100.64.0.0/10")   # rete privata di Tailscale: iPhone e Watch da fuori casa


def rete_locale(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a in TAILSCALE
    except ValueError:
        return False


def token_pannello():
    try:
        cfg = json.loads(CONFIG_PANNELLO.read_text(encoding="utf-8"))
        return cfg.get("token", ""), cfg.get("token_pc", "")
    except (OSError, ValueError):
        return "", ""


class Gestore(BaseHTTPRequestHandler):
    server_version = "Jarvis/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def j(self) -> Jarvis:
        return self.server.jarvis

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def manda(self, codice, corpo):
        dati = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(dati)

    def autorizzato(self, q):
        token = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        tablet, pc = token_pannello()
        if token and tablet and secrets.compare_digest(token, tablet):
            return True
        return bool(token and pc and secrets.compare_digest(token, pc)
                    and ipaddress.ip_address(self.client_address[0]).is_loopback)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _controlli(self):
        if not rete_locale(self.client_address[0]):
            self.manda(403, {"errore": "solo rete locale"})
            return None
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/jarvis/interno/"):
            if not ipaddress.ip_address(self.client_address[0]).is_loopback or not secrets.compare_digest(
                    self.headers.get("X-Interno", ""), self.j.chiave_interna):
                self.manda(403, {"errore": "riservato alla guardia"})
                return None
            return u, q
        if not self.autorizzato(q):
            self.manda(401, {"errore": "token mancante o sbagliato"})
            return None
        return u, q

    def do_GET(self):
        r = self._controlli()
        if not r:
            return
        u, q = r
        if u.path == "/jarvis/interno/lavoro":
            return self.manda(200, self.j.claude.stato_lavoro())
        if u.path == "/jarvis/stato":
            return self.manda(200, self.j.istantanea())
        if u.path == "/jarvis/flusso":
            return self.flusso()
        if u.path == "/jarvis/dispositivi":
            try:
                return self.manda(200, audio.dispositivi())
            except Exception as e:
                return self.manda(200, {"ingressi": [], "uscite": [], "errore": str(e)})
        if u.path == "/jarvis/altoparlante":
            return self.altoparlante()
        if u.path == "/jarvis/eventi":
            dopo = int((q.get("dopo") or ["0"])[0] or 0)
            return self.manda(200, self.j.novita(dopo))
        if u.path == "/jarvis/conversazioni":
            return self.manda(200, {"attuale": self.j.claude.sessione, "conversazioni": self.j.claude.conversazioni()})
        return self.manda(404, {"errore": "non trovato"})

    def do_POST(self):
        r = self._controlli()
        if not r:
            return
        u, q = r
        if u.path == "/jarvis/microfono":
            return self.microfono()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                raise ValueError("richiesta troppo grande")
            d = json.loads(self.rfile.read(n) or b"{}")
            j = self.j
            if u.path == "/jarvis/interno/ferma-lavoro":
                j.claude.ferma_lavoro()
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/interno/conferma":
                return self.manda(200, {"ok": j.chiedi_conferma(str(d.get("descrizione", ""))[:300])})
            if u.path == "/jarvis/impostazioni":
                j.cambia(d)
                return self.manda(200, j.istantanea())
            if u.path in ("/jarvis/scrivi", "/jarvis/tocca", "/jarvis/prova-voce"):
                j.solo_remoto = False
            if u.path == "/jarvis/scrivi":
                testo = str(d.get("testo", "")).strip()[:2000]
                if not testo:
                    raise ValueError("testo vuoto")
                j.scrivi(testo)
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/chiedi":
                testo = str(d.get("testo", "")).strip()[:2000]
                if not testo:
                    raise ValueError("testo vuoto")
                attesa = max(3, min(60, int(d.get("attesa", 30))))
                return self.manda(200, j.chiedi_e_aspetta(testo, attesa, bool(d.get("parla_a_casa", False))))
            if u.path == "/jarvis/tocca":
                j.tocca()
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/taci":
                j.uscita.taci()
                j.claude.interrompi()
                j.imposta_stato("riposo")
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/nuova":
                j.nuova_conversazione()
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/conferma":
                j.conferma_dal_pannello(d.get("id"), d.get("ok"))
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/apri-app":
                uscita = j.claude.apri_nell_app(d.get("id") or None)
                log("conversazione aperta nell'app Claude:", uscita)
                j.evento(nuova=True)
                return self.manda(200, {"ok": True, "messaggio": uscita})
            if u.path == "/jarvis/riavvia":
                # Jarvis da amministratore non si può fermare da fuori senza privilegi: si chiude da solo con un
                # codice di errore, e l'attività pianificata (o pannello.py) lo fa ripartire con il codice nuovo
                log("riavvio richiesto")
                j.claude.ferma()
                threading.Timer(0.5, lambda: os._exit(3)).start()
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/prova-voce":
                j.prova_voce()
                return self.manda(200, {"ok": True})
        except ValueError as e:
            return self.manda(400, {"errore": str(e)})
        except Exception as e:
            log("errore richiesta:", traceback.format_exc())
            return self.manda(500, {"errore": str(e)})
        return self.manda(404, {"errore": "non trovato"})

    def flusso(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        j = self.j
        try:
            self.wfile.write(b"data: " + json.dumps({"tutto": j.istantanea()}, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()
            ultimo = j.n_evento
            while True:
                with j.cond:
                    j.cond.wait_for(lambda: j.n_evento != ultimo, timeout=15)
                    nuovi = [e for n, e in j.eventi if n > ultimo]
                    ultimo = j.n_evento
                if not nuovi:
                    self.wfile.write(b": ping\n\n")
                for e in nuovi:
                    self.wfile.write(b"data: " + json.dumps(e, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass

    def altoparlante(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        log("tablet collegato (altoparlante):", self.client_address[0])

        def scrivi(dati):
            self.wfile.write(dati)
            self.wfile.flush()
        try:
            self.j.tablet.servi_altoparlante(scrivi)
        except (ConnectionError, OSError):
            pass
        log("tablet scollegato (altoparlante)")

    def microfono(self):
        """Corpo a pezzi (Transfer-Encoding: chunked) con PCM 16 kHz, finché il tablet lo manda."""
        self.close_connection = True
        q = parse_qs(urlparse(self.path).query)
        self.j.tablet.aec = (q.get("aec") or ["0"])[0] == "1"
        log("microfono del tablet collegato:", self.client_address[0], "- cancellazione eco:",
            "sì" if self.j.tablet.aec else "no")
        try:
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                while True:
                    riga = self.rfile.readline(64)
                    if not riga:
                        break
                    n = int(riga.split(b";")[0].strip() or b"0", 16)
                    if n == 0:
                        break
                    dati = self.rfile.read(n)
                    self.rfile.readline(4)
                    self.j.tablet.dal_microfono(dati)
            else:
                while True:
                    dati = self.rfile.read1(4096) if hasattr(self.rfile, "read1") else self.rfile.read(4096)
                    if not dati:
                        break
                    self.j.tablet.dal_microfono(dati)
        except (ConnectionError, OSError, ValueError):
            pass
        log("microfono del tablet scollegato")
        try:
            self.manda(200, {"ok": True})
        except OSError:
            pass


class ServerUnico(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def main():
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    main.mutex = k32.CreateMutexW(None, False, "Local\\PannelloPCJarvis")
    if ctypes.get_last_error() in (183, 5):  # già c'è (5: c'è, ma avviato come amministratore)
        log("jarvis già in esecuzione: esco")
        return
    j = Jarvis()
    srv = ServerUnico(("0.0.0.0", PORTA), Gestore)
    srv.jarvis = j
    j.avvia()
    log("jarvis in ascolto sulla porta", PORTA, "- voce", j.imp["modello_voce"], "- compiti", j.imp["modello_compiti"])
    if "--scrivi" in sys.argv:
        testo = sys.argv[sys.argv.index("--scrivi") + 1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.5)
        j.scrivi(testo)
        time.sleep(1)
        while j.stato in ("lavoro", "parlo", "penso") or j.claude.occupato or not j.da_dire.empty():
            time.sleep(0.3)
        time.sleep(1)
        return
    srv.serve_forever()
