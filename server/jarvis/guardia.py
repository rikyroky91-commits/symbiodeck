"""Guardia di Jarvis: hook PreToolUse di Claude Code.

Claude Code la esegue prima di ogni strumento (vedi esecutore.py, file di impostazioni generato). Se l'azione
è irreversibile chiede a Jarvis di domandare conferma a voce all'utente e aspetta la risposta:
  sì -> l'azione procede, no / nessuna risposta -> l'azione viene rifiutata e Claude lo sa.
Livelli (impostazione "conferme"): "irreversibili" (predefinito), "sempre" (ogni azione che cambia qualcosa),
"mai". Solo libreria standard: parte a ogni strumento, deve essere veloce.

    python guardia.py   (JSON dell'hook su stdin)
"""
import json
import os
import re
import sys
import urllib.request

# parole brevi (del, rd, format...) contano solo all'inizio di un comando: "Immagini del mare" o
# "Get-Date -Format" non devono chiedere conferma
_INIZIO = r"(?:^|[;&|(\n{]\s*|\bcmd(?:\.exe)?\s+/[ck]\s+|\b(?:sudo|xargs)\s+)"
_COMANDI = [
    # cancellazioni
    (_INIZIO + r"(ri|rm|rmdir|rd|del|erase)\b", "cancellare dei file"),
    (r"\b(remove-item|clear-content|clear-recyclebin)\b|\[(system\.)?io\.(file|directory)\]::delete|\bos\.(remove|unlink|rmdir)\b|\bshutil\.rmtree\b",
     "cancellare dei file"),
    (r"\bremove-\w+", "rimuovere qualcosa"),
    (r"\b(shred|cipher\s+/w|sdelete)\b", "cancellare dei dati in modo definitivo"),
    # spegnimento
    (r"\b(shutdown|stop-computer|restart-computer|logoff|setsuspendstate)\b|powrprof", "spegnere o riavviare il PC"),
    # dischi e sistema
    (_INIZIO + r"format(\.com)?\s+[a-z]:|\b(format-volume|diskpart|clear-disk|initialize-disk|bcdedit|mountvol)\b", "modificare i dischi"),
    (r"\breg(\.exe)?\s+(add|delete|import|restore|load|unload)\b|\b(set|new|remove)-itemproperty\b.*\bhk(lm|cu|cr|u)\b|\bregedit\b",
     "modificare il registro di sistema"),
    (r"\b(set-mppreference|add-mppreference|netsh\s+advfirewall|set-netfirewall\w*|set-executionpolicy|icacls|takeown)\b",
     "cambiare impostazioni di sicurezza"),
    (r"\bsc(\.exe)?\s+(delete|config|stop)\b|\b(stop|set)-service\b|\bschtasks\s+/(delete|change)\b|\bunregister-scheduledtask\b",
     "cambiare servizi o attività di sistema"),
    # programmi
    (r"\b(uninstall-\w+|winget\s+uninstall|choco\s+uninstall|scoop\s+uninstall|msiexec\s+/x)\b|\bpip\s+uninstall\b|\bnpm\s+(uninstall|rm)\s+-g\b",
     "disinstallare un programma"),
    # git
    (r"\bgit\s+push\b", "pubblicare con git push"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s|restore\s|branch\s+-D|rebase|stash\s+(drop|clear))",
     "scartare modifiche con git"),
    # messaggi e soldi
    (r"\b(send-mailmessage|sendmail|smtplib)\b", "inviare un'email"),
    (r"\b(kill|taskkill|stop-process)\b.*(-force|/f)\b", "chiudere a forza un programma"),
]
_COMANDI = [(re.compile(p, re.I), d) for p, d in _COMANDI]

_MCP_PERICOLOSI = re.compile(r"(send|post|publish|reply|delete|remove|trash|create_event|update_event|purchase|buy|pay|"
                             r"transfer|share|invite|archive|merge|push|upload)", re.I)
