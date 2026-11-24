"""Promemoria e timer di Jarvis, capiti e gestiti sul PC senza intelligenza artificiale: subito, gratis, anche in
modalità privata e senza Internet. Idea da jarvis-desktop (lopadova), pi-esp-jarvis e jarvis-fr.

    "ricordami tra 20 minuti di chiamare Marco"      "metti un timer di 5 minuti"
    "avvisami alle 18:30 di uscire"                   "ricordami domani alle 9 di pagare la bolletta"
    "che promemoria ho?"                              "cancella i promemoria"

Le frasi più complicate ("ricordamelo venerdì dopo pranzo") vanno alla voce di Claude, che usa lo strumento
promemoria(quando, testo) con data e ora esatte. File: server/jarvis-dati/promemoria.json (sopravvive ai riavvii:
un promemoria scaduto a PC spento si dice alla riaccensione, con l'ora a cui era).
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_NUMERI = {"un": 1, "uno": 1, "una": 1, "un'": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
           "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13,
           "quattordici": 14, "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19,
           "venti": 20, "venticinque": 25, "trenta": 30, "quaranta": 40, "quarantacinque": 45, "cinquanta": 50,
           "sessanta": 60, "novanta": 90, "cento": 100, "centoventi": 120}
_UNITA = {"second": 1, "minut": 60, "or": 3600}
_N = r"(\d+(?:[.,]\d+)?|" + "|".join(sorted((re.escape(k) for k in _NUMERI), key=len, reverse=True)) + r")"

_RICORDA = re.compile(r"^\W*(?:jarvis\W+)?(?:ricordami|ricordamelo|ricordati\s+di\s+dirmi|avvisami|"
                      r"fammi\s+(?:un\s+)?(?:promemoria|ricordare)|metti\s+(?:un\s+)?promemoria|"
                      r"promemoria)\b(.*)$", re.I | re.S)
_TIMER = re.compile(r"^\W*(?:jarvis\W+)?(?:metti|imposta|fai\s+partire|avvia)\s+(?:un\s+)?(?:timer|"
                    r"conto\s+alla\s+rovescia)\s+(?:di|da)\s+(.*)$", re.I | re.S)
_TRA = re.compile(r"\b(?:tra|fra)\s+" + _N + r"\s*(second[oi]|minut[oi]|or[ae])\b"
                  r"(?:\s+e\s+(mezz[oa]|(?:" + _N[1:-1] + r")\s*(?:minut[oi])?))?", re.I)
_MEZZORA = re.compile(r"\b(?:tra|fra)\s+(?:una\s+)?mezz'?\s*ora\b", re.I)
_ALLE = re.compile(r"\b(oggi|domani|dopodomani)?\s*(?:alle|all'|per\s+le|verso\s+le)\s*(\d{1,2})"
                   r"(?:\s*[:.]\s*(\d{2})|\s+e\s+(\d{1,2}|mezza|un\s+quarto|quarto))?\b"
                   r"(?:\s+(?:del|di)\s+(mattina|pomeriggio|sera|notte))?", re.I)
_DURATA = re.compile(r"^\s*" + _N + r"\s*(second[oi]|minut[oi]|or[ae])\b", re.I)
_ELENCO = re.compile(r"^\W*(?:jarvis\W+)?(?:che|quali)\s+promemoria\s+(?:ho|ci\s+sono)|"
                     r"^\W*(?:jarvis\W+)?(?:elenca|dimmi)\s+(?:i\s+)?promemoria", re.I)
_CANCELLA = re.compile(r"^\W*(?:jarvis\W+)?(?:cancella|togli|elimina|annulla)\s+(?:tutti\s+)?"
                       r"(?:i\s+|il\s+)?(?:promemoria|timer)\b", re.I)


def _numero(t):
    t = t.lower().strip()
    if t in _NUMERI:
        return _NUMERI[t]
    return float(t.replace(",", "."))


def _secondi(n, unita):
    return _numero(n) * next(v for k, v in _UNITA.items() if unita.lower().startswith(k))


def _pulisci(testo):
    testo = re.sub(r"^\W*(?:di|che|a|per|da)\s+", "", testo.strip(" ,.;:!?"), flags=re.I)
    return testo.strip(" ,.;:!?")


def quando_da_frase(frase, adesso=None):
    """(datetime, resto della frase senza l'indicazione di tempo) oppure (None, frase)."""
    adesso = adesso or datetime.now()
    m = _MEZZORA.search(frase)
    if m:
        return adesso + timedelta(minutes=30), frase[:m.start()] + frase[m.end():]
    m = _TRA.search(frase)
    if m:
        secondi = _secondi(m.group(1), m.group(2))
        extra = m.group(3)
        if extra:
            secondi += 30 * 60 if extra.lower().startswith("mezz") else _numero(extra.split()[0]) * 60
        return adesso + timedelta(seconds=secondi), frase[:m.start()] + frase[m.end():]
    m = _ALLE.search(frase)
    if m:
        giorno, ore, minuti, e_minuti, parte = m.groups()
        ore = int(ore)
        if minuti:
            minuti = int(minuti)
        elif e_minuti:
            minuti = {"mezza": 30, "un quarto": 15, "quarto": 15}.get(e_minuti.lower(), None)
            minuti = int(e_minuti) if minuti is None else minuti
        else:
            minuti = 0
        if parte and parte.lower() in ("pomeriggio", "sera") and ore < 12:
            ore += 12
        if ore > 23 or minuti > 59:
            return None, frase
        quando = adesso.replace(hour=ore, minute=minuti, second=0, microsecond=0)
        giorni = {"domani": 1, "dopodomani": 2}.get((giorno or "").lower(), 0)
        quando += timedelta(days=giorni)
        if not giorno and quando <= adesso:
            # "alle 7" detto alle 15: le 19 se ha senso, altrimenti domani
            if ore < 12 and not parte and quando + timedelta(hours=12) > adesso:
                quando += timedelta(hours=12)
            else:
                quando += timedelta(days=1)
        return quando, frase[:m.start()] + frase[m.end():]
    return None, frase


def capisci(frase, adesso=None):
    """Interpreta una richiesta di promemoria. None se la frase non è (chiaramente) un promemoria: allora va a
    Claude. Altrimenti un dict: {"tipo": "nuovo", "quando", "testo"} | {"tipo": "elenco"} | {"tipo": "cancella"}
    | {"tipo": "manca_ora", "testo"}."""
    frase = (frase or "").strip()
    if _ELENCO.search(frase):
        return {"tipo": "elenco"}
    if _CANCELLA.search(frase):
        return {"tipo": "cancella"}
    adesso = adesso or datetime.now()
    m = _TIMER.match(frase)
    if m:
        d = _DURATA.match(m.group(1))
        if not d:
            return None
        durata = _secondi(d.group(1), d.group(2))
        etichetta = f"{d.group(1)} {d.group(2)}"
        return {"tipo": "nuovo", "quando": adesso + timedelta(seconds=durata),
                "testo": f"il timer di {etichetta} è finito"}
    m = _RICORDA.match(frase)
    if not m:
        return None
    quando, resto = quando_da_frase(m.group(1), adesso)
    testo = _pulisci(resto)
    if not quando:
        return None   # "ricordami che il disco dei giochi è J": è memoria, non un promemoria -> a Claude
    return {"tipo": "nuovo", "quando": quando, "testo": testo or "è l'ora che mi avevi chiesto"}


def ora_parlata(quando, adesso=None):
    adesso = adesso or datetime.now()
    giorno = ""
    if quando.date() == (adesso + timedelta(days=1)).date():
        giorno = "domani "
    elif quando.date() != adesso.date():
        giorno = f"il {quando.day}/{quando.month} "
    return f"{giorno}alle {quando.hour}:{quando.minute:02d}"


class Promemoria:
    """Elenco persistente e un thread che li fa scattare. `di(testo)` è la funzione che parla."""

    def __init__(self, file: Path, di=print, log=print, evento=lambda **d: None):
        self.file = Path(file)
        self.di, self.log, self.evento = di, log, evento
        self._blocco = threading.Lock()
        try:
            self.voci = json.loads(self.file.read_text(encoding="utf-8")).get("promemoria", [])
        except (OSError, ValueError, AttributeError):
            self.voci = []

    def _salva(self):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"promemoria": self.voci}, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError as e:
            self.log("promemoria non salvati:", repr(e))

    def aggiungi(self, quando: datetime, testo):
        testo = " ".join(str(testo).split())[:200] or "promemoria"
        with self._blocco:
            v = {"id": max([x["id"] for x in self.voci] + [0]) + 1, "quando": quando.timestamp(), "testo": testo}
            self.voci.append(v)
            self.voci.sort(key=lambda x: x["quando"])
            self._salva()
        self.log("promemoria:", quando.strftime("%Y-%m-%d %H:%M:%S"), testo)
        self.evento(promemoria=self.elenco())
        return v

    def elenco(self):
        return [dict(v, ora=datetime.fromtimestamp(v["quando"]).strftime("%d/%m %H:%M")) for v in self.voci]

    def togli(self, id_=None):
        with self._blocco:
            prima = len(self.voci)
            self.voci = [v for v in self.voci if id_ is not None and v["id"] != id_]
            tolti = prima - len(self.voci)
            self._salva()
        self.evento(promemoria=self.elenco())
        return tolti

    def scaduti(self, adesso=None):
        adesso = (adesso or datetime.now()).timestamp()
        with self._blocco:
            pronti = [v for v in self.voci if v["quando"] <= adesso]
            if pronti:
                self.voci = [v for v in self.voci if v["quando"] > adesso]
                self._salva()
        return pronti

    def ciclo(self):
        while True:
            for v in self.scaduti():
                ritardo = time.time() - v["quando"]
                frase = f"Promemoria: {v['testo']}."
                if ritardo > 120:
                    frase = (f"Promemoria che era per le {datetime.fromtimestamp(v['quando']).strftime('%H:%M')}, "
                             f"mentre ero spento: {v['testo']}.")
                self.log("promemoria scattato:", v["testo"])
                self.evento(promemoria=self.elenco(), avviso=frase)
                self.di(frase)
            time.sleep(1)

    def rispondi(self, richiesta):
        """La frase da dire per una richiesta già capita (vedi capisci)."""
        tipo = richiesta["tipo"]
        if tipo == "elenco":
            if not self.voci:
                return "Non hai promemoria."
            parti = [f"{ora_parlata(datetime.fromtimestamp(v['quando']))}: {v['testo']}" for v in self.voci[:5]]
            altri = f" E altri {len(self.voci) - 5}." if len(self.voci) > 5 else ""
            return "Hai questi promemoria. " + "; ".join(parti) + "." + altri
        if tipo == "cancella":
            n = self.togli()
            return f"Ho cancellato {n} promemoria." if n else "Non c'erano promemoria."
        quando = richiesta["quando"]
        self.aggiungi(quando, richiesta["testo"])
        mancano = quando - datetime.now()
        if mancano < timedelta(hours=1):
            minuti = round(mancano.total_seconds() / 60)
            return f"Fatto, tra {minuti} minut{'o' if minuti == 1 else 'i'}." if minuti >= 1 else "Fatto."
        return f"Fatto, {ora_parlata(quando)}."
