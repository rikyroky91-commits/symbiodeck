"""Jarvis: il cuore. Ascolta la parola chiave, capisce la richiesta, la passa a Claude Code e risponde a voce.

    cd server
    python -m jarvis                 avvio normale (lo fa pannello.py)
    python -m jarvis --scrivi "..."  manda una richiesta scritta e dice la risposta, poi esce (prova)

Stati: spento, riposo (aspetta "Ehi Jarvis"), ascolto, penso (trascrizione), lavoro (Claude Code),
parlo, conferma (aspetta sì/no), seguito (dopo una risposta ascolta ancora qualche secondo senza parola chiave).
"""
import ipaddress
import itertools
import json
import os
import queue
import re
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

from . import annulla, audio, llm_locale, memoria, tastiera, visore
from .cervello import Cervello
from .emergenza import CervelloLocale, Emergenza, SintesiLocale
from .esecutore import trova_claude
from .memoria import UTENTE, Memoria
from .permessi import Permessi
from .promemoria import Promemoria, capisci as capisci_promemoria
from .sicurezza import oscura
from .trame import Frasi, Interruzione, risposta_conferma
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
    "verifica_parola": True,       # sotto "parola_sicura" Whisper controlla che sia stato detto davvero "Jarvis"
    "parola_sicura": 0.9,          # da qui in su la parola chiave basta da sola (detta bene: 0,93-0,97)
    "seguito": 8,                  # secondi in cui ascolta ancora dopo aver risposto
    "pausa": 1.4,                  # secondi di silenzio che chiudono una frase (più lunga: puoi pensare)
    "conferme": "irreversibili",   # irreversibili | sempre | mai
    "stt": "auto",
    "stt_cpu": "medium",           # modello del processore (gioco, modello locale): medium preciso, small veloce
    "accendi_schermo": False,
    "aec_pc": True,                # cancellazione dell'eco sul PC (eco.py): toglie la sua voce dal microfono
    "interrompibile": True,        # mentre parla continua ad ascoltare: se gli parli sopra si ferma
    "eco": True,                   # cancellazione dell'eco sul tablet (modalità "chiamata" di Android)
    "emergenza": "auto",           # auto (senza Internet o fuori dai limiti) | sempre | mai
    "modello_emergenza": "27b",    # 27b (32K di contesto) | 27b-mtp (più veloce, 16K)
    "modalita_gioco": True,        # quando giochi: scheda video libera, niente modello locale
    "consegna_app": True,          # ogni conversazione compare subito nell'app Claude (progetto pc-panel)
    "cartella_lavoro": "",         # dove lavora Claude Code (vuoto: la cartella pc-panel)
    "aggiornamenti": True,         # mentre l'esecutore lavora in silenzio, ogni tanto dice a che punto è
    "aggiornamenti_ogni": 30,      # secondi di silenzio prima di un aggiornamento
    "privata": False,              # modalità privata: tutto resta sul PC (cervello, voce, memoria), mai Claude
    "conferma_alta": "tocco",      # azioni ad alto rischio: tocco (solo dal tablet) | voce
    "avvisi_temperatura": True,    # avvisa a voce se processore o scheda video scaldano troppo
    "soglia_cpu": 90,              # gradi
    "soglia_gpu": 85,
    "citta": "",                   # per il meteo del briefing del mattino (vuoto: niente meteo)
    "memoria": True,               # ricorda fatti stabili tra una conversazione e l'altra (memoria.py)
    "ragionamento_locale": False,  # il modello locale ragiona prima di rispondere (più lento, vedi LEGGIMI)
    "claude_exe": "",
}
SCELTE = {
    "modello_voce": ["opus", "sonnet", "haiku"], "modello_compiti": ["opus", "sonnet", "haiku"],
    "effort_voce": ["low", "medium", "high", "xhigh", "max"], "effort_compiti": ["low", "medium", "high", "xhigh", "max"],
    "microfono": ["tablet", "pc"], "casse": ["tablet", "pc"], "conferme": ["irreversibili", "sempre", "mai"],
    "stt": MODELLI_STT, "stt_cpu": ["medium", "small"], "voce": VOCI, "emergenza": ["auto", "sempre", "mai"],
    "modello_emergenza": ["27b", "27b-mtp"], "conferma_alta": ["tocco", "voce"],
}
# frasi brevi che Jarvis dice spesso: la loro voce si prepara all'avvio e si tiene (niente attesa della sintesi)
_CIELO = {0: "sereno", 10: "poco nuvoloso", 20: "nebbia o foschia", 40: "nebbia", 50: "pioviggine",
          60: "pioggia", 70: "neve", 80: "rovesci", 90: "temporali"}
FRASI_PRONTE = ("Procedo.", "D'accordo, lascio stare.", "Va bene, mi fermo.", "Non ho capito: sì o no?",
                "Ci guardo.", "Vediamo un po'.", "Fatto.", "Un attimo.")
# comandi detti a voce che non vanno mai a Claude (accendere la privacy non deve passare da Internet)
_PRIVATA_SI = re.compile(r"^(?:(?:attiva|accendi|passa|entra|vai|metti(?:ti)?|usa)\b.*\b)?"
                         r"(?:modalit[aà] (?:privata|locale)|solo (?:in )?locale|privacy (?:totale|massima))$")
_DETTATURA_SI = re.compile(r"^(?:(?:inizia|avvia|attiva|comincia)\w* (?:a |la )?)?(?:dettatura|detta|"
                           r"scrivi (?:quello )?che (?:ti )?dico|scrivi sotto dettatura)$")
_DETTATURA_NO = re.compile(r"^(?:fine|basta|stop|chiudi|termina|esci dalla) (?:la )?dettatura$|^smetti di scrivere$|^(?:stop|basta)$")
_A_CAPO = re.compile(r"^(?:a capo|nuova riga|invio)$")
_CANCELLA_PAROLA = re.compile(r"^cancella (?:l'ultima )?parola$")
_BRIEFING = re.compile(r"^(?:buongiorno|buon giorno|briefing|com'è la giornata|com'è la mia giornata|"
                       r"dammi il briefing|cosa c'è oggi)$")
_ANNULLA = re.compile(r"^(?:annulla (?:l'ultima modifica|le (?:ultime )?modifiche)|rimetti (?:tutto )?com'era|"
                      r"ripristina i file|torna come prima)$")
# modalità sentinella del tablet (server/sentinella.py, Sentinella.java): accesa e spenta senza passare da Claude
_SENTINELLA_NO = re.compile(r"^(?:disattiva|spegni|ferma|togli|disinserisci|disarma|chiudi|stacca)\w*\b.*\bsentinella(?: per favore| grazie)?$")
_SENTINELLA_SI = re.compile(r"^(?:attiva|accendi|avvia|metti|inserisci|arma|fai partire)\w*\b.*\bsentinella(?: per favore| grazie)?$"
                            r"|^(?:la )?modalit[aà] sentinella$")
_SENTINELLA_STATO = re.compile(r"^(?:com'?è|come va|com'?è messa|qual è lo stato|stato|cosa ha (?:visto|sentito)|"
                               r"(?:ha|hai) visto qualcosa|novità|è attiva|è accesa)\b.*\bsentinella\b.*$"
                               r"|^(?:la )?sentinella (?:ha (?:visto|sentito)|è attiva|è accesa)\b.*$")
_PRIVATA_NO = re.compile(r"^(?:(?:esci|disattiva|spegni|togli|chiudi)\b.*\bmodalit[aà] (?:privata|locale)"
                         r"|torna (?:a|con) claude|modalit[aà] normale)$")