_SOLO_LETTURA = {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent", "ToolSearch",
                 "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "ListMcpResources", "ReadMcpResource"}
_CARTELLE_SISTEMA = re.compile(r"^[a-z]:\\(windows|program files( \(x86\))?|programdata)\\", re.I)


def valuta(nome, ingresso, livello="irreversibili"):
    """Restituisce la descrizione dell'azione da confermare, oppure None se può procedere da sola."""
    if livello == "mai":
        return None
    i = ingresso or {}
    if nome in ("Bash", "PowerShell"):
        comando = str(i.get("command", ""))
        for regola, descrizione in _COMANDI:
            if regola.search(comando):
                return f"{descrizione}: {_breve(i.get('description') or comando)}"
        return f"eseguire: {_breve(i.get('description') or comando)}" if livello == "sempre" else None
    if nome in ("Write", "Edit", "NotebookEdit"):
        percorso = str(i.get("file_path") or i.get("notebook_path") or "")
        normale = os.path.normpath(os.path.expandvars(percorso)) if percorso else ""
        if _CARTELLE_SISTEMA.match(normale):
            return f"modificare un file di sistema: {percorso}"
        if nome == "Write" and normale and os.path.exists(normale):
            return f"sovrascrivere il file {os.path.basename(normale)}"
        return f"scrivere il file {os.path.basename(normale)}" if livello == "sempre" else None
    if nome.startswith("mcp__"):
        azione = nome.split("__")[-1]
        if _MCP_PERICOLOSI.search(azione):
            return f"usare {nome.split('__')[1]}: {azione.replace('_', ' ')}"
        return None if livello != "sempre" or re.match(r"(get|list|read|search|find|query)", azione, re.I) \
            else f"usare {nome.split('__')[1]}: {azione.replace('_', ' ')}"
    if nome in _SOLO_LETTURA:
        return None
    return f"usare lo strumento {nome}" if livello == "sempre" else None


# azioni per cui "sì, sempre" non si può dire (troppo gravi o troppo larghe): si chiede ogni volta
SEMPRE_VIETATO = ("modificare i dischi", "modificare il registro", "cambiare impostazioni di sicurezza",
                  "spegnere o riavviare", "cancellare dei dati in modo definitivo", "disinstallare",
                  "cambiare servizi", "scartare modifiche", "inviare un'email", "modificare un file di sistema", "usare ")


def impronta(nome, ingresso, descrizione, cartella=""):
    """Chiave stretta dell'azione per le decisioni ricordate: tipo + comando (o percorso) esatto + cartella."""
    i = ingresso or {}
    tipo = descrizione.split(":")[0].strip()
    if nome in ("Bash", "PowerShell"):
        oggetto = " ".join(str(i.get("command", "")).split()).lower()
    elif nome in ("Write", "Edit", "NotebookEdit"):
        oggetto = os.path.normcase(os.path.normpath(os.path.expandvars(
            str(i.get("file_path") or i.get("notebook_path") or ""))))
    else:
        oggetto = nome
    return f"{nome}|{tipo}|{os.path.normcase(cartella or '')}|{oggetto}"[:1000]


def sempre_permesso(descrizione):
    """True se per questa azione l'utente può rispondere "sì, sempre"."""
    return not any(descrizione.startswith(v) for v in SEMPRE_VIETATO)


def _breve(s, n=120):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def chiedi_a_jarvis(descrizione, attesa=60, impronta_azione="", sempre_ok=False):
    """True se l'utente conferma (o l'aveva permesso per sempre). Senza Jarvis raggiungibile: no (più prudente)."""
    porta = os.environ.get("JARVIS_PORTA", "47802")
    chiave = os.environ.get("JARVIS_INTERNO", "")
    richiesta = urllib.request.Request(f"http://127.0.0.1:{porta}/jarvis/interno/conferma",
                                       data=json.dumps({"descrizione": descrizione, "impronta": impronta_azione,
                                                         "sempre_ok": sempre_ok}).encode("utf-8"),
                                       headers={"Content-Type": "application/json", "X-Interno": chiave})
    try:
        with urllib.request.urlopen(richiesta, timeout=attesa) as r:
            return bool(json.loads(r.read()).get("ok"))
    except (OSError, ValueError):
        return False


def _livello():
    """Livello delle conferme dalle impostazioni di Jarvis (letto ogni volta: si cambia dal tablet)."""
    try:
        with open(os.environ.get("JARVIS_CONFIG", ""), encoding="utf-8") as f:
            return json.load(f).get("conferme") or "irreversibili"
    except (OSError, ValueError, AttributeError):
        return "irreversibili"


def _leggi_ingresso():
    # os.read/os.write e non sys.stdin/stdout: con pythonw.exe (nessuna finestra) sys.stdout può essere None
    pezzi = []
    while True:
        p = os.read(0, 65536)
        if not p:
            return b"".join(pezzi)
        pezzi.append(p)


# strumenti che la voce (sessione principale) può usare da sola: tutto il resto lo fa l'esecutore
_DELLA_VOCE = {"Agent", "Task", "ToolSearch", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
               "TaskStop", "SendMessage", "ListAgents"}


def solo_esecutore(dati):
    """Con JARVIS_DELEGA=1 la voce non usa strumenti: i compiti li fa il sotto-agente "esecutore", con il suo
    modello e il suo livello di ragionamento (impostati a parte nel pannello). Restituisce il rifiuto o None."""
    if os.environ.get("JARVIS_DELEGA") != "1" or dati.get("agent_type") or dati.get("agent_id"):
        return None
    if dati.get("tool_name", "") in _DELLA_VOCE:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": ("Tu sei la voce: non usare strumenti. Affida questo compito al sotto-agente "
                                     "esecutore (strumento Agent, subagent_type \"esecutore\") con istruzioni "
                                     "complete, e intanto di' una frase breve all'utente."),
    }}


def main():
    try:
        dati = json.loads(_leggi_ingresso().decode("utf-8") or "{}")
    except (ValueError, OSError):
        return
    rifiuto = solo_esecutore(dati)
    if rifiuto:
        os.write(1, json.dumps(rifiuto, ensure_ascii=False).encode("utf-8"))
        return
    descrizione = valuta(dati.get("tool_name", ""), dati.get("tool_input") or {}, _livello())
    if not descrizione:
        return  # nessuna uscita: decide la modalità dei permessi (qui: procede)
    ok = chiedi_a_jarvis(descrizione, impronta_azione=impronta(dati.get("tool_name", ""), dati.get("tool_input"),
                                                               descrizione, dati.get("cwd", "")),
                         sempre_ok=sempre_permesso(descrizione))
    esito = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if ok else "deny",
        "permissionDecisionReason": ("L'utente ha confermato a voce." if ok else
                                     "L'utente NON ha confermato (ha detto no o non ha risposto): non farlo e "
                                     "non riprovare in altro modo; diglielo in una frase."),
    }}
    os.write(1, json.dumps(esito, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
