"""Modalità sentinella: il tablet fa da telecamera di sicurezza (telecamera davanti e microfono) e, quando vede un
movimento o sente un rumore, manda qui la foto e la clip video con l'audio. Tutto resta sul PC, nella cartella
Sentinella sul Desktop (Desktop\\Sentinella\\AAAA-MM-GG\\hh.mm.ss tipo (xxxx)\\): niente servizi esterni.

Il tablet decide da solo (Sentinella.java); il PC:
  - conserva gli eventi: una cartella per evento con evento.json, foto.jpg e clip.mp4 (video H.264 con audio AAC,
    da qualche secondo prima a dopo l'evento); se il tablet non riesce a fare il video, clip-NN.jpg e audio.wav;
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
    "sens_movimento": 8,      # 1 (solo movimenti grandi) … 10 (anche piccoli e lontani); 8 = come una Ring
    "rumore": True,           # scatta con un rumore
    "sens_rumore": 5,         # 1 (solo rumori forti) … 10 (anche deboli)
    "armamento": 20,          # secondi tra l'accensione e l'inizio della sorveglianza (per uscire dalla stanza)
    "pausa": 20,              # secondi minimi tra un evento e il successivo
    "schermo": "attenuato",   # "attenuato" (nero con l'indicatore, luce al minimo) o "spento"
    "notte": "auto",          # bianco e nero con più luce: "auto" (quando la stanza è buia), "sempre", "mai"
    "voce": True,             # Jarvis lo dice a voce
    "notifica_pc": True,      # notifica di Windows sul PC (con la foto; senza nessun modello, zero token)
    "conserva_giorni": 30,    # gli eventi più vecchi si cancellano
}
LIMITI = {"sens_movimento": (1, 10), "sens_rumore": (1, 10), "armamento": (0, 300), "pausa": (5, 600),
          "conserva_giorni": (1, 365)}
SCELTE = {"schermo": ("attenuato", "spento"), "notte": ("auto", "sempre", "mai")}

ID = re.compile(r"^\d{8}-\d{6}-(movimento|rumore|ferma)-[0-9a-f]{4}$")
NOME = re.compile(r"^(foto|clip-\d{2})\.jpg$|^audio\.wav$|^clip\.mp4$")
MAX_FILE = {".jpg": 3 * 2**20, ".wav": 4 * 2**20, ".mp4": 40 * 2**20}
CARTELLA_EVENTO = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}) (movimento|rumore|ferma) \(([0-9a-f]{4})\)$")
GIORNO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
MAX_FILE_EVENTO = 48            # foto + fotogrammi + audio
COMANDO_VALIDO = 90             # secondi: un comando vecchio non si applica (es. al riavvio del tablet)
STATO_VECCHIO = 120             # secondi senza notizie dal tablet: stato sconosciuto
SILENZIO_FERMA = 180            # sentinella accesa e tablet muto da 3 minuti (manda lo stato ogni 30 s): "ferma"


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


def _mp4(dati):
    return len(dati) > 12 and dati[4:8] == b"ftyp"


def cartella_desktop():
    """La cartella Sentinella sul Desktop dell'utente (anche se il Desktop è in OneDrive)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        guid = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")   # FOLDERID_Desktop
        buf = ctypes.create_string_buffer(guid.bytes_le)
        percorso = wintypes.LPWSTR()
        if ctypes.windll.shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(percorso)) == 0:
            desktop = Path(percorso.value)
            ctypes.windll.ole32.CoTaskMemFree(percorso)
            return desktop / "Sentinella"
    except (AttributeError, OSError, ValueError):
        pass
    return Path.home() / "Desktop" / "Sentinella"


def sottocartella(id_):
    """20261125-082115-rumore-73b9 -> 2026-11-25/08.21.15 rumore (73b9)"""
    g, o, tipo, coda = id_.split("-")
    return Path(f"{g[:4]}-{g[4:6]}-{g[6:]}") / f"{o[:2]}.{o[2:4]}.{o[4:]} {tipo} ({coda})"