def _riduci_foto(jpeg, lato=1568):
    """Lato lungo al massimo 1568 px (quanto Claude guarda senza ridurre da sé), JPEG di nuovo."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg))
    im.load()
    if max(im.size) <= lato and im.format == "JPEG":
        return jpeg
    im = im.convert("RGB")
    im.thumbnail((lato, lato))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def log(*parti):
    # chiavi, token e password non finiscono mai in chiaro nel registro (che contiene ogni frase detta)
    riga = time.strftime("%Y-%m-%d %H:%M:%S ") + oscura(" ".join(str(p) for p in parti))
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
        # lo speaker del Quest: Jarvis XR si collega come un altro "tablet" (stesse trame), senza microfono
        self.visore_audio = audio.Tablet(lambda *a: None)
        # il microfono del Quest (app nativa, "Hey Jarvis" senza toccare la sfera): conta solo col visore addosso
        self.mic_visore = audio.Tablet(lambda b: self._blocco("visore", b))
        self.stt = Trascrittore(self.imp, log)
        self.tts = Sintesi(self.imp, log)
        self.in_gioco = False
        self.memoria = Memoria(DATI / "fatti.json", estrai=self._estrai, log=log,
                               attiva=lambda: bool(self.imp.get("memoria", True)))
        memoria.ATTUALE = self.memoria      # i prompt di voce, esecutore e cervello locale la includono
        self.permessi = Permessi(DATI / "permessi.json", log)
        self.promemoria = Promemoria(DATI / "promemoria.json", di=lambda t: self.di_subito(t), log=log,
                                     evento=lambda **d: self.evento(**d))
        self.voce_pronta = {}      # (testo, voce, velocita, tono, online) -> pcm, per le frasi brevi
        self.caldo = {"cpu": 0, "gpu": 0, "avvisato": 0.0}
        self.dettatura = False     # "detta": quello che dici si scrive nella finestra in primo piano, senza IA
        self.blocco_domande = threading.Lock()   # una conferma o domanda per volta
        self.meteo = {"t": 0, "testo": ""}
        self.visori = 0            # visori (Quest, web/xr.js) collegati al flusso di eventi
        self.visore_immersivo = False   # l'utente è dentro Jarvis XR (sessione immersiva): microfono del tablet spento
        self.foto_attese = {}      # id -> {"pronta": Event, "jpeg": bytes}: foto chieste a visore o tablet
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
                "conferma": self.conferma and self.conferma.get("pubblico"),
                "claude": bool(trova_claude(self.imp.get("claude_exe", ""))),
                "azione": self.ultima_azione, "sottofondo": self.claude.lavora_in_sottofondo(),
                "emergenza": {"attiva": self.emergenza.attiva, "motivo": self.emergenza.motivo},
                "privata": bool(self.imp.get("privata")), "promemoria": self.promemoria.elenco(),
                "visore": self.visori > 0,
                "gioco": self.in_gioco,
                "eco": self.uscita.aec.stato() if self.uscita.aec is not None else None}

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
        self.imp["parola_sicura"] = max(0.5, min(1.01, float(self.imp["parola_sicura"])))
        self.imp["seguito"] = max(0, min(30, int(self.imp["seguito"])))
        self.imp["pausa"] = max(0.6, min(4.0, float(self.imp["pausa"])))
        salva_impostazioni(self.imp)
        self._applica()
        self.evento(impostazioni=self.impostazioni_pubbliche())
        if "stt" in nuove:
            threading.Thread(target=self._prepara_stt, daemon=True).start()
        if "privata" in nuove:
            log("modalità privata:", self.imp["privata"])
            threading.Thread(target=self.emergenza.valuta, daemon=True).start()

    def online_permesso(self):
        """False in modalità privata: niente Claude, Haiku, edge-tts. Ogni uscita verso Internet passa da qui."""
        return not self.imp.get("privata")

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
                self.uscita.aec = CancellaEco(log, file_ritardo=DATI / "eco-ritardo.json")
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
        # con il visore addosso si parla dal Quest: il microfono del tablet si spegne, se no sente la stessa voce
        self.tablet.vuoi_microfono(self.imp["attivo"] and self.imp["microfono"] == "tablet"
                                   and not self.visore_immersivo)

    def imposta_visore(self, immersivo):
        """Jarvis XR dice se l'utente ha il visore addosso, dentro la sessione (ogni 4 s finché è così: è anche il
        battito che la guardia_visore aspetta), o se l'ha tolto / la sessione è finita / il visore si è scollegato."""
        immersivo = bool(immersivo)
        if immersivo:
            self.visore_battito = time.time()
            if not getattr(self, "_guardia_visore_avviata", False):
                self._guardia_visore_avviata = True
                threading.Thread(target=self._guardia_visore, daemon=True, name="guardia-visore").start()
        if immersivo == self.visore_immersivo:
            return
        self.visore_immersivo = immersivo
        log("visore in uso: microfono del tablet spento, voce dal Quest" if immersivo
            else "visore tolto: microfono e voce di nuovo sul tablet")
        self.uscita.visore = self.visore_audio if immersivo else None
        self._aggiorna_microfono()
        # la sentinella del tablet sa che il microfono è spento per via del visore (non lo riapre per sé)
        self.tablet.comando(visore=immersivo)
        self.evento(visore_immersivo=immersivo)

    def _guardia_visore(self):
        """Visore tolto dalla testa: il Quest nasconde la sessione e la pagina si ferma, i battiti smettono. Dopo 12 s
        senza battito voce e microfono tornano sul tablet (anche se la pagina non ha fatto in tempo a dirlo)."""
        while True:
            time.sleep(2)
            if self.visore_immersivo and time.time() - getattr(self, "visore_battito", 0) > 12:
                log("visore: nessun segnale da 12 s (tolto o in pausa)")
                self.imposta_visore(False)

    # ------------------------------------------------------------ sentinella del tablet
    def comanda_sentinella(self, attiva):
        """"Jarvis, attiva la sentinella": il pannello passa il comando al tablet (nel suo flusso)."""
        r = api_pannello("/api/sentinella/comando", {"attiva": attiva, "da": "jarvis"})
        if r is None:
            self.di("Il pannello sul PC non risponde: la sentinella non riesco a comandarla.")
        elif not r.get("tablet_collegato"):
            self.di("Il tablet non è collegato al PC: la sentinella si accende dal tablet.")
        elif not attiva:
            self.di("Sentinella spenta.")
        else:
            armamento = int(((stato_sentinella() or {}).get("imp") or {}).get("armamento", 20))
            frase = (f"Sentinella attivata: fra {armamento} secondi comincio a sorvegliare con la telecamera e il "
                     "microfono del tablet." if armamento else "Sentinella attivata: sorveglio da adesso.")
            if self.visore_immersivo:
                frase += " Finché hai il visore il microfono del tablet resta spento: guardo solo con la telecamera."
            self.di(frase)
        self._torna_libero()

    def racconta_sentinella(self):
        r = api_pannello("/api/sentinella/riassunto", {})
        if r is None:
            self.di("Il pannello sul PC non risponde.")
        else:
            t = r.get("tablet")
            if not t:
                stato = "Non so com'è la sentinella: il tablet non si fa sentire."
            elif not t.get("attiva"):
                stato = "La sentinella è spenta."
            elif not t.get("armata"):
                stato = "La sentinella si sta attivando."
            else:
                stato = "La sentinella sta sorvegliando" + ("." if t.get("rumore") else ", solo con la telecamera.")
            n, ultimo = r.get("oggi", 0), r.get("ultimo")
            if n and ultimo:
                cosa = "un movimento" if ultimo.get("tipo") == "movimento" else "un rumore"
                ora = time.strftime("%H:%M", time.localtime(ultimo.get("ora") or time.time()))
                eventi = f" Oggi ha registrato {n} {'evento' if n == 1 else 'eventi'}, l'ultimo {cosa} alle {ora}."
            else:
                eventi = " Oggi nessun evento."
            self.di(stato + eventi)
        self._torna_libero()

    def sentinella_evento(self, d):
        """Il pannello avvisa: il tablet ha visto un movimento o sentito un rumore. Sul pannello di Jarvis e nel
        visore (avviso volante; se lo indossi si apre anche la foto), e a voce (al massimo ogni 45 s)."""
        testo = str(d.get("testo", "")).strip()[:200] or "un evento"
        log("sentinella:", d.get("id", ""), testo)
        self.evento(avviso=f"🛡 Sentinella: {testo}", sentinella={"id": str(d.get("id", ""))[:40],
                                                                  "tipo": str(d.get("tipo", ""))[:20]})
        adesso = time.time()
        if d.get("voce") and self.imp.get("attivo") and adesso - getattr(self, "_voce_sentinella", 0) > 45:
            self._voce_sentinella = adesso
            self.di_subito(f"Attenzione: la sentinella ha registrato {testo}.")
        if self.visore_immersivo and d.get("foto"):
            self.mostra_nel_visore({"cosa": "file", "percorso": str(d["foto"])})
        return {"ok": True}

    # ------------------------------------------------------------ telecamere (visore e tablet)
    def guarda(self, motivo, dispositivo="auto", attesa_foto=20):
        """Una foto dalla telecamera del visore (se l'utente lo indossa) o del tablet, solo col suo permesso ogni
        volta. Restituisce {"ok", "messaggio", "jpeg"} (jpeg in base64, lato lungo al massimo 1568 px)."""
        import base64
        if dispositivo not in ("visore", "tablet"):
            dispositivo = "visore" if self.visore_immersivo else "tablet"
        if dispositivo == "visore" and not self.visori:
            return {"ok": False, "messaggio": "Il visore non è collegato (Jarvis XR non è aperto)."}
        if dispositivo == "tablet" and not self.tablet.collegato():
            return {"ok": False, "messaggio": "Il tablet non è collegato a Jarvis."}
        nome = "del visore" if dispositivo == "visore" else "del tablet"
        motivo = " ".join(str(motivo).split())[:120] or "guardare"
        # "usare ..." non accetta "sì, sempre" (guardia.SEMPRE_VIETATO): il permesso si chiede ogni volta
        if not self.chiedi_conferma(f"usare la telecamera {nome}: {motivo}", sempre_ok=False):
            return {"ok": False, "messaggio": "L'utente non ha dato il permesso di usare la telecamera."}
        id_ = secrets.token_hex(6)
        attesa = {"pronta": threading.Event(), "jpeg": None, "errore": ""}
        self.foto_attese[id_] = attesa
        try:
            if dispositivo == "visore":
                self.evento(telecamera={"id": id_})
            else:
                self.tablet.comando_una_volta(foto=id_)
            self.evento(azione=f"Telecamera {nome}: {motivo}")
            if not attesa["pronta"].wait(attesa_foto) or not attesa["jpeg"]:
                return {"ok": False, "messaggio": attesa["errore"] or f"La telecamera {nome} non ha mandato la foto."}
            jpeg = _riduci_foto(attesa["jpeg"])
        finally:
            self.foto_attese.pop(id_, None)
        log("foto dalla telecamera", nome, f"{len(jpeg) // 1024} KB:", motivo)
        return {"ok": True, "messaggio": f"Foto dalla telecamera {nome}.", "jpeg": base64.b64encode(jpeg).decode()}

    def foto_arrivata(self, id_, jpeg, errore=""):
        a = self.foto_attese.get(id_)
        if not a:
            return False
        a["jpeg"], a["errore"] = jpeg, errore
        a["pronta"].set()
        return True

    def _prepara_stt(self):
        try:
            self.stt.prepara()
            self.stt.precarica_cpu()
        except Exception as e:
            log("stt non disponibile:", repr(e))
            self.evento(errore="Riconoscimento vocale non disponibile")

    # ------------------------------------------------------------ audio in ingresso
    def _blocco(self, da, blocco):
        if not self.imp["attivo"]:
            return
        if da == "visore":
            # il microfono del Quest vale solo mentre l'utente ha il visore addosso (battito di Jarvis XR); l'eco
            # della voce di Jarvis la toglie il Quest stesso (cancellazione dell'eco di Android)
            if not self.visore_immersivo:
                return
            # mentre Jarvis parla dallo speaker del Quest il suo microfono non conta: senza un segnale di riferimento
            # sul PC la sua stessa voce potrebbe sembrare l'utente che lo interrompe
            if self.uscita.parla or time.monotonic() < (self.uscita.fine_audio or 0.0) + 0.4:
                return
        else:
            if da != self.imp["microfono"]:
                return
            if self.visore_immersivo and (da == "tablet" or self.mic_visore.microfono_attivo()):
                return   # col visore si parla dal Quest: niente due microfoni mescolati
        if self.uscita.aec is not None and da != "visore":
            try:
                blocco = self.uscita.aec.pulisci(blocco, time.monotonic())
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
        # con la cancellazione dell'eco sul PC: un'Interruzione a parte che impara solo dal segnale già pulito e
        # solo a cancellazione agganciata (se imparasse mentre il filtro converge, crederebbe l'eco molto più forte)
        interruzione_pulita, epoca_eco, aec_prima = Interruzione(), None, None
        # parola chiave incerta: Whisper controlla gli ultimi 2 s in un altro thread; intanto si tiene da parte quello
        # che segue, così se era davvero "Jarvis" la richiesta detta di fila non si perde
        prima, verifica = [], None
        while True:
            try:
                blocco = self.blocchi.get(timeout=1)
            except queue.Empty:
                continue
            ora = time.time()
            prima.append(blocco)
            del prima[:-25]                                  # 2 s
            if verifica is not None:
                if verifica["esito"] is None and ora - verifica["t"] < 10:
                    verifica["dopo"].append(blocco)
                else:
                    if verifica["esito"]:
                        parlato, silenzio = self._attivato_dopo_verifica(verifica["dopo"], vad)
                        frase = verifica["dopo"] if parlato else verifica["dopo"][-4:]
                        inizio_ascolto = ora
                    verifica = None
            rms = float(np.sqrt(np.mean((blocco.astype(np.float32) / 32768) ** 2)))
            self.livello = max(rms * 6, self.livello * 0.6)
            if ora - ultimo_livello > 0.1:
                ultimo_livello = ora
                self.evento(livello=round(min(1.0, self.livello), 3))

            # parola chiave (anche mentre parla o lavora: "Ehi Jarvis" lo interrompe)
            punteggio = self.parola.predict(blocco).get("hey_jarvis", 0)
            # mentre parla la parola chiave è più severa, se la sua voce rientra dal microfono: molto senza
            # cancellazione, poco con quella del PC agganciata (simulato: sulla sola eco pulita resta sotto 0,05)
            aec = self.uscita.aec
            if aec is not aec_prima:
                aec_prima, epoca_eco, interruzione_pulita = aec, None, Interruzione()
            pulita = aec is not None and aec.affidabile()
            soglia = self.imp["sensibilita"]
            if self.uscita.parla and not self.tablet.aec:
                soglia += 0.1 if pulita else (0.25 if self.imp["microfono"] == self.imp["casse"] else 0)
            if punteggio >= soglia and ora - dopo_attivazione > 1.5 and self.stato != "spento":
                dopo_attivazione = ora
                self.parola.reset()
                log("parola chiave", round(float(punteggio), 2))
                if self.imp.get("verifica_parola", True) and punteggio < self.imp.get("parola_sicura", 0.9):
                    if verifica is None:     # una per volta
                        verifica = {"esito": None, "dopo": [], "t": ora}
                        threading.Thread(target=self._verifica_parola, args=(np.concatenate(prima), verifica),
                                         daemon=True).start()
                    continue
                verifica = None              # detta bene: la verifica in corso non serve più
                self._attivato()
                frase, parlato, silenzio, inizio_ascolto = [], False, 0, ora
                vad.reset_states()
                continue

            # mentre Jarvis parla si continua ad ascoltare: una voce vera, più forte dell'eco, lo interrompe
            # (anche nelle pause tra una frase e l'altra: lo stato resta "parlo" finché la risposta non è finita)
            if self.stato == "parlo" and self.imp.get("interrompibile", True):
                prob = float(vad.predict(blocco, frame_size=640))
                sopra_voce.append(blocco)
                # l'inizio della frase non si perde: 800 ms; 1,6 s se l'audio è già pulito dall'eco (con eco molto
                # forte l'interruzione scatta più tardi: provato al 60%, la frase perdeva "Aspetta, fermati")
                del sopra_voce[:-(20 if aec is not None else 10)]
                if aec is not None:
                    if aec.epoca != epoca_eco:          # il filtro è ripartito: si reimpara
                        epoca_eco, interruzione_pulita = aec.epoca, Interruzione()
                    # a cancellazione non agganciata niente interruzione a voce (resta "Hey Jarvis")
                    scatta = pulita and interruzione_pulita.passo(prob, rms, self.uscita.livello_uscita(),
                                                                  self._fattore_eco())
                    k = interruzione_pulita.eco
                else:
                    scatta = interruzione.passo(prob, rms, self.uscita.livello_uscita(), self._fattore_eco())
                    k = interruzione.eco
                if scatta:
                    log("interrotto mentre parlava", f"(voce {rms:.3f}, eco {k:.3f})")
                    self._interrompi_voce()
                    frase, parlato, silenzio, inizio_ascolto = list(sopra_voce), True, 0, ora
                    sopra_voce = []
                continue
            if interruzione.blocchi > 10 or interruzione_pulita.blocchi > 10:   # ha appena finito di parlare
                if aec is not None:
                    log("eco della voce dopo la cancellazione:", aec.stato(), f"(rimasta {interruzione_pulita.eco:.3f})")
                else:
                    log(f"eco della voce: {interruzione.eco:.3f} (microfono/uscita; con cancellazione efficace < 0,15)")
            interruzione.azzera()
            interruzione_pulita.azzera()
            ascolta = ("ascolto", "seguito", "conferma") + (("penso", "lavoro") if self.conversazione else ())
            if self.stato not in ascolta:
                self.utente_parla = False
                continue
            if self.uscita.parla or self._coda_eco(aec):
                continue  # suoni brevi (attivazione, conferma) e la loro eco: non si ascoltano
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
                if self.stato in ("penso", "lavoro") or self.claude.lavora_in_sottofondo() or self.dettatura:
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

    def _coda_eco(self, aec):
        """Appena finito un suono o una frase, la sua eco arriva ancora al microfono per il ritardo casse ->
        microfono più il riverbero. Senza cancellazione agganciata quella coda non si ascolta: provato il
        2026-11-24, l'eco del suono di attivazione è diventata un "Sì!" (durante una conferma avrebbe approvato)."""
        if aec is not None and aec.affidabile():
            return False
        ritardo = aec.ritardo if aec is not None else 0.6
        return time.monotonic() < self.uscita.fine_audio + ritardo + 0.3

    def _fattore_eco(self):
        """Quanto più forte dell'eco deve essere una voce per interrompere. Con la cancellazione dell'eco del
        tablet (microfono e casse sullo stesso tablet) basta meno."""
        # simulato con voci vere (2026-11-23): 1,6 non scatta mai sulla sola eco, né con né senza la
        # cancellazione del tablet; con la cancellazione si ferma 0,4-0,8 s dopo che la persona parla
        return 1.6

    def _zittisci(self):
        """Smette di parlare e butta tutto il resto della risposta: le frasi in coda, quelle già sintetizzate
        (gen_voce) e quello che Claude sta ancora scrivendo."""
        self.gen_voce += 1
        self.uscita.taci()
        self._svuota(self.da_dire)
        self._svuota(self.pronte)
        self.frasi = Frasi()
        if self.claude.occupato:          # stava ancora scrivendo la risposta: il resto non serve
            self.scarta = True
            self.claude.interrompi()
        self.uscita.riprendi()

    def _interrompi_voce(self):
        self._zittisci()
        self.conversazione = True
        self.imposta_stato("ascolto")
        self.evento(interrotto=True)

    def _suona(self, nome):
        threading.Thread(target=self.uscita.riproduci, args=(audio.suono(nome),), daemon=True).start()

    def _attivato(self, suono=True):
        if self.conferma:
            return
        if self.stato == "parlo" or self.claude.occupato:
            # "Hey Jarvis" mentre parla o prepara la risposta: si zittisce del tutto e ascolta (prima la frase già
            # sintetizzata e il resto della risposta ripartivano dopo il suono, sopra la richiesta nuova)
            self._zittisci()
        self.uscita.riprendi()
        self.conversazione = True
        self.solo_remoto = False
        self.imposta_stato("ascolto")
        self.tablet.comando(attivato=time.time())
        # solo un suono, niente "Dimmi" a voce: mentre parla non ascolta, e si perderebbe l'inizio
        if suono:
            self._suona("attivo")

    def _verifica_parola(self, pcm, verifica):
        """Thread: c'è davvero "Jarvis" nell'audio della parola chiave? Se Whisper non va: no (meglio una parola
        chiave da ripetere che Jarvis che si accende mentre si parla d'altro)."""
        try:
            detto, testo = self.stt.dice_jarvis(pcm)
        except Exception as e:
            detto, testo = False, f"(verifica non riuscita: {e!r})"[:160]
        log("parola chiave", "confermata" if detto else "scartata", f"da Whisper: {testo!r}")
        verifica["esito"] = detto

    def _attivato_dopo_verifica(self, dopo, vad):
        """Parola chiave confermata: si attiva e si riprende quello che l'utente ha detto mentre si verificava.
        Restituisce (sta parlando, blocchi di silenzio dopo la voce); se parla già, niente suono (coprirebbe le
        sue parole)."""
        vad.reset_states()
        parlato, silenzio = False, 0
        for b in dopo:
            if vad.predict(b, frame_size=640) > 0.5:
                parlato, silenzio = True, 0
            elif parlato:
                silenzio += 1
        self._attivato(suono=not parlato)
        self.utente_parla = parlato
        return parlato, silenzio

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
                if self._comando_locale(testo):
                    continue
                if _BRIEFING.match(re.sub(r"^(?:ehi |hey )?jarvis\W*", "",
                                          " ".join(testo.lower().replace("’", "'").split()).strip(" .!?,"))):
                    log("briefing del mattino")
                    testo = self.briefing()
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

    def _comando_locale(self, testo):
        """Richieste gestite sul PC senza mandarle a nessun modello: modalità privata e promemoria. True se gestita."""
        t = " ".join(testo.lower().replace("’", "'").split()).strip(" .!?,")
        t = re.sub(r"^(?:ehi |hey )?jarvis\W*", "", t)
        if self.dettatura:
            return self._detta(testo, t)
        if _DETTATURA_SI.match(t):
            self.dettatura = True
            self.conversazione = True
            log("dettatura iniziata")
            self.evento(dettatura=True)
            self.di("Detta pure: scrivo nella finestra davanti. Per finire di' fine dettatura.")
            self.imposta_stato("ascolto")
            return True
        if _ANNULLA.match(t):
            threading.Thread(target=self.annulla_modifiche, daemon=True).start()
            return True
        if _SENTINELLA_NO.match(t) or _SENTINELLA_SI.match(t):
            threading.Thread(target=self.comanda_sentinella, args=(not _SENTINELLA_NO.match(t),), daemon=True).start()
            return True
        if _SENTINELLA_STATO.match(t):
            threading.Thread(target=self.racconta_sentinella, daemon=True).start()
            return True
        if _PRIVATA_NO.match(t):
            if self.imp.get("privata"):
                self.cambia({"privata": False})
                self.di("Modalità privata spenta: torno a Claude.")
            else:
                self.di("Non ero in modalità privata.")
            return True
        if _PRIVATA_SI.match(t):
            if not self.imp.get("privata"):
                self.cambia({"privata": True})
                self.di("Modalità privata: da adesso resta tutto su questo PC.")
            else:
                self.di("Sono già in modalità privata.")
            return True
        richiesta = capisci_promemoria(testo)
        if richiesta:
            log("promemoria (locale):", richiesta.get("tipo"), testo)
            self.di(self.promemoria.rispondi(richiesta))
            self._torna_libero()
            return True
        if self.imp.get("privata") and not getattr(self.claude, "locale", False):
            motivo = ("stai giocando, e il cervello locale lo accendo quando finisci" if self.in_gioco
                      else "il cervello locale non è ancora pronto")
            self.di(f"Sono in modalità privata e {motivo}. Claude non lo uso.")
            self._torna_libero()
            return True
        return False

    def _detta(self, originale, t):
        """In dettatura: scrive la frase nella finestra in primo piano (mai a un modello)."""
        if _DETTATURA_NO.match(t):
            self.dettatura = False
            log("dettatura finita")
            self.evento(dettatura=False)
            self.di("Fine dettatura.")
            return True
        if _A_CAPO.match(t):
            tastiera.premi(tastiera.INVIO)
        elif _CANCELLA_PAROLA.match(t):
            tastiera.premi(tastiera.CTRL, tastiera.BACKSPACE)
        else:
            tastiera.scrivi(originale.strip() + " ")
            self.evento(azione="Scritto: " + originale.strip()[:80])
        self.imposta_stato("ascolto")
        return True

    def annulla_modifiche(self):
        cartella = DATI / "annulla"
        gruppo = annulla.ultimo_gruppo(cartella)
        if not gruppo:
            self.di("Non ho modifiche ai file da annullare.")
            return
        nomi = list(dict.fromkeys(os.path.basename(v["percorso"]) for v in gruppo))
        elenco = ", ".join(nomi[:4]) + (f" e altri {len(nomi) - 4}" if len(nomi) > 4 else "")
        if not self.chiedi_conferma(f"rimettere com'erano {len(nomi)} file: {elenco}"):
            return
        rimessi, tolti, falliti = annulla.ripristina(cartella, gruppo)
        log("annullate le modifiche:", rimessi, tolti, falliti)
        parti = []
        if rimessi:
            parti.append(f"ho rimesso com'erano {', '.join(rimessi[:4])}")
        if tolti:
            parti.append(f"ho tolto i file nuovi {', '.join(tolti[:4])} (sono in jarvis-dati, non cancellati)")
        if falliti:
            parti.append(f"non sono riuscito con {', '.join(falliti[:4])}")
        self.di(("; ".join(parti) or "Fatto") + ".")

    def briefing(self):
        """Il testo da mandare al cervello per il briefing del mattino: i dati li raccoglie il PC."""
        from datetime import datetime
        from .promemoria import ora_parlata
        oggi = datetime.now().date()
        di_oggi = [f"{ora_parlata(datetime.fromtimestamp(v['quando']))}: {v['testo']}"
                   for v in self.promemoria.voci if datetime.fromtimestamp(v["quando"]).date() == oggi]
        righe = [f"Promemoria di oggi: {'; '.join(di_oggi) if di_oggi else 'nessuno'}."]
        meteo = self.leggi_meteo()
        if meteo:
            righe.append(f"Meteo: {meteo}.")
        try:
            import shutil
            libero = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").free / 2**30
            righe.append(f"Spazio libero sul disco di sistema: {libero:.0f} GB.")
        except OSError:
            pass
        d = stato_pannello() or {}
        temp = [f"{n} {(d.get(k) or {}).get('temp'):.0f} °C" for k, n in (("cpu", "processore"), ("gpu", "scheda video"))
                if (d.get(k) or {}).get("temp")]
        if temp:
            righe.append("Temperature: " + ", ".join(temp) + ".")
        extra = ("" if getattr(self.claude, "locale", False) else
                 " Se l'esecutore può leggere calendario e posta (connettori collegati), affidagli di controllare gli "
                 "impegni di oggi e le mail importanti e aggiungili quando arrivano; se non può, non dirlo.")
        return ("[Briefing del mattino chiesto dall'utente] Dati raccolti dal PC:\n" + "\n".join(righe) +
                "\nFai un buongiorno di massimo 30 secondi con le cose che contano, senza elencare tutto." + extra)

    def leggi_meteo(self):
        """Meteo di oggi per la città nelle impostazioni (Open-Meteo, gratis, senza chiave). Mai in privata."""
        citta = (self.imp.get("citta") or "").strip()
        if not citta or not self.online_permesso():
            return ""
        if time.time() - self.meteo["t"] < 1800 and self.meteo.get("citta") == citta:
            return self.meteo["testo"]
        import urllib.parse
        import urllib.request
        try:
            with urllib.request.urlopen("https://geocoding-api.open-meteo.com/v1/search?count=1&language=it&name="
                                        + urllib.parse.quote(citta), timeout=4) as r:
                luogo = json.loads(r.read())["results"][0]
            url = ("https://api.open-meteo.com/v1/forecast?timezone=auto&forecast_days=1"
                   f"&latitude={luogo['latitude']}&longitude={luogo['longitude']}"
                   "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
                   "&current=temperature_2m")
            with urllib.request.urlopen(url, timeout=4) as r:
                m = json.loads(r.read())
            g = m["daily"]
            testo = (f"{citta}: ora {m['current']['temperature_2m']:.0f} gradi, minima {g['temperature_2m_min'][0]:.0f}, "
                     f"massima {g['temperature_2m_max'][0]:.0f}, pioggia {g['precipitation_probability_max'][0]}%, "
                     f"{_CIELO.get(g['weather_code'][0] // 10 * 10, 'tempo variabile')}")
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            log("meteo non disponibile:", repr(e)[:120])
            return ""
        self.meteo = {"t": time.time(), "testo": testo, "citta": citta}
        return testo

    def mostra_nel_visore(self, d):
        """Dalla voce, dall'esecutore o dal cervello locale (visore_mcp.py): apre nel visore una cartella, un file,
        una finestra o lo schermo. Restituisce un messaggio per chi l'ha chiesto."""
        if not self.visori:
            return {"ok": False, "messaggio": "Il visore non è collegato (l'app Jarvis XR non è aperta)."}
        cosa = str(d.get("cosa", ""))
        if cosa == "cartella":
            percorso = visore.risolvi_cartella(d.get("nome") or d.get("percorso") or "", d.get("dove") or "")
            if not percorso:
                return {"ok": False, "messaggio": "Cartella non trovata: fatti dare il percorso esatto dall'esecutore "
                                                  "e richiamami con quello."}
            try:
                n = len(visore.elenco(percorso)["voci"])
            except (OSError, PermissionError) as e:
                return {"ok": False, "messaggio": f"Non posso mostrarla: {e}"}
            self.evento(visore={"cosa": "cartella", "percorso": percorso})
            return {"ok": True, "messaggio": f"Aperta nel visore: {percorso} ({n} elementi)."}
        if cosa == "file":
            percorso = str(d.get("percorso", ""))
            try:
                visore.leggibile(percorso)
            except PermissionError as e:
                return {"ok": False, "messaggio": str(e)}
            self.evento(visore={"cosa": "file", "percorso": percorso, "tipo": visore.tipo_di(percorso)})
            return {"ok": True, "messaggio": f"Mostrato nel visore: {os.path.basename(percorso)}."}
        if cosa == "finestra":
            f = visore.trova_finestra(d.get("titolo", ""))
            if not f:
                elenco = ", ".join(x["titolo"][:40] for x in visore.finestre()[:8])
                return {"ok": False, "messaggio": f"Nessuna finestra così. Aperte: {elenco or 'nessuna'}."}
            self.evento(visore={"cosa": "finestra", "hwnd": f["hwnd"], "titolo": f["titolo"]})
            return {"ok": True, "messaggio": f"Finestra nel visore: {f['titolo']}."}
        if cosa in ("schermo", "finestre", "file_radici", "chiudi"):
            self.evento(visore={"cosa": cosa, "numero": int(d.get("numero") or 1)})
            return {"ok": True, "messaggio": "Fatto nel visore."}
        return {"ok": False, "messaggio": "Cosa mostrare? cartella, file, finestra, schermo, finestre o chiudi."}

    def _fine_conferma(self):
        """Conferma o domanda chiusa (risposta, scadenza, chiusa da fuori): mai restare nello stato "conferma". Prima,
        a domanda scaduta in silenzio, Jarvis ci restava per ore e il tablet mostrava lo schermo rosso (2026-11-25)."""
        if self.stato != "conferma":
            return
        if self.claude.occupato or self.claude.lavora_in_sottofondo():
            self.imposta_stato("lavoro")
        else:
            self.imposta_stato("riposo")

    def chiudi_domanda(self):
        """La domanda in corso non serve più (es. visore consentito dalle impostazioni del PC): si chiude in
        silenzio, senza risposta. True se ce n'era una."""
        c = self.conferma
        if not c or not c.get("domanda"):
            return False
        c["testo"], c["ok"] = "", False
        self._svuota(self.da_dire)
        self.uscita.taci()
        c["esito"].set()
        return True

    def _torna_libero(self):
        if not self.claude.occupato and not self.claude.lavora_in_sottofondo():
            self.imposta_stato("seguito" if self.imp.get("seguito") else "riposo")

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
        # memoria: lo scambio va all'estrattore (in sottofondo). Se l'esecutore sta ancora lavorando si aspetta il
        # turno con l'esito, che contiene le cose vere
        if not errore and not self.claude.lavora_in_sottofondo():
            self.memoria.dopo_il_turno(self.richiesta, self.risposta or risultato or "")
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
        uscita = self._haiku(sistema, richiesta, 20)
        frase = uscita.strip().strip('"').splitlines()[0] if uscita and uscita.strip() else ""
        if 3 < len(frase) < 220:
            return frase
        ultima = recenti[-1][0].lower() + recenti[-1][1:] if recenti else ""
        return f"Ci sto ancora lavorando{': ' + ultima if ultima else ''}."

    def _haiku(self, sistema, richiesta, attesa=30):
        """Una risposta da Haiku senza strumenti né contesto di Claude Code (pochi token). None se non va."""
        if not self.online_permesso():
            return None
        try:
            exe = trova_claude(self.imp.get("claude_exe", ""))
            r = subprocess.run([exe, "-p", "--model", "haiku", "--effort", "low", "--tools", "",
                                "--system-prompt", sistema, "--strict-mcp-config", "--disable-slash-commands",
                                "--setting-sources", "", "--no-session-persistence"],
                               input=richiesta, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=attesa, cwd=str(DATI), creationflags=0x08000000)
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, TypeError) as e:
            log("haiku non raggiungibile:", repr(e)[:160])
            return None

    def _estrai(self, sistema, testo):
        """Per la memoria: il modello locale in emergenza (se acceso), altrimenti Haiku. Niente durante il gioco."""
        if self.in_gioco:
            return None
        if self.emergenza.attiva:
            return llm_locale.frase(sistema, testo, max_token=400, attesa=60, una_riga=False) \
                if llm_locale.attivo() else None
        return self._haiku(sistema, testo, 40)

    def ricorda(self, fatto):
        return self.memoria.aggiungi(fatto, UTENTE)

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
            nuovo.contesto = lambda: " [l'utente indossa il visore Quest: per mostrare cartelle, foto, video e " \
                                     "finestre usa gli strumenti visore_*]" if self.visori else ""
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
                pcm = self._sintetizza(f)
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

    def _sintetizza(self, frase):
        """PCM della frase: edge-tts (online) o Piper (sul PC: emergenza, modalità privata, edge-tts giù). Le frasi
        brevi si tengono pronte (idea da DesktopJarvis): "Procedo." parte senza aspettare la sintesi."""
        online = not self.emergenza.attiva and self.online_permesso()
        chiave = (frase, self.imp.get("voce"), self.imp.get("velocita"), self.imp.get("tono"), online)
        if len(frase) <= 60 and chiave in self.voce_pronta:
            return self.voce_pronta[chiave]
        pcm = None
        if online:
            try:
                pcm = self.tts.pcm(frase)
            except Exception as e:
                log("sintesi vocale online non riuscita, uso Piper:", repr(e)[:120])
                chiave = chiave[:4] + (False,)
        if pcm is None:
            try:
                pcm = self.tts_locale.pcm(frase)
            except Exception as e:
                log("sintesi vocale locale non riuscita:", repr(e))
                return audio.suono("errore")
        if len(frase) <= 60 and pcm:
            if len(self.voce_pronta) > 80:
                self.voce_pronta.pop(next(iter(self.voce_pronta)))
            self.voce_pronta[chiave] = pcm
        return pcm

    def prepara_frasi(self):
        for f in FRASI_PRONTE:
            try:
                self._sintetizza(f)
            except Exception as e:
                log("frase pronta non preparata:", repr(e)[:120])
                return

    # ------------------------------------------------------------ avvisi di temperatura
    def ciclo_temperature(self):
        """Ogni 10 s i dati del pannello: se processore o scheda video restano sopra soglia per 30 s, Jarvis lo dice
        (una volta ogni 15 minuti). Idea da jarvis-fr; i sensori sono quelli che il pannello legge già."""
        while True:
            time.sleep(10)
            if not self.imp.get("avvisi_temperatura", True) or self.stato == "spento":
                continue
            d = stato_pannello()
            if d:
                self.controlla_temperature(d)

    def controlla_temperature(self, d, adesso=None):
        adesso = adesso or time.time()
        for parte, soglia in (("cpu", self.imp.get("soglia_cpu", 90)), ("gpu", self.imp.get("soglia_gpu", 85))):
            temp = (d.get(parte) or {}).get("temp")
            self.caldo[parte] = self.caldo[parte] + 1 if temp and temp >= soglia else 0
            if self.caldo[parte] >= 3 and adesso - self.caldo["avvisato"] > 15 * 60:
                self.caldo["avvisato"] = adesso
                nome = "il processore" if parte == "cpu" else "la scheda video"
                log("temperatura alta:", parte, temp)
                self.evento(avviso=f"{nome} a {temp:.0f} °C")
                self.di_subito(f"Occhio: {nome} è a {temp:.0f} gradi da più di mezzo minuto.")

    # ------------------------------------------------------------ conferme (dalla guardia)
    def chiedi_conferma(self, descrizione, attesa=45, impronta="", sempre_ok=False, alto=False):
        # una per volta: due richieste insieme (esecutore e un'altra sessione) si sovrascriverebbero
        if not self.blocco_domande.acquire(timeout=attesa + 30):
            log("conferma scartata: un'altra è in corso da troppo", descrizione)
            return False
        try:
            return self._chiedi_conferma(descrizione, attesa, impronta, sempre_ok, alto)
        finally:
            self.blocco_domande.release()

    def chiedi_risposta(self, domanda, opzioni=(), attesa=120):
        """Per le altre sessioni di Claude Code (jarvis_mcp.py): fa una domanda a voce e restituisce la risposta
        detta (o l'opzione toccata sul tablet o sul Quest). Stringa vuota se non risponde."""
        if not self.blocco_domande.acquire(timeout=attesa):
            return ""
        try:
            opzioni = [str(o)[:60] for o in list(opzioni)[:4]]
            c = {"id": secrets.token_hex(4), "descrizione": domanda[:300], "esito": threading.Event(), "ok": False,
                 "domanda": True, "opzioni": opzioni, "testo": "", "sempre": False, "sempre_ok": False,
                 "alto": False}
            c["pubblico"] = {"id": c["id"], "descrizione": c["descrizione"], "domanda": True, "opzioni": opzioni,
                             "scade": time.time() + attesa}
            self.conferma = c
            self.evento(conferma=c["pubblico"])
            self.uscita.riprendi()
            self.da_dire.put(domanda + (" Puoi dire: " + ", ".join(opzioni) + "." if opzioni else ""))
            threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
            c["esito"].wait(attesa)
            self.conferma = None
            self.evento(conferma=None)
            self._fine_conferma()
            log("domanda", domanda[:120], "->", c["testo"])
            return c["testo"]
        finally:
            self.blocco_domande.release()

    def _chiedi_conferma(self, descrizione, attesa=45, impronta="", sempre_ok=False, alto=False):
        ricordata = self.permessi.decisione(impronta, sempre_ok)
        if ricordata is not None:
            log("conferma", descrizione, "->", ricordata, "(decisione ricordata)")
            self.evento(azione=("Permesso già dato: " if ricordata else "Vietato per sempre: ") + descrizione[:120])
            if ricordata:
                self.da_dire.put(f"Vado: {descrizione.split(':')[0]} me l'avevi già permesso.")
            return ricordata
        if self.solo_remoto:
            attesa = max(attesa, 90)   # da telefono/orologio si risponde più lentamente (anche il conto alla rovescia)
        c = {"id": secrets.token_hex(4), "descrizione": descrizione, "esito": threading.Event(), "ok": False,
             "sempre": False, "sempre_ok": bool(sempre_ok and impronta),
             "alto": bool(alto and self.imp.get("conferma_alta", "tocco") == "tocco")}
        c["pubblico"] = {"id": c["id"], "descrizione": descrizione, "sempre_ok": c["sempre_ok"], "alto": c["alto"],
                         "scade": time.time() + attesa}
        self.conferma = c
        self.evento(conferma=c["pubblico"])
        self.uscita.riprendi()
        # a voce solo cosa (senza il comando: illeggibile), il dettaglio è sul pannello. Il "sì, sempre" si
        # suggerisce una volta per avvio di Jarvis, solo dove vale
        suggerimento = ""
        if c["sempre_ok"] and not getattr(self, "_suggerito_sempre", False):
            self._suggerito_sempre = True
            suggerimento = " Se vuoi puoi dire anche sì, sempre."
        if c["alto"]:
            # un "sì" detto da una TV o da un'altra persona non deve bastare (idea da jarvis-desktop, jarvis-fr)
            self.da_dire.put(f"Devo {descrizione.split(':')[0]}. È un'azione delicata: per farla tocca Sì sul "
                             + ("telefono o sull'orologio" if self.solo_remoto else "tablet")
                             + ". A voce posso solo annullarla.")
        else:
            self.da_dire.put(f"Devo {descrizione.split(':')[0]}. Confermi?{suggerimento}")
        # si ascolta appena finita la domanda
        threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
        c["esito"].wait(attesa)
        self.conferma = None
        self.evento(conferma=None)
        self._fine_conferma()
        log("conferma", descrizione, "->", c["ok"], "(per sempre)" if c["sempre"] else "")
        if c["sempre"]:
            self.permessi.ricorda(impronta, c["ok"], descrizione, c["sempre_ok"])
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
        ok, sempre = risposta_conferma(testo) if testo else (None, False)
        self._risposta_conferma(ok, testo, sempre)

    def _risposta_conferma(self, ok, testo="", sempre=False):
        c = self.conferma
        if not c:
            return
        if c.get("domanda"):
            if not testo:      # silenzio: si aspetta ancora (scade con l'attesa della domanda)
                threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
                return
            c["testo"], c["ok"] = testo, True
            self.imposta_stato("lavoro")
            self.da_dire.put("Ricevuto.")
            c["esito"].set()
            return
        if c.get("alto") and ok:
            self.da_dire.put("Per questa serve il tocco sul tablet.")
            threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
            return
        if ok is None and testo and not c.get("richiesto"):
            c["richiesto"] = True
            self.da_dire.put("Non ho capito: sì o no?")
            threading.Thread(target=self._ascolta_conferma_dopo_domanda, args=(c,), daemon=True).start()
            return
        c["ok"] = bool(ok)
        c["sempre"] = bool(sempre and (c["sempre_ok"] or not ok))   # "no, mai" vale sempre; "sì, sempre" non per tutto
        self.imposta_stato("lavoro")
        if c["sempre"] and ok:
            frase = "Procedo, e d'ora in poi questo lo faccio senza chiedertelo."
        elif c["sempre"]:
            frase = "D'accordo: questo non lo faccio, e non te lo chiederò più."
        elif sempre and ok:
            frase = "Procedo. Per questo però continuerò a chiederti ogni volta."
        else:
            frase = "Procedo." if ok else "D'accordo, lascio stare."
        self.da_dire.put(frase)
        c["esito"].set()

    def conferma_dal_pannello(self, id_, ok, sempre=False, testo=None):
        c = self.conferma
        if c and c["id"] == id_ and c.get("domanda"):
            c["testo"], c["ok"] = str(testo or ("sì" if ok else "no"))[:300], True
            c["esito"].set()
            return
        if c and c["id"] == id_:
            c["ok"] = bool(ok)
            c["sempre"] = bool(sempre and (c["sempre_ok"] or not ok))
            self.imposta_stato("lavoro")
            c["esito"].set()

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
                     "ciclo_gpu", "ciclo_emergenza", "ciclo_temperature"):
            threading.Thread(target=self._protetto, args=(getattr(self, nome),), daemon=True, name=nome).start()
        threading.Thread(target=self._protetto, args=(self.promemoria.ciclo,), daemon=True, name="promemoria").start()
        threading.Thread(target=self.prepara_frasi, daemon=True, name="frasi-pronte").start()
        threading.Thread(target=self._prepara_stt, daemon=True).start()

    def _protetto(self, f):
        while True:
            try:
                f()
            except Exception:
                log("errore in", f.__name__, traceback.format_exc())
                time.sleep(2)


