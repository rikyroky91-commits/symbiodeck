"""LLM locale sulla GPU (llama-server di C:\\Users\\<utente>\\llm, lo stesso del worker): zero token Claude.

Serve a due cose: frasi brevi (gli aggiornamenti "a che punto sono", quando il server è già acceso) e la modalità
emergenza (senza Internet o fuori dai limiti di Claude, vedi emergenza.py)."""
import json
import urllib.request
from pathlib import Path

INDIRIZZO = "http://127.0.0.1:8099"
CARTELLA_LLM = Path.home() / "llm"


def attivo(attesa=1.5):
    """True se llama-server risponde (acceso dal worker o dalla modalità emergenza)."""
    try:
        with urllib.request.urlopen(INDIRIZZO + "/health", timeout=attesa) as r:
            return json.loads(r.read()).get("status") == "ok"
    except (OSError, ValueError):
        return False


def frase(sistema, utente, max_token=80, attesa=15):
    """Una risposta breve dal modello locale (API compatibile OpenAI di llama-server). None se non va."""
    corpo = {"model": "worker", "max_tokens": max_token, "temperature": 0.7,
             "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": utente}]}
    r = urllib.request.Request(INDIRIZZO + "/v1/chat/completions", data=json.dumps(corpo).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=attesa) as risposta:
            testo = json.loads(risposta.read())["choices"][0]["message"]["content"]
    except (OSError, ValueError, KeyError, IndexError):
        return None
    testo = (testo or "").strip().strip('"')
    return testo.splitlines()[0].strip() if testo else None
