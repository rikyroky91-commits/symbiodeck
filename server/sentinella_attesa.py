"""Aspetta il prossimo evento della sentinella e lo descrive, poi esce (per la chat delle notifiche sul telefono).

La chat di Claude che manda gli eventi sul telefono (un modello leggero, pochi token per evento) lancia questo script in
background: lo script blocca senza consumare niente finché in Desktop\\Sentinella compare un evento nuovo, aspetta che
foto e clip siano complete, raccoglie gli altri eventi arrivati nei secondi subito dopo e stampa una riga JSON per
evento, poi esce. La chat manda foto, clip e testo così come sono e lo rilancia.

Uso: python server/sentinella_attesa.py [--cartella PERCORSO] [--dopo SECONDI_EPOCH]
  Riga stampata: {"testo": "...", "foto": percorso o null, "clip": percorso o null, "tipo": "...", "cartella": "..."}
Non legge chiavi né scrive niente: solo la cartella degli eventi (e "cartella_sentinella" da config.json se c'è).
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))

ATTESA_CLIP = 45      # s: la clip arriva qualche secondo dopo la foto e può durare fino a 30 s
RAFFICA = 6           # s: gli eventi arrivati subito dopo il primo partono nello stesso messaggio
TESTI = {"movimento": "Movimento davanti al tablet", "rumore": "Rumore vicino al tablet",
         "ferma": "La sentinella si è fermata: il tablet non risponde"}


def cartella_eventi(forzata=None):
    if forzata:
        return Path(forzata)
    try:
        cfg = json.loads((QUI / "config.json").read_text(encoding="utf-8"))
        if cfg.get("cartella_sentinella"):
            return Path(cfg["cartella_sentinella"])
    except (OSError, ValueError):
        pass
    from sentinella import cartella_desktop
    return cartella_desktop()


def eventi(radice):
    """Le cartelle evento (giorno/evento) con evento.json."""
    if not radice.is_dir():
        return []
    return [e for g in radice.iterdir() if g.is_dir() for e in g.iterdir() if (e / "evento.json").is_file()]


def stabile(f, secondi=3.0):
    """Il file esiste e non cresce più da [secondi]."""
    try:
        a = f.stat()
    except OSError:
        return False
    return time.time() - a.st_mtime >= secondi


def descrivi(cartella):
    try:
        ev = json.loads((cartella / "evento.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ev = {}
    tipo = str(ev.get("tipo") or cartella.name.split(" ")[1] if " " in cartella.name else "evento")
    ora = ev.get("ora")
    quando = datetime.fromtimestamp(ora).strftime("%H:%M") if isinstance(ora, (int, float)) else cartella.name[:5].replace(".", ":")
    testo = f"🛡 {TESTI.get(tipo, tipo.capitalize())} alle {quando}"
    if ev.get("ritardo", 0) and ev.get("ritardo", 0) > 120:
        testo += " (arrivato in ritardo: il PC era spento o irraggiungibile)"
    foto, clip = cartella / "foto.jpg", cartella / "clip.mp4"
    return {"testo": testo, "tipo": tipo, "cartella": str(cartella),
            "foto": str(foto) if foto.is_file() else None, "clip": str(clip) if clip.is_file() else None}


def completa(cartella, limite):
    """Aspetta foto e clip ferme (al massimo fino a [limite]); gli eventi "ferma" non hanno file."""
    while time.time() < limite:
        foto, clip = cartella / "foto.jpg", cartella / "clip.mp4"
        if (cartella / "evento.json").is_file() and "ferma" in cartella.name:
            return
        if foto.is_file() and clip.is_file() and stabile(clip):
            return
        time.sleep(1)


def aspetta(radice, dopo, passo=2.0, raffica=RAFFICA, attesa_clip=ATTESA_CLIP):
    visti = {e for e in eventi(radice) if e.stat().st_mtime <= dopo} if dopo else set(eventi(radice))
    while True:
        nuovi = [e for e in eventi(radice) if e not in visti]
        if nuovi:
            time.sleep(raffica)
            nuovi = sorted((e for e in eventi(radice) if e not in visti), key=lambda e: e.stat().st_mtime)
            limite = time.time() + attesa_clip
            for e in nuovi:
                completa(e, limite)
            return [descrivi(e) for e in nuovi]
        time.sleep(passo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cartella")
    ap.add_argument("--dopo", type=float, default=0.0, help="eventi più nuovi di questo istante (epoch); 0 = da adesso")
    a = ap.parse_args()
    for r in aspetta(cartella_eventi(a.cartella), a.dopo):
        print(json.dumps(r, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
