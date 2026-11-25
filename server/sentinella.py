"""Modalità sentinella: il tablet fa da telecamera di sicurezza (telecamera davanti e microfono) e, quando vede un
movimento o sente un rumore, manda qui foto, fotogrammi e audio. Tutto resta sul PC, in server/sentinella-dati/
(fuori da git): niente servizi esterni.

Il tablet decide da solo (Sentinella.java); il PC:
  - conserva gli eventi: una cartella per evento con evento.json, foto.jpg, clip-NN.jpg, audio.wav;
  - tiene le impostazioni (config.json, chiave "sentinella") e le manda al tablet nel flusso del pannello;
  - passa al tablet i comandi "attiva/disattiva" (dal PC, da Jarvis a voce) nel flusso, con un numero che cresce;
  - sa lo stato che il tablet gli dice (attiva, in armamento, microfono usato o no e perché).
Solo libreria standard: si prova senza Windows (tests/test_sentinella.py).
"""
import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

PREDEFINITE = {
    "movimento": True,        # scatta con il movimento davanti alla telecamera
    "sens_movimento": 6,      # 1 (solo movimenti grandi) … 10 (anche piccoli)
    "rumore": True,           # scatta con un rumore
    "sens_rumore": 5,         # 1 (solo rumori forti) … 10 (anche deboli)
    "armamento": 20,          # secondi tra l'accensione e l'inizio della sorveglianza (per uscire dalla stanza)
    "pausa": 20,              # secondi minimi tra un evento e il successivo
    "schermo": "attenuato",   # "attenuato" (nero con l'indicatore, luce al minimo) o "spento"
    "voce": True,             # Jarvis lo dice a voce
    "conserva_giorni": 30,    # gli eventi più vecchi si cancellano
}
LIMITI = {"sens_movimento": (1, 10), "sens_rumore": (1, 10), "armamento": (0, 300), "pausa": (5, 600),
          "conserva_giorni": (1, 365)}
SCELTE = {"schermo": ("attenuato", "spento")}

ID = re.compile(r"^\d{8}-\d{6}-(movimento|rumore)-[0-9a-f]{4}$")
NOME = re.compile(r"^(foto|clip-\d{2})\.jpg$|^audio\.wav$")
MAX_FILE = {".jpg": 3 * 2**20, ".wav": 4 * 2**20}
MAX_FILE_EVENTO = 48            # foto + fotogrammi + audio
COMANDO_VALIDO = 90             # secondi: un comando vecchio non si applica (es. al riavvio del tablet)
STATO_VECCHIO = 120             # secondi senza notizie dal tablet: stato sconosciuto


def impostazioni(cfg):
    """Le impostazioni della sentinella, con i valori predefiniti per quelle mancanti."""
    imp = dict(PREDEFINITE)
    imp.update({k: v for k, v in (cfg.get("sentinella") or {}).items() if k in PREDEFINITE})
    return imp


def correggi(nuove, attuali):
    """Valida e unisce le impostazioni nuove a quelle attuali. ValueError se un valore non va."""
    imp = dict(attuali)
    for k, v in (nuove or {}).items():
        if k not in PREDEFINITE:
            continue
        tipo = type(PREDEFINITE[k])
        if tipo is bool:
            v = bool(v)
        elif tipo is int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"valore non valido per {k}: {v!r}")
            lo, hi = LIMITI[k]
            v = max(lo, min(hi, v))
        else:
            v = str(v)
            if k in SCELTE and v not in SCELTE[k]:
                raise ValueError(f"valore non valido per {k}: {v!r}")
        imp[k] = v
    return imp


def _jpeg(dati):
    return len(dati) > 4 and dati[:3] == b"\xff\xd8\xff"


def _wav(dati):
    return len(dati) >= 44 and dati[:4] == b"RIFF" and dati[8:12] == b"WAVE"


