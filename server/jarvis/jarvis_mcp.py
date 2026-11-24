"""Server MCP "jarvis" per le ALTRE sessioni di Claude Code sul PC (quelle che apri tu nel terminale o nell'app):
possono parlarti con la voce di Jarvis e chiederti una decisione a voce mentre sei lontano dalla tastiera.
Idea da jarvis-desktop (lopadova: jarvis_speak, jarvis_ask_user).

    jarvis_parla(testo)                    "Ho finito la build, tutti i test passano."
    jarvis_chiedi(domanda, opzioni)        "Pubblico la versione 2.1? Sì o no" -> "sì" (o quello che dici)
    jarvis_promemoria(quando, testo)       un promemoria che Jarvis dirà a quell'ora

Si registra una volta, per tutte le cartelle:
    claude mcp add --scope user jarvis -- python "<cartella pc-panel>\\server\\jarvis\\jarvis_mcp.py"
Usa la chiave del pannello (server/config.json) e Jarvis sulla porta 47802. Solo libreria standard + mcp.
"""
import json
import os
import urllib.request
from pathlib import Path

from mcp.server.mcpserver import MCPServer

SERVER = Path(__file__).resolve().parent.parent
PORTA = os.environ.get("JARVIS_PORTA", "47802")

server = MCPServer("jarvis", instructions=(
    "Jarvis è l'assistente vocale del PC: parla dalle casse del tablet o del PC. Usa jarvis_parla per avvisare "
    "l'utente quando finisci un lavoro lungo o succede qualcosa di importante (frasi brevi, in italiano, senza "
    "codice né percorsi lunghi) e jarvis_chiedi quando ti serve una sua decisione e potrebbe non essere davanti "
    "allo schermo. Non usarlo per ogni passo."))


def _chiave():
    try:
        return json.loads((SERVER / "config.json").read_text(encoding="utf-8")).get("token", "")
    except (OSError, ValueError):
        return ""


def _jarvis(percorso, corpo, attesa=15):
    r = urllib.request.Request(f"http://127.0.0.1:{PORTA}{percorso}", data=json.dumps(corpo).encode("utf-8"),
                               headers={"X-Token": _chiave(), "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=attesa) as risposta:
        return json.loads(risposta.read())


@server.tool()
def jarvis_parla(testo: str) -> str:
    """Fa dire a Jarvis una frase breve ad alta voce (massimo 300 caratteri), in italiano parlato."""
    try:
        _jarvis("/jarvis/annuncia", {"testo": testo[:300]})
        return "Detto."
    except OSError as e:
        return f"Jarvis non raggiungibile: {e}"


@server.tool()
def jarvis_chiedi(domanda: str, opzioni: list[str] | None = None, attesa_secondi: int = 120) -> str:
    """Fa una domanda all'utente a voce e aspetta la risposta (detta, o toccata sul tablet o sul visore). Con
    `opzioni` (al massimo 4, brevi) le propone. Restituisce la risposta, oppure dice che non ha risposto."""
    attesa = max(10, min(600, int(attesa_secondi)))
    try:
        risposta = _jarvis("/jarvis/domanda", {"domanda": domanda[:300], "opzioni": (opzioni or [])[:4],
                                               "attesa": attesa}, attesa + 40).get("risposta", "")
    except OSError as e:
        return f"Jarvis non raggiungibile: {e}"
    return f"L'utente ha risposto: {risposta}" if risposta else "L'utente non ha risposto."


@server.tool()
def jarvis_promemoria(quando: str, testo: str) -> str:
    """Un promemoria che Jarvis dirà a voce: quando = "AAAA-MM-GG HH:MM" (ora locale del PC)."""
    try:
        _jarvis("/jarvis/promemoria-esterno", {"quando": quando, "testo": testo[:200]})
        return f"Promemoria salvato per {quando}."
    except OSError as e:
        return f"Jarvis non raggiungibile: {e}"


if __name__ == "__main__":
    server.run("stdio")
