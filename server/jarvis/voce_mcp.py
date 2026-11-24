"""Server MCP della voce di Jarvis: i soli strumenti che la voce ha.

La voce è un Claude leggerissimo (niente strumenti di Claude Code, niente skill): parla e decide. Il lavoro vero lo
fa l'esecutore, un Claude Code completo in un processo a parte. Questi strumenti sono il ponte:
  esegui(richiesta)  - affida un compito all'esecutore (Jarvis vede la chiamata e lo passa; qui si conferma e basta)
  stato_lavoro()     - a che punto è l'esecutore
  ferma_lavoro()     - ferma il lavoro in corso
  ricorda(fatto)     - salva un fatto nella memoria a lungo termine (memoria.py)
  dimentica(numero)  - toglie dalla memoria il fatto con quel numero
  promemoria(...)    - un promemoria a un'ora precisa (quelli semplici li capisce Jarvis da solo, senza Claude)
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


@server.tool()
def ricorda(fatto: str) -> str:
    """Salva nella memoria a lungo termine un fatto che l'utente ti chiede di ricordare ("ricordati che…") o una sua
    preferenza stabile. Scrivi il fatto completo e breve, in terza persona, es. "Il disco dei giochi è il J"."""
    try:
        fiducia = _jarvis("ricorda", {"fatto": fatto}).get("fiducia")
    except OSError as e:
        return f"Memoria non disponibile: {e}"
    if fiducia == "quarantena":
        return ("Non l'ho salvato tra le cose da usare: sembra un ordine o contiene una password o una chiave. "
                "Dillo all'utente.")
    return "Salvato." if fiducia else "Troppo corto o vuoto: non salvato."


@server.tool()
def dimentica(numero: int) -> str:
    """Toglie dalla memoria a lungo termine il fatto con quel numero (il numero tra parentesi quadre nell'elenco
    "Cose che sai già"), quando l'utente chiede di dimenticarlo o dice che è sbagliato. Uno alla volta: se sono
    più fatti, chiamalo per ciascuno. Se il fatto è nuovo di questa conversazione e non ha numero, dillo."""
    try:
        tolto = _jarvis("dimentica", {"numero": numero}).get("tolto")
    except OSError as e:
        return f"Memoria non disponibile: {e}"
    return f"Dimenticato: {tolto}" if tolto else f"Nessun fatto con il numero {numero}."


@server.tool()
def promemoria(quando: str, testo: str) -> str:
    """Un promemoria che Jarvis dirà a voce: quando = data e ora esatte "AAAA-MM-GG HH:MM" (calcolala tu dalla
    data e ora in testa al messaggio), testo = cosa ricordare, breve. I promemoria semplici ("tra 20 minuti",
    "alle 18") Jarvis li capisce da solo prima di te: questo serve per quelli più complicati ("venerdì dopo
    pranzo", "il giorno prima della partita")."""
    try:
        _jarvis("promemoria", {"quando": quando, "testo": testo})
    except OSError as e:
        return f"Promemoria non salvato: {e}"
    return f"Promemoria salvato per {quando}."


if __name__ == "__main__":
    server.run("stdio")