# ---------------------------------------------------------------- server HTTP per il tablet

def stato_pannello():
    """I dati che il pannello legge ogni secondo (FPS, carico e temperature di CPU e GPU), o None."""
    import urllib.request
    tablet, _pc = token_pannello()
    try:
        r = urllib.request.Request("http://127.0.0.1:47800/api/stato", headers={"X-Token": tablet})
        with urllib.request.urlopen(r, timeout=3) as risposta:
            return json.loads(risposta.read())
    except (OSError, ValueError):
        return None


def api_pannello(percorso, corpo):
    """POST al pannello sul PC (127.0.0.1:47800) con la chiave del tablet. None se non risponde."""
    import urllib.request
    tablet, _pc = token_pannello()
    r = urllib.request.Request("http://127.0.0.1:47800" + percorso, method="POST",
                               data=json.dumps(corpo).encode("utf-8"),
                               headers={"X-Token": tablet, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as risposta:
            return json.loads(risposta.read())
    except (OSError, ValueError):
        return None


def stato_sentinella():
    import urllib.request
    tablet, _pc = token_pannello()
    try:
        r = urllib.request.Request("http://127.0.0.1:47800/api/sentinella?quanti=1", headers={"X-Token": tablet})
        with urllib.request.urlopen(r, timeout=5) as risposta:
            return json.loads(risposta.read())
    except (OSError, ValueError):
        return None


def rileva_gioco():
    """Si sta giocando? Con i dati che il pannello legge ogni secondo: FPS di RivaTuner (un gioco agganciato),
    oppure una finestra a schermo intero con la scheda video carica, oppure la scheda video molto carica."""
    d = stato_pannello()
    if d is None:
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

    def manda_dati(self, dati, tipo, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        self.send_header("Cache-Control", cache)
        self._cors()
        self.end_headers()
        self.wfile.write(dati)

    def manda_file(self, percorso):
        """File così com'è, a pezzi se il browser chiede un intervallo (Range): i video si possono scorrere."""
        p = visore.leggibile(percorso)
        totale = p.stat().st_size
        inizio, fine = 0, totale - 1
        intervallo = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)", intervallo)
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                inizio = int(m.group(1))
                fine = min(int(m.group(2)), totale - 1) if m.group(2) else min(inizio + 4 * 2**20 - 1, totale - 1)
            else:
                inizio = max(0, totale - int(m.group(2)))
        if visore.tipo_di(p.name) == "testo" and not m:
            fine = min(fine, 400 * 1024 - 1)   # i testi lunghi si mostrano a metà
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", visore.tipo_mime(p))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fine - inizio + 1))
        if m:
            self.send_header("Content-Range", f"bytes {inizio}-{fine}/{totale}")
        self.send_header("Cache-Control", "private, max-age=300")
        self._cors()
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(inizio)
            resto = fine - inizio + 1
            while resto > 0:
                pezzo = f.read(min(256 * 1024, resto))
                if not pezzo:
                    break
                self.wfile.write(pezzo)
                resto -= len(pezzo)

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
        if u.path == "/jarvis/altoparlante-visore":
            return self.altoparlante(visore=True)
        if u.path == "/jarvis/eventi":
            dopo = int((q.get("dopo") or ["0"])[0] or 0)
            return self.manda(200, self.j.novita(dopo))
        if u.path == "/jarvis/memoria":
            return self.manda(200, {"attiva": bool(self.j.imp.get("memoria", True)), "fatti": self.j.memoria.elenco(),
                                    "permessi": self.j.permessi.elenco()})
        if u.path == "/jarvis/schermo/diretta":
            return self.diretta(q)
        if u.path.startswith("/jarvis/file/") or u.path.startswith("/jarvis/finestre") or u.path.startswith("/jarvis/schermo/"):
            return self.visore_get(u, q)
        if u.path == "/jarvis/conversazioni":
            return self.manda(200, {"attuale": self.j.claude.sessione, "conversazioni": self.j.claude.conversazioni()})
        return self.manda(404, {"errore": "non trovato"})

    def visore_get(self, u, q):
        """File, anteprime, finestre e schermo per il visore (web/xr.js)."""
        a = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        try:
            if u.path == "/jarvis/file/elenco":
                return self.manda(200, visore.elenco(a("percorso")))
            if u.path == "/jarvis/file/anteprima":
                return self.manda_dati(visore.anteprima(a("percorso"), int(a("lato", "320"))), "image/jpeg",
                                       "private, max-age=600")
            if u.path == "/jarvis/file/dati":
                return self.manda_file(a("percorso"))
            if u.path == "/jarvis/finestre":
                return self.manda(200, {"finestre": visore.finestre()})
            if u.path == "/jarvis/finestre/immagine":
                return self.manda_dati(visore.cattura_finestra(int(a("hwnd", "0")), int(a("lato", "1600"))), "image/jpeg")
            if u.path == "/jarvis/schermo/immagine":
                return self.manda_dati(visore.cattura_schermo(int(a("numero", "1")), int(a("lato", "1920"))), "image/jpeg")
        except (PermissionError, LookupError, ValueError, FileNotFoundError) as e:
            return self.manda(404, {"errore": str(e)})
        except (ConnectionError, BrokenPipeError):
            return None
        except Exception as e:
            log("visore:", traceback.format_exc()[-600:])
            return self.manda(500, {"errore": str(e)})
        return self.manda(404, {"errore": "non trovato"})

    def do_POST(self):
        r = self._controlli()
        if not r:
            return
        u, q = r
        if u.path == "/jarvis/microfono":
            return self.microfono()
        if u.path == "/jarvis/voce":
            return self.voce_intera()
        if u.path == "/jarvis/telecamera":
            return self.foto_dal_dispositivo(q)
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
                return self.manda(200, {"ok": j.chiedi_conferma(str(d.get("descrizione", ""))[:300],
                                                                impronta=str(d.get("impronta", ""))[:1000],
                                                                sempre_ok=bool(d.get("sempre_ok")),
                                                                alto=bool(d.get("alto")))})
            if u.path == "/jarvis/interno/promemoria":
                from datetime import datetime
                quando = datetime.strptime(str(d.get("quando", "")).strip()[:16], "%Y-%m-%d %H:%M")
                v = j.promemoria.aggiungi(quando, str(d.get("testo", ""))[:200])
                return self.manda(200, {"ok": True, "id": v["id"]})
            if u.path == "/jarvis/promemoria-esterno":
                from datetime import datetime
                quando = datetime.strptime(str(d.get("quando", "")).strip()[:16], "%Y-%m-%d %H:%M")
                v = j.promemoria.aggiungi(quando, str(d.get("testo", ""))[:200])
                return self.manda(200, {"ok": True, "id": v["id"]})
            if u.path == "/jarvis/promemoria":
                j.promemoria.togli(int(d["togli"]) if "togli" in d else None)
                return self.manda(200, {"promemoria": j.promemoria.elenco()})
            if u.path == "/jarvis/interno/ricorda":
                fiducia = j.ricorda(str(d.get("fatto", ""))[:400])
                return self.manda(200, {"fiducia": fiducia})
            if u.path == "/jarvis/interno/dimentica":
                return self.manda(200, {"tolto": j.memoria.togli_id(int(d.get("numero", 0)))})
            if u.path == "/jarvis/memoria":
                if "aggiungi" in d:
                    j.ricorda(str(d["aggiungi"])[:400])
                if "togli" in d:
                    j.memoria.togli_id(int(d["togli"]))
                if d.get("azzera"):
                    j.memoria.azzera()
                if "togli_permesso" in d:
                    j.permessi.togli(str(d["togli_permesso"]))
                if d.get("azzera_permessi"):
                    j.permessi.azzera()
                return self.manda(200, {"attiva": bool(j.imp.get("memoria", True)), "fatti": j.memoria.elenco(),
                                        "permessi": j.permessi.elenco()})
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
            if u.path == "/jarvis/interno/visore":
                return self.manda(200, j.mostra_nel_visore(d))
            if u.path == "/jarvis/interno/guarda":
                return self.manda(200, j.guarda(str(d.get("motivo", "")), str(d.get("dispositivo", "auto"))))
            if u.path == "/jarvis/domanda/chiudi":
                return self.manda(200, {"ok": j.chiudi_domanda()})
            if u.path == "/jarvis/visore/sessione":
                j.imposta_visore(bool(d.get("immersivo")))
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/sentinella":
                return self.manda(200, j.sentinella_evento(d))
            if u.path == "/jarvis/file/apri":
                try:
                    visore.apri_sul_pc(str(d.get("percorso", "")))
                except (PermissionError, OSError) as e:
                    return self.manda(403, {"errore": str(e)})
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/finestre/azione":
                try:
                    esito = visore.azione(int(d.get("hwnd") or 0), str(d.get("tipo", "")), d.get("x", 0.5), d.get("y", 0.5),
                                          int(d.get("scatti") or 0), str(d.get("testo") or ""), str(d.get("tasti") or ""))
                except LookupError as e:
                    return self.manda(404, {"errore": str(e)})
                return self.manda(200, {"ok": True, "esito": esito})
            if u.path == "/jarvis/annuncia":
                testo = str(d.get("testo", "")).strip()[:300]
                if not testo:
                    raise ValueError("testo vuoto")
                log("annuncio da un'altra sessione:", testo)
                j.di_subito(testo)
                return self.manda(200, {"ok": True})
            if u.path == "/jarvis/domanda":
                domanda = str(d.get("domanda", "")).strip()[:300]
                if not domanda:
                    raise ValueError("domanda vuota")
                attesa = max(10, min(600, int(d.get("attesa", 120))))
                return self.manda(200, {"risposta": j.chiedi_risposta(domanda, d.get("opzioni") or [], attesa)})
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
                j.conferma_dal_pannello(d.get("id"), d.get("ok"), bool(d.get("sempre")), d.get("testo"))
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
        visore_qui = (parse_qs(urlparse(self.path).query).get("xr") or ["0"])[0] == "1"
        if visore_qui:
            self.j.visori += 1
            self.j.evento(visore_collegato=True)
            log("visore collegato:", self.client_address[0])
        try:
            self._flusso()
        finally:
            if visore_qui:
                self.j.visori -= 1
                log("visore scollegato")
                if not self.j.visori:
                    self.j.imposta_visore(False)

    def _flusso(self):
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

    def altoparlante(self, visore=False):
        """Voce di Jarvis in trame (trame.py) per il tablet o, con visore=True, per lo speaker del Quest (Jarvis XR:
        tipo di contenuto proprio, così il pannello lo inoltra man mano e non a blocchi)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-jarvis-audio" if visore else "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        chi = "visore" if visore else "tablet"
        log(chi, "collegato (altoparlante):", self.client_address[0])

        def scrivi(dati):
            self.wfile.write(dati)
            self.wfile.flush()
        try:
            (self.j.visore_audio if visore else self.j.tablet).servi_altoparlante(scrivi)
        except (ConnectionError, OSError):
            pass
        log(chi, "scollegato (altoparlante)")

    def diretta(self, q):
        """Schermo o finestra in diretta per il cinema di Jarvis XR: [4 byte di lunghezza][JPEG], uno dopo l'altro,
        finché il visore resta collegato."""
        a = lambda k, d: (q.get(k) or [d])[0]   # noqa: E731
        try:
            fotogrammi = visore.diretta_isolata(int(a("numero", "0")), int(a("hwnd", "0")), max(320, min(1920, int(a("lato", "1280")))),
                                        int(a("fps", "30")))
            primo = next(fotogrammi)
        except (LookupError, ValueError, OSError) as e:
            return self.manda(404, {"errore": str(e)})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-jarvis-fotogrammi")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        log("diretta per il visore iniziata:", dict((k, v[0]) for k, v in q.items() if k != "t"))
        n = 0
        try:
            for jpeg in itertools.chain([primo], fotogrammi):
                self.wfile.write(len(jpeg).to_bytes(4, "big") + jpeg)
                self.wfile.flush()
                n += 1
        except (ConnectionError, OSError, LookupError):
            pass
        finally:
            fotogrammi.close()
            log("diretta per il visore finita:", n, "fotogrammi")

    def foto_dal_dispositivo(self, q):
        """La foto chiesta da guarda(): JPEG nel corpo (visore o tablet), ?id=…; ?errore=… se non è riuscita."""
        id_ = (q.get("id") or [""])[0]
        errore = (q.get("errore") or [""])[0][:200]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 12 * 1024 * 1024:
                return self.manda(400, {"errore": "foto troppo grande"})
            dati = self.rfile.read(n) if n else b""
        except (ValueError, OSError) as e:
            return self.manda(400, {"errore": str(e)})
        if dati and not dati.startswith(b"\xff\xd8"):
            return self.manda(400, {"errore": "non è un JPEG"})
        if not self.j.foto_arrivata(id_, dati, errore):
            return self.manda(404, {"errore": "nessuna foto chiesta con questo id"})
        return self.manda(200, {"ok": True})

    def voce_intera(self):
        """Una frase intera già registrata (PCM 16 kHz, 16 bit, mono): il "tieni premuto e parla" dell'app per Quest
        3. Si trascrive sul PC come quella del tablet; se c'è una conferma o una domanda in corso, è la risposta."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 3200 <= n <= 60 * 32000:
                return self.manda(400, {"errore": "audio vuoto o più lungo di un minuto"})
            pcm = np.frombuffer(self.rfile.read(n), dtype=np.int16).copy()
        except (ValueError, OSError) as e:
            return self.manda(400, {"errore": str(e)})
        j = self.j
        if j.conferma:
            threading.Thread(target=j._capisci_conferma, args=(pcm,), daemon=True).start()
        else:
            j.uscita.riprendi()
            j.conversazione = True
            if j.stato not in ("lavoro",):
                j.imposta_stato("penso")
            j.richieste.put(("voce", pcm))
        return self.manda(200, {"ok": True})

    def microfono(self):
        """Corpo a pezzi (Transfer-Encoding: chunked) con PCM 16 kHz, finché il tablet (o il Quest, ?da=visore) lo
        manda."""
        self.close_connection = True
        q = parse_qs(urlparse(self.path).query)
        visore = (q.get("da") or [""])[0] == "visore"
        dest = self.j.mic_visore if visore else self.j.tablet
        if not visore:
            self.j.tablet.aec = (q.get("aec") or ["0"])[0] == "1"
        chi = "del visore" if visore else "del tablet"
        log(f"microfono {chi} collegato:", self.client_address[0], "- cancellazione eco:",
            "sul Quest" if visore else "sì" if self.j.tablet.aec else "no")
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
                    dest.dal_microfono(dati)
            else:
                while True:
                    dati = self.rfile.read1(4096) if hasattr(self.rfile, "read1") else self.rfile.read(4096)
                    if not dati:
                        break
                    dest.dal_microfono(dati)
        except (ConnectionError, OSError, ValueError):
            pass
        log(f"microfono {chi} scollegato")
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
    # l'attività pianificata parte a priorità "sotto il normale": con un'installazione pesante a pieno carico
    # (2026-11-24) Jarvis rispondeva in secondi e caricava la voce in minuti. Normale, ereditata da Claude Code
    k32.SetPriorityClass(ctypes.c_void_p(k32.GetCurrentProcess()), 0x20)   # NORMAL_PRIORITY_CLASS
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