def id_da(giorno, nome):
    a, m = GIORNO.match(giorno), CARTELLA_EVENTO.match(nome)
    if not a or not m:
        return None
    return f"{a[1]}{a[2]}{a[3]}-{m[1]}{m[2]}{m[3]}-{m[4]}-{m[5]}"


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
        self.su_stato = None                # chiamata (stato) quando il tablet accende/spegne o arma la sentinella
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
                                    "in_coda", "visore", "livelli", "notte", "video", "da", "riaperture") if k in d}
        s["attiva"] = bool(s.get("attiva"))
        s["ricevuto"] = time.time()
        if getattr(self, "ferma", None):
            self.log("sentinella: il tablet si fa sentire di nuovo dopo", round(s["ricevuto"] - self.ferma["da"]), "s",
                     "(sorveglia)" if s["attiva"] else "(spenta)")
            self.ferma = None
        s["ip"] = ip
        self.tablet = s
        if prima.get("attiva") != s["attiva"] or prima.get("armata") != s.get("armata") \
                or prima.get("rumore") != s.get("rumore"):
            self.log("sentinella: tablet", "attiva" if s["attiva"] else "spenta",
                     "(sorveglia)" if s.get("armata") else "(in armamento)" if s["attiva"] else "",
                     "microfono: " + ("sì" if s.get("rumore") else f"no ({s.get('perche_rumore') or '-'})")
                     if s["attiva"] else "")
            self._cambiato()
            if self.su_stato:
                threading.Thread(target=self._su_stato, args=(dict(s),), daemon=True).start()
        return {"ok": True}

    def _su_stato(self, s):
        try:
            self.su_stato(s)
        except Exception as e:
            self.log("sentinella: stato non passato:", repr(e))

    def stato_tablet(self):
        t = self.tablet
        if not t or time.time() - t["ricevuto"] > STATO_VECCHIO:
            return None
        return dict(t, secondi_fa=round(time.time() - t["ricevuto"]))

    def controlla_silenzio(self, adesso=None):
        """Ogni 30 s dal pannello: se la sentinella era accesa e il tablet non manda lo stato da SILENZIO_FERMA secondi
        (app chiusa, tablet spento o bloccato, rete persa) si registra un evento "ferma" nella cartella degli eventi e si
        avvisa. Una volta sola finché il tablet non si fa risentire. Spenta di proposito (stato attiva=false) non conta."""
        adesso = adesso or time.time()
        t = self.tablet
        if not t or not t.get("attiva") or getattr(self, "ferma", None) or adesso - t["ricevuto"] < SILENZIO_FERMA:
            return None
        self.ferma = {"da": t["ricevuto"]}
        id_ = time.strftime("%Y%m%d-%H%M%S", time.localtime(adesso)) + "-ferma-" + secrets.token_hex(2)
        ultimo = time.strftime("%H:%M:%S", time.localtime(t["ricevuto"]))
        self.ricevi_file(id_, "evento", b"", valore=round(adesso - t["ricevuto"]), ora=adesso * 1000,
                         nota=f"La sentinella era accesa ma il tablet non manda più lo stato dalle {ultimo}: "
                              "app chiusa, tablet spento o bloccato, oppure rete persa.")
        self.log("sentinella: FERMA, tablet muto dalle", ultimo)
        return id_

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
                                                  "telecamera", "in_coda", "notte")}
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
    def ricevi_file(self, id_, nome, dati, tipo="", valore=None, ora=None, nota=None):
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
            if est == ".mp4" and not _mp4(dati):
                raise ValueError("non è un MP4")
        cartella = self.cartella / sottocartella(id_)
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
                if nota:
                    ev["nota"] = nota
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
        c = self.cartella / sottocartella(id_)
        try:
            ev = json.loads((c / "evento.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        file = sorted(p.name for p in c.iterdir() if NOME.match(p.name))
        ev["foto"] = "foto.jpg" in file
        ev["video"] = "clip.mp4" in file
        ev["clip"] = [f for f in file if f.startswith("clip-")]
        ev["audio"] = "audio.wav" in file
        ev["cartella"] = str(c)
        return ev

    def _ids(self):
        """Gli id di tutti gli eventi, dal più recente."""
        if not self.cartella.exists():
            return []
        ids = []
        for g in self.cartella.iterdir():
            if g.is_dir() and GIORNO.match(g.name):
                ids += [i for i in (id_da(g.name, e.name) for e in g.iterdir() if e.is_dir()) if i]
        return sorted(ids, reverse=True)

    def elenco(self, quanti=60):
        return [e for e in (self.evento(i) for i in self._ids()[:quanti]) if e]

    def file(self, id_, nome):
        """Il percorso di un file di un evento, o None (niente percorsi fuori dalla cartella)."""
        if not ID.match(id_ or "") or not NOME.match(nome or ""):
            return None
        p = self.cartella / sottocartella(id_) / nome
        return p if p.is_file() else None

    def migra(self, vecchia):
        """Gli eventi della prima versione (server/sentinella-dati/<id>/) passano nella cartella nuova."""
        vecchia = Path(vecchia)
        if not vecchia.is_dir() or vecchia.resolve() == self.cartella.resolve():
            return 0
        spostati = 0
        for p in vecchia.iterdir():
            if p.is_dir() and ID.match(p.name):
                dove = self.cartella / sottocartella(p.name)
                if not dove.exists():
                    dove.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dove))
                    spostati += 1
        if (vecchia / "visti.json").exists() and not (self.cartella / "visti.json").exists():
            self.cartella.mkdir(parents=True, exist_ok=True)
            shutil.move(str(vecchia / "visti.json"), str(self.cartella / "visti.json"))
        if spostati:
            self.log("sentinella:", spostati, "eventi spostati in", self.cartella)
            self._conta_non_visti()
        try:
            vecchia.rmdir()
        except OSError:
            pass
        return spostati

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
        c = self.cartella / sottocartella(id_)
        if c.is_dir():
            shutil.rmtree(c, ignore_errors=True)
            try:
                c.parent.rmdir()   # il giorno è rimasto vuoto
            except OSError:
                pass
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
        for i in self._ids():
            ev = self.evento(i)
            if ev and ev.get("ora", 0) < limite:
                self.cancella(i)
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
    if ev.get("tipo") == "ferma":
        return ("che la sentinella del tablet si è fermata: era accesa ma il tablet non si fa sentire "
                f"dalle {time.strftime('%H:%M', time.localtime((ev.get('ora') or time.time()) - (ev.get('valore') or 0)))}")
    cosa = "un movimento davanti al tablet" if ev.get("tipo") == "movimento" else "un rumore vicino al tablet"
    return f"{cosa} alle {time.strftime('%H:%M', time.localtime(ev.get('ora') or time.time()))}"
