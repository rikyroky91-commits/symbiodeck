"""Annullare le modifiche ai file fatte da Jarvis (idea da jarvis-fr): "annulla l'ultima modifica".

La guardia (hook PreToolUse) chiama `registra` prima di ogni Write/Edit/NotebookEdit dell'esecutore: se il file
esiste se ne fa una copia, se non esiste si annota che è nuovo. `ultimo_gruppo` raccoglie le modifiche dell'ultimo
lavoro (stessa sessione, senza pause di oltre 10 minuti) e `ripristina` le rimette com'erano: i file modificati
tornano alla copia, quelli nuovi si spostano in jarvis-dati/annulla/tolti (mai cancellati). Le cancellazioni con
PowerShell non passano da qui: l'esecutore le fa nel Cestino (vedi esecutore.md), da cui si recuperano.
Solo libreria standard: la guardia parte a ogni strumento e deve essere veloce.
"""
import json
import os
import shutil
import time
from pathlib import Path

MASSIMO_BYTE = 20 * 2**20     # file più grandi non si copiano (si annota soltanto)
PAUSA = 10 * 60               # oltre questa pausa tra due modifiche, comincia un altro lavoro
TENERE = 300                  # voci del registro tenute


def _registro(cartella):
    return Path(cartella) / "registro.jsonl"


def registra(cartella, percorso, sessione=""):
    cartella = Path(cartella)
    try:
        cartella.mkdir(parents=True, exist_ok=True)
        p = Path(os.path.expandvars(str(percorso))).resolve()
        voce = {"t": time.time(), "sessione": sessione, "percorso": str(p), "copia": None, "nuovo": not p.exists()}
        if p.is_file():
            if p.stat().st_size <= MASSIMO_BYTE:
                copia = cartella / "copie" / f"{time.time_ns()}-{p.name}"
                copia.parent.mkdir(exist_ok=True)
                shutil.copy2(p, copia)
                voce["copia"] = str(copia)
            else:
                voce["troppo_grande"] = True
        elif p.exists():
            return None   # una cartella: non si gestisce
        with open(_registro(cartella), "a", encoding="utf-8") as f:
            f.write(json.dumps(voce, ensure_ascii=False) + "\n")
        _pota(cartella)
        return voce
    except OSError:
        return None


def _leggi(cartella):
    try:
        righe = _registro(cartella).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    voci = []
    for r in righe:
        try:
            voci.append(json.loads(r))
        except ValueError:
            continue
    return voci


def _pota(cartella):
    voci = _leggi(cartella)
    if len(voci) <= TENERE + 50:
        return
    vecchie, tenute = voci[:-TENERE], voci[-TENERE:]
    for v in vecchie:
        if v.get("copia"):
            try:
                os.remove(v["copia"])
            except OSError:
                pass
    _registro(cartella).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in tenute),
                                   encoding="utf-8")


def ultimo_gruppo(cartella):
    """Le modifiche dell'ultimo lavoro non ancora annullate, dalla più recente."""
    voci = [v for v in _leggi(cartella) if not v.get("annullata")]
    if not voci:
        return []
    gruppo = [voci[-1]]
    for v in reversed(voci[:-1]):
        if v.get("sessione") != gruppo[-1].get("sessione") or gruppo[-1]["t"] - v["t"] > PAUSA:
            break
        gruppo.append(v)
    return gruppo


def ripristina(cartella, gruppo):
    """Rimette com'erano i file del gruppo. Restituisce (rimessi, tolti, non_riusciti) come elenchi di nomi."""
    cartella = Path(cartella)
    esito, fatti = {}, set()   # percorso -> "rimesso" | "tolto" | "fallito" (vale l'ultima, cioè la più vecchia)
    # dalla più recente alla più vecchia: alla fine resta la copia più vecchia, cioè com'era prima del lavoro
    for v in gruppo:
        p = Path(v["percorso"])
        try:
            if v.get("nuovo"):
                if p.exists():
                    dest = cartella / "tolti" / f"{time.time_ns()}-{p.name}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), dest)
                esito[str(p)] = "tolto"
            elif v.get("copia") and os.path.exists(v["copia"]):
                shutil.copy2(v["copia"], p)
                esito[str(p)] = "rimesso"
            else:
                esito[str(p)] = "fallito"
            fatti.add((v["t"], v["percorso"]))
        except OSError:
            esito[str(p)] = "fallito"
    voci = _leggi(cartella)
    for v in voci:
        if (v["t"], v["percorso"]) in fatti:
            v["annullata"] = True
    _registro(cartella).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in voci), encoding="utf-8")
    nomi = lambda tipo: [Path(k).name for k, e in esito.items() if e == tipo]  # noqa: E731
    return nomi("rimesso"), nomi("tolto"), nomi("fallito")
