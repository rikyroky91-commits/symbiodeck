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

from . import audio
from .esecutore import Esecutore, trova_claude
from .trame import Frasi, si_o_no
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
    "seguito": 6,                  # secondi in cui ascolta ancora dopo aver risposto
    "conferme": "irreversibili",   # irreversibili | sempre | mai
    "stt": "auto",
    "accendi_schermo": False,
    "aggiornamenti": True,         # mentre l'esecutore lavora in silenzio, ogni tanto dice a che punto è
    "aggiornamenti_ogni": 30,      # secondi di silenzio prima di un aggiornamento
    "claude_exe": "",
}
SCELTE = {
    "modello_voce": ["opus", "sonnet", "haiku"], "modello_compiti": ["opus", "sonnet", "haiku"],
    "effort_voce": ["low", "medium", "high", "xhigh", "max"], "effort_compiti": ["low", "medium", "high", "xhigh", "max"],
    "microfono": ["tablet", "pc"], "casse": ["tablet", "pc"], "conferme": ["irreversibili", "sempre", "mai"],
    "stt": MODELLI_STT, "voce": VOCI,
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
        self.claude = Esecutore(DATI, self.imp, PORTA, self.chiave_interna, CONFIG)
        self.claude.ascoltatori(self._claude_testo, self._claude_azione, self._claude_fine)
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
                "azione": self.ultima_azione, "sottofondo": self.claude.lavora_in_sottofondo()}

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
        salva_impostazioni(self.imp)
        self._applica()
        self.evento(impostazioni=self.impostazioni_pubbliche())
        if "stt" in nuove:
            threading.Thread(target=self._prepara_stt, daemon=True).start()

    def _applica(self):
        self.uscita.dove = self.imp["casse"]
        self.uscita.volume = self.imp["volume"] / 100
        self.tablet.comando(accendi_schermo=self.imp["accendi_schermo"])
        if not self.imp["attivo"]:
            self.imposta_stato("spento")
        elif self.stato == "spento":
            self.imposta_stato("riposo")
        self._dispositivi_pc()
        self._aggiorna_microfono()

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
        except Exception as e:
            log("stt non disponibile:", repr(e))
            self.evento(errore="Riconoscimento vocale non disponibile")

    # ------------------------------------------------------------ audio in ingresso
    def _blocco(self, da, blocco):
        if da != self.imp["microfono"] or not self.imp["attivo"]:
            return
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
            soglia = self.imp["sensibilita"] + (0.25 if self.uscita.parla and self.imp["microfono"] ==
                                                self.imp["casse"] else 0)
            if punteggio >= soglia and ora - dopo_attivazione > 1.5 and self.stato != "spento":
                dopo_attivazione = ora
                self.parola.reset()
                log("parola chiave", round(float(punteggio), 2))
                self._attivato()
                frase, parlato, silenzio, inizio_ascolto = [], False, 0, ora
                vad.reset_states()
                continue

            if self.stato not in ("ascolto", "seguito", "conferma"):
                continue
            if self.uscita.parla:
                continue  # mezzo-duplex: non si ascolta la propria voce
            if not inizio_ascolto:
                inizio_ascolto = ora
            voce = vad.predict(blocco, frame_size=640) > 0.5
            frase.append(blocco)
            if voce:
                parlato, silenzio = True, 0
            elif parlato:
                silenzio += 1
            if not parlato:
                frase = frase[-4:]  # 320 ms prima dell'inizio, per non tagliare la prima sillaba
                # dopo una domanda di Jarvis ("Mi conferma?") si aspetta di più la risposta
                seguito = max(self.imp["seguito"], 12) if self.domanda else self.imp["seguito"]
                attesa = {"ascolto": 6, "conferma": 10}.get(self.stato, seguito)
                if ora - inizio_ascolto > attesa:
                    if self.stato == "conferma":
                        self._risposta_conferma(None)
                    else:
                        self.imposta_stato("riposo")
                        self._suona("fine")
                    inizio_ascolto = 0
                continue
            durata = len(frase) * audio.BLOCCO_IN / audio.FREQ_IN
            if silenzio >= 10 or durata > 25:  # 0,8 s di silenzio dopo la voce
                pcm = np.concatenate(frase)
                frase, parlato, silenzio, inizio_ascolto = [], False, 0, 0
                vad.reset_states()
                if self.stato == "conferma":
                    threading.Thread(target=self._capisci_conferma, args=(pcm,), daemon=True).start()
                else:
                    self.imposta_stato("penso")
                    self._suona("capito")
                    self.richieste.put(("voce", pcm))

    def _suona(self, nome):
        threading.Thread(target=self.uscita.riproduci, args=(audio.suono(nome),), daemon=True).start()

    def _attivato(self):
        if self.conferma:
            return
        if self.stato == "parlo" or self.claude.occupato:
            self.uscita.taci()   # interrotto: smette di parlare e ascolta
            self._svuota(self.da_dire)
        self.uscita.riprendi()
        self.imposta_stato("ascolto")
        self.tablet.comando(attivato=time.time())
        # solo un suono, niente "Sì, signore?" a voce: mentre parla non ascolta, e si perderebbe l'inizio
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
                    if not testo:
                        self.imposta_stato("riposo")
                        self._suona("fine")
                        continue
                else:
                    testo = dato
                self.evento(utente=testo)
                if testo.lower().strip(" .!") in ("stop", "fermati", "basta", "annulla", "lascia stare"):
                    self.claude.interrompi()
                    self.imposta_stato("riposo")
                    self.di("Come desidera.")
                    continue
                self.risposta, self.ultima_azione = "", ""
                self.fine_turno.clear()
                self.imposta_stato("lavoro")
                self.richiesta, self.azioni, self.detti = testo, [], []
                self.fine_parlato = time.time()
                self.claude.chiedi(testo)
            except Exception as e:
                log("errore richiesta:", traceback.format_exc())
                self.imposta_stato("riposo")
                self.di("Mi scusi signore, qualcosa è andato storto: " + str(e)[:120])

    def _claude_testo(self, pezzo):
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
        for f in self.frasi.fine():
            self.da_dire.put(f)
        if errore:
            log("claude:", errore)
            if "login" in errore.lower() or "logged in" in errore.lower():
                errore = ("Signore, Claude Code sul PC non ha ancora fatto l'accesso. Esegua claude auth login "
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
            if not self.imp.get("aggiornamenti") or not lavora or self.stato not in ("lavoro", "riposo"):
                continue
            if self.uscita.parla or not self.da_dire.empty() or self.conferma:
                continue
            ogni = max(15, int(self.imp.get("aggiornamenti_ogni", 30)))
            if time.time() - self.fine_parlato < ogni:
                continue
            frase = self._frase_aggiornamento()
            # nel frattempo può aver finito: allora l'aggiornamento non serve più
            ancora = self.claude.occupato or self.claude.lavora_in_sottofondo()
            if frase and ancora and self.stato in ("lavoro", "riposo") and not self.uscita.parla:
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
        richiesta = (f"L'utente ha chiesto: {self.richiesta[:400]}\n"
                     f"Ci stai lavorando da {minuti:.0f} minuti. Le ultime cose che hai fatto: "
                     f"{'; '.join(recenti) or 'sta ancora ragionando, nessuna azione nuova'}.\n"
                     f"Aggiornamenti già detti: {' | '.join(self.detti[-3:]) or 'nessuno'}.\n"
                     "Sei Jarvis, l'assistente vocale del PC. Scrivi UNA frase parlata in italiano, in prima persona "
                     "(\"sto...\", \"ho trovato...\"), massimo 20 parole, dando del tu all'utente, tono naturale e un "
                     "po' ironico, che dica a che punto sei. Non ripetere gli aggiornamenti già detti, non dire che "
                     "hai quasi finito, niente nomi di comandi o file lunghi. Solo la frase.")
        try:
            exe = trova_claude(self.imp.get("claude_exe", ""))
            r = subprocess.run([exe, "-p", richiesta, "--model", "haiku", "--effort", "low", "--tools", "",
                                "--no-session-persistence"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20, cwd=str(DATI), creationflags=0x08000000)
            frase = r.stdout.strip().strip('"').splitlines()[0] if r.stdout.strip() else ""
            if 3 < len(frase) < 220:
                return frase
        except (OSError, subprocess.TimeoutExpired, IndexError) as e:
            log("aggiornamento non generato:", repr(e))
        ultima = recenti[-1][0].lower() + recenti[-1][1:] if recenti else ""
        return f"Ci sto ancora lavorando{': ' + ultima if ultima else ''}."

    def di(self, testo):
        self.evento(jarvis=testo)
        self.da_dire.put(testo)
        self.da_dire.put(None)

    def ciclo_voce(self):
        """Dice le frasi in ordine; prepara la sintesi della successiva mentre suona quella corrente."""
        pronte = queue.Queue(maxsize=2)

        def sintetizza():
            while True:
                f = self.da_dire.get()
                if f is None:
                    pronte.put(None)
                    continue
                try:
                    pronte.put((f, self.tts.pcm(f)))
                except Exception as e:
                    log("sintesi vocale non riuscita:", repr(e))
                    pronte.put((f, audio.suono("errore")))
        threading.Thread(target=sintetizza, daemon=True, name="sintesi").start()
        while True:
            voce = pronte.get()
            if voce is None:
                if not self.claude.occupato and self.stato in ("parlo", "lavoro", "penso"):
                    self.imposta_stato("seguito" if self.imp["seguito"] else "riposo")
                    self.evento(sottofondo=self.claude.lavora_in_sottofondo())
                continue
            if self.uscita.interrotta.is_set():
                continue  # frase vecchia di prima dell'interruzione
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
        self.da_dire.put(f"Signore, devo {descrizione.split(':')[0]}. Confermi?")
        # si ascolta appena finita la domanda
        threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
        c["esito"].wait(attesa)
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

    def tocca(self):
        self._attivato()

    def nuova_conversazione(self):
        self.claude.nuova_conversazione()
        self.evento(nuova=True)

    def prova_voce(self):
        self.uscita.riprendi()
        self.di("Buongiorno signore. Tutti i sistemi sono operativi.")

    def avvia(self):
        self._applica()
        for nome in ("ciclo_ascolto", "ciclo_richieste", "ciclo_voce", "ciclo_aggiornamenti"):
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

def rete_locale(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
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
        if u.path == "/jarvis/interno/conferma":
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
            if u.path == "/jarvis/interno/conferma":
                return self.manda(200, {"ok": j.chiedi_conferma(str(d.get("descrizione", ""))[:300])})
            if u.path == "/jarvis/impostazioni":
                j.cambia(d)
                return self.manda(200, j.istantanea())
            if u.path == "/jarvis/scrivi":
                testo = str(d.get("testo", "")).strip()[:2000]
                if not testo:
                    raise ValueError("testo vuoto")
                j.scrivi(testo)
                return self.manda(200, {"ok": True})
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
        log("microfono del tablet collegato:", self.client_address[0])
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
