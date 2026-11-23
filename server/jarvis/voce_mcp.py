"""Server MCP della voce di Jarvis: i soli tre strumenti che la voce ha.

La voce è un Claude leggerissimo (niente strumenti di Claude Code, niente skill): parla e decide. Il lavoro vero lo
fa l'esecutore, un Claude Code completo in un processo a parte. Questi strumenti sono il ponte:
  esegui(richiesta)  - affida un compito all'esecutore (Jarvis vede la chiamata e lo passa; qui si conferma e basta)
  stato_lavoro()     - a che punto è l'esecutore
  ferma_lavoro()     - ferma il lavoro in corso
Lo avvia la voce (vedi cervello.py, --mcp-config):  python voce_mcp.py
"""
import json
import os
import urllib.request

from mcp.server.mcpserver import MCPServer

PORTA = os.environ.get("JARVIS_PORTA", "47802")
CHIAVE = os.environ.get("JARVIS_INTERNO", "")

server = MCPServer("jarvis", instructions="Ponte tra la voce di Jarvis e l'esecutore che lavora sul PC.")


def _jarvis(percorso, corpo=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORTA}/jarvis/interno/{percorso}",
                               data=None if corpo is None else json.dumps(corpo).encode("utf-8"),
                               headers={"X-Interno": CHIAVE, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as risposta:
        return json.loads(risposta.read())


@server.tool()
def esegui(richiesta: str) -> str:
    """Affida all'esecutore (Claude Code completo sul PC) un compito: qualsiasi cosa da fare o controllare sul PC o
    in rete. Scrivi istruzioni complete in italiano, con il contesto che serve. Torna subito: l'esito ti arriverà
    più tardi come messaggio, e allora lo racconterai all'utente."""
    return "Affidato all'esecutore: ci sta lavorando. Intanto rispondi all'utente in una frase breve."


@server.tool()
def stato_lavoro() -> str:
    """A che punto è l'esecutore: se sta lavorando, su cosa, da quanto e le ultime cose che ha fatto."""
    try:
        d = _jarvis("lavoro")
    except OSError as e:
        return f"Stato non disponibile: {e}"
    if not d.get("occupato"):
        return "L'esecutore è libero: nessun lavoro in corso."
    return (f"Sta lavorando da {d.get('minuti', 0)} minuti su: {d.get('richiesta', '')}. "
            f"Ultime azioni: {'; '.join(d.get('azioni') or []) or 'sta ragionando'}.")


@server.tool()
def ferma_lavoro() -> str:
    """Ferma subito il lavoro in corso dell'esecutore (quando l'utente dice di lasciar perdere)."""
    try:
        _jarvis("ferma-lavoro", {})
        return "Lavoro fermato."
    except OSError as e:
        return f"Non sono riuscito a fermarlo: {e}"


if __name__ == "__main__":
    server.run("stdio")
