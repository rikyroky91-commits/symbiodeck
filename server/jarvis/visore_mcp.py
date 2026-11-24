"""Server MCP "visore": la voce di Jarvis, l'esecutore e il cervello locale aprono cose nel visore Quest 3 (web/xr.js)
mentre l'utente lo indossa. "Ehi Jarvis, aprimi la cartella delle foto sul desktop" -> visore_cartella("foto",
"desktop"): Jarvis trova la cartella sul PC e il visore la apre davanti all'utente, con le foto in anteprima.

Lo avviano voce, esecutore e cervello locale (vedi cervello.py ed esecutore.py, --mcp-config):  python visore_mcp.py
"""
import base64
import json
import os
import urllib.request

from mcp.server.mcpserver import Image, MCPServer

PORTA = os.environ.get("JARVIS_PORTA", "47802")
CHIAVE = os.environ.get("JARVIS_INTERNO", "")

server = MCPServer("visore", instructions=(
    "Mostrare cose nel visore Meta Quest 3 dell'utente (app Jarvis XR): cartelle con le foto in anteprima, singole "
    "foto, video o testi, le finestre aperte sul PC in diretta (che l'utente può usare toccandole) e lo schermo "
    "intero. Usalo quando l'utente ha il visore e chiede di vedere, aprire o mostrare qualcosa. Se risponde che il "
    "visore non è collegato, apri la cosa sul PC come al solito. Con guarda_con_telecamera vedi invece quello che "
    "l'utente ha davanti, dalla telecamera del visore o del tablet, sempre col suo permesso."))


def _visore(corpo):
    r = urllib.request.Request(f"http://127.0.0.1:{PORTA}/jarvis/interno/visore", data=json.dumps(corpo).encode("utf-8"),
                               headers={"X-Interno": CHIAVE, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=20) as risposta:
            return json.loads(risposta.read()).get("messaggio", "fatto")
    except OSError as e:
        return f"Jarvis non raggiungibile: {e}"


@server.tool()
def visore_cartella(nome: str, dove: str = "") -> str:
    """Apre nel visore una cartella del PC, con foto e video in anteprima. `nome`: com'è stata detta ("foto",
    "vacanze 2024", "screenshot") oppure il percorso completo; `dove`: desktop, documenti, immagini, download,
    video, musica (vuoto: cerca dappertutto). Esempio: "la cartella delle foto sul desktop" -> nome="foto",
    dove="desktop". Se non la trova, fatti dare il percorso dall'esecutore e richiamalo con quello."""
    return _visore({"cosa": "cartella", "nome": nome, "dove": dove})


@server.tool()
def visore_file(percorso: str) -> str:
    """Mostra nel visore un file del PC dal percorso completo: foto (grande, sfogliabile), video, testo."""
    return _visore({"cosa": "file", "percorso": percorso})


@server.tool()
def visore_finestra(titolo: str) -> str:
    """Porta nel visore, in diretta, una finestra aperta sul PC (parte del titolo o nome del programma, es.
    "Chrome", "Esplora file", "Spotify"). L'utente la vede e la usa toccandola."""
    return _visore({"cosa": "finestra", "titolo": titolo})


@server.tool()
def visore_schermo(numero: int = 1) -> str:
    """Mostra nel visore lo schermo intero del PC (1 = principale), in diretta."""
    return _visore({"cosa": "schermo", "numero": numero})


@server.tool()
def visore_elenco_finestre() -> str:
    """Apre nel visore l'elenco delle finestre aperte sul PC, tra cui l'utente sceglie toccando."""
    return _visore({"cosa": "finestre"})


@server.tool()
def visore_chiudi() -> str:
    """Chiude nel visore cartelle, foto, video e finestre aperte (i pannelli fissi restano)."""
    return _visore({"cosa": "chiudi"})


@server.tool()
def guarda_con_telecamera(motivo: str, dispositivo: str = "auto"):
    """Fa una foto con la telecamera del visore Quest (se l'utente lo indossa) o del tablet e te la mostra, per
    rispondere a domande su cose che l'utente ha davanti: "guarda questa scheda madre e dimmi che modello è",
    "che pianta è questa?", "leggimi questa etichetta". Jarvis chiede ogni volta il permesso all'utente prima di
    accendere la telecamera. `motivo`: in poche parole cosa guardi (viene detto all'utente nella richiesta di
    permesso), es. "riconoscere il modello della scheda madre". `dispositivo`: auto, visore o tablet.
    Usala solo quando l'utente ti chiede di guardare qualcosa; se rifiuta il permesso non riprovare."""
    r = urllib.request.Request(f"http://127.0.0.1:{PORTA}/jarvis/interno/guarda",
                               data=json.dumps({"motivo": motivo, "dispositivo": dispositivo}).encode("utf-8"),
                               headers={"X-Interno": CHIAVE, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=180) as risposta:   # il permesso può aspettare fino a 90 s
            d = json.loads(risposta.read())
    except OSError as e:
        return f"Jarvis non raggiungibile: {e}"
    if not d.get("ok"):
        return d.get("messaggio") or "Foto non disponibile."
    return [d.get("messaggio", "Foto"), Image(data=base64.b64decode(d["jpeg"]), format="jpeg")]


if __name__ == "__main__":
    server.run("stdio")