class Sentinella:
    def __init__(self, cartella, cfg, salva_config=None, log=print, avvisa=None):
        """cartella: dove stanno gli eventi. cfg: la config del pannello (si legge e si scrive "sentinella").
        avvisa(evento): chiamata una volta per ogni evento nuovo (notifiche, Jarvis), in un thread a parte."""
        self.cartella = Path(cartella)
        self.cfg = cfg
        self.salva_config = salva_config or (lambda c: None)
        self.log = log
        self.avvisa = avvisa
        self.blocco = threading.Lock()
        self.avvio = secrets.token_hex(3)   # i numeri dei comandi ripartono a ogni avvio del server
        self.n_comando = 0
        self.comando = None                 # {"id", "attiva", "t", "da"}
        self.tablet = None                  # ultimo stato detto dal tablet, con "ricevuto"
        self.ultimo_evento = None
        self.non_visti = 0
        self.cambiato = None                # chiamata quando cambia qualcosa che il flusso deve mandare subito
        self._conta_non_visti()

    # ------------------------------------------------------------ impostazioni e comandi
    def impostazioni(self):
        return impostazioni(self.cfg)

    def cambia_impostazioni(self, nuove):
        imp = correggi(nuove, self.impostazioni())
        self.cfg["sentinella"] = imp
        self.salva_config(self.cfg)
        self.log("sentinella: impostazioni", json.dumps(imp, ensure_ascii=False))
        self._cambiato()
        return imp

    def comanda(self, attiva, da="pc"):
        """Chiede al tablet di accendere o spegnere la sentinella (arriva nel flusso del pannello)."""
        with self.blocco:
            self.n_comando += 1
            self.comando = {"id": f"{self.avvio}-{self.n_comando}", "attiva": bool(attiva), "t": time.time(), "da": da}
        self.log("sentinella:", "attivazione" if attiva else "disattivazione", "chiesta da", da)
        self._cambiato()
        return dict(self.comando)

    def stato_dal_tablet(self, d, ip=""):
        prima = self.tablet or {}
        s = {k: d.get(k) for k in ("attiva", "armata", "armamento", "rumore", "perche_rumore", "telecamera",
                                    "in_coda", "visore", "livelli") if k in d}
        s["attiva"] = bool(s.get("attiva"))
        s["ricevuto"] = time.time()
        s["ip"] = ip
        self.tablet = s
        if prima.get("attiva") != s["attiva"] or prima.get("armata") != s.get("armata") \
                or prima.get("rumore") != s.get("rumore"):
            self.log("sentinella: tablet", "attiva" if s["attiva"] else "spenta",
                     "(sorveglia)" if s.get("armata") else "(in armamento)" if s["attiva"] else "",
                     "microfono: " + ("sì" if s.get("rumore") else f"no ({s.get('perche_rumore') or '-'})")
                     if s["attiva"] else "")
            self._cambiato()
        return {"ok": True}

    def stato_tablet(self):
        t = self.tablet
        if not t or time.time() - t["ricevuto"] > STATO_VECCHIO:
            return None
        return dict(t, secondi_fa=round(time.time() - t["ricevuto"]))

    def per_flusso(self):
        """La parte del flusso del pannello (ogni secondo) che riguarda la sentinella: impostazioni per il tablet,
        comando in corso, stato del tablet e ultimo evento (per l'avviso sulla pagina)."""
        d = {"imp": self.impostazioni(), "non_visti": self.non_visti}
        c = self.comando
        if c and time.time() - c["t"] < COMANDO_VALIDO:
            d["comando"] = {"id": c["id"], "attiva": c["attiva"]}
        t = self.stato_tablet()
        if t:
            d["tablet"] = {k: t.get(k) for k in ("attiva", "armata", "armamento", "rumore", "perche_rumore",
                                                  "telecamera", "in_coda")}
        if self.ultimo_evento:
            d["ultimo"] = {k: self.ultimo_evento.get(k) for k in ("id", "tipo", "ora")}
        return d

    def _cambiato(self):
        if self.cambiato:
            try:
                self.cambiato()
            except Exception as e:   # il flusso non deve far cadere la richiesta
                self.log("sentinella: aggiornamento del flusso:", repr(e))

    # ------------------------------------------------------------ eventi
    def ricevi_file(self, id_, nome, dati, tipo="", valore=None, ora=None):
        """Un file di un evento dal tablet. Il primo file di un id crea l'evento (e lo annuncia); gli altri si
        aggiungono. Lo stesso file mandato due volte (il tablet riprova se la risposta si perde) si riscrive.
        Restituisce {"ok", "nuovo"}. ValueError per dati non validi."""
        if not ID.match(id_ or ""):
            raise ValueError("id dell'evento non valido")
        tipo_id = id_.split("-")[2]
        if nome != "evento" and not NOME.match(nome or ""):
            raise ValueError("nome del file non valido")
        if nome != "evento":
            est = Path(nome).suffix
            if len(dati) > MAX_FILE[est]:
                raise ValueError("file troppo grande")
            if est == ".jpg" and not _jpeg(dati):
                raise ValueError("non è un JPEG")
            if est == ".wav" and not _wav(dati):
                raise ValueError("non è un WAV")
        cartella = self.cartella / id_
        nuovo = False
        with self.blocco:
            if not cartella.exists():
                cartella.mkdir(parents=True)
                nuovo = True
                adesso = time.time()
                try:
                    ora = float(ora) / 1000 if ora is not None else adesso
                except (TypeError, ValueError):
                    ora = adesso
                if abs(ora - adesso) > 7 * 86400:   # orologio del tablet sbagliato: vale l'ora del PC
                    ora = adesso
                ev = {"id": id_, "tipo": tipo_id, "ora": ora, "ricevuto": adesso, "valore": valore,
                      "ritardo": round(adesso - ora)}
                (cartella / "evento.json").write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
            elif len(list(cartella.iterdir())) > MAX_FILE_EVENTO:
                raise ValueError("troppi file per questo evento")
            if nome != "evento":
                tmp = cartella / (nome + ".tmp")
                tmp.write_bytes(dati)
                tmp.replace(cartella / nome)
        if nuovo:
            self.non_visti += 1
            self.ultimo_evento = ev
            self.log("sentinella: evento", id_, f"valore {valore}", f"(arrivato {ev['ritardo']} s dopo)"
                     if ev["ritardo"] > 5 else "")
            self._cambiato()
            if self.avvisa:
                threading.Thread(target=self._avvisa, args=(dict(ev),), daemon=True, name="sentinella-avviso").start()
        return {"ok": True, "nuovo": nuovo}

    def _avvisa(self, ev):
        try:
            self.avvisa(ev)
        except Exception as e:
            self.log("sentinella: avviso non riuscito:", repr(e))

    def evento(self, id_):
        if not ID.match(id_ or ""):
            return None
        c = self.cartella / id_
        try:
            ev = json.loads((c / "evento.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        file = sorted(p.name for p in c.iterdir() if NOME.match(p.name))
        ev["foto"] = "foto.jpg" in file
        ev["clip"] = [f for f in file if f.startswith("clip-")]
        ev["audio"] = "audio.wav" in file
        return ev

    def elenco(self, quanti=60):
        if not self.cartella.exists():
            return []
        ids = sorted((p.name for p in self.cartella.iterdir() if p.is_dir() and ID.match(p.name)), reverse=True)
        return [e for e in (self.evento(i) for i in ids[:quanti]) if e]

    def file(self, id_, nome):
        """Il percorso di un file di un evento, o None (niente percorsi fuori dalla cartella)."""
        if not ID.match(id_ or "") or not NOME.match(nome or ""):
            return None
        p = self.cartella / id_ / nome
        return p if p.is_file() else None

    def segna_visti(self):
        self.non_visti = 0
        try:
            self.cartella.mkdir(parents=True, exist_ok=True)
            (self.cartella / "visti.json").write_text(json.dumps({"fino": time.time()}), encoding="utf-8")
        except OSError as e:
            self.log("sentinella: visti non salvati:", repr(e))
        self._cambiato()

    def cancella(self, id_):
        if not ID.match(id_ or ""):
            raise ValueError("id dell'evento non valido")
        c = self.cartella / id_
        if c.is_dir():
            shutil.rmtree(c, ignore_errors=True)
            self.log("sentinella: evento cancellato", id_)
            if self.ultimo_evento and self.ultimo_evento.get("id") == id_:
                self.ultimo_evento = None
            return True
        return False

    def pulisci(self, adesso=None):
        """Cancella gli eventi più vecchi di "conserva_giorni"."""
        if not self.cartella.exists():
            return 0
        limite = (adesso or time.time()) - self.impostazioni()["conserva_giorni"] * 86400
        tolti = 0
        for p in self.cartella.iterdir():
            if not (p.is_dir() and ID.match(p.name)):
                continue
            ev = self.evento(p.name)
            if ev and ev.get("ora", 0) < limite:
                shutil.rmtree(p, ignore_errors=True)
                tolti += 1
        if tolti:
            self.log("sentinella: cancellati", tolti, "eventi vecchi")
        return tolti

    def _conta_non_visti(self):
        """All'avvio del server: gli eventi arrivati dopo l'ultima volta che l'utente li ha guardati."""
        try:
            fino = json.loads((self.cartella / "visti.json").read_text(encoding="utf-8"))["fino"]
        except (OSError, ValueError, KeyError, TypeError):
            fino = 0
        self.non_visti = sum(1 for e in self.elenco(200) if e.get("ricevuto", 0) > fino)

    def riassunto(self):
        """Per Jarvis ("com'è la sentinella?"): stato del tablet ed eventi di oggi."""
        t = self.stato_tablet()
        oggi = time.strftime("%Y%m%d")
        eventi = [e for e in self.elenco(200) if e["id"].startswith(oggi)]
        return {"tablet": t, "oggi": len(eventi), "ultimo": eventi[0] if eventi else None,
                "non_visti": self.non_visti}


def descrivi(ev):
    """La frase dell'avviso: "movimento davanti al tablet alle 14:32"."""
    cosa = "un movimento davanti al tablet" if ev.get("tipo") == "movimento" else "un rumore vicino al tablet"
    return f"{cosa} alle {time.strftime('%H:%M', time.localtime(ev.get('ora') or time.time()))}"
