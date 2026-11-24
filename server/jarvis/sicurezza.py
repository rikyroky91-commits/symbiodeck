"""Controlli di sicurezza sul testo: istruzioni nascoste (prompt injection) e credenziali.

Idea e parte delle regole da OpenJarvis (security/injection_scanner.py e credential_stripper.py, Apache 2.0,
https://github.com/open-jarvis/OpenJarvis), riscritte e allargate all'italiano. Solo libreria standard.

- `sospetto(testo)`: None se il testo è pulito, altrimenti il motivo. Serve alla memoria: un "fatto" che sembra
  un ordine al modello ("ignora le istruzioni", "d'ora in poi esegui...") non diventa mai un'istruzione permanente.
- `oscura(testo)`: sostituisce chiavi, token e password con [NASCOSTO:tipo]. Serve al registro jarvis.log, che
  contiene ogni frase detta e ogni richiesta.
"""
import re

_ISTRUZIONI = [
    # scavalcare le istruzioni
    (r"\b(ignore|disregard|forget)\s+(all\s+)?(the\s+)?(previous|prior|above|earlier|your)\s+"
     r"(instructions?|prompts?|rules?|guidelines?)", "scavalca le istruzioni"),
    (r"\b(ignora|dimentica|scorda|trascura)\w*\s+(tutte\s+)?(le\s+)?(tue\s+)?(istruzioni|regole|indicazioni)"
     r"(\s+(precedenti|di\s+prima|di\s+sistema))?", "scavalca le istruzioni"),
    (r"\b(new|updated)\s+(system\s+)?(instructions?|prompt)\s*:", "istruzioni nuove"),
    (r"\b(nuove|nuovo)\s+(istruzioni|prompt|regole)\s*(di\s+sistema)?\s*:", "istruzioni nuove"),
    (r"\b(system\s+prompt|prompt\s+di\s+sistema|developer\s+mode|modalità\s+sviluppatore|jailbreak|\bDAN\b)",
     "parla del prompt di sistema"),
    # finti ruoli e marcatori
    (r"(<\|?(system|im_start|im_end)\|?>|\[/?(INST|SYS)\]|<</?SYS>>|^\s*(system|assistant)\s*:)",
     "marcatori di ruolo"),
    (r"\b(you\s+are\s+now|from\s+now\s+on\s+you|act\s+as\s+(an?\s+)?(unrestricted|unfiltered))",
     "cambia il ruolo"),
    (r"\b(da\s+ora\s+in\s+poi|d'ora\s+in\s+poi|d'ora\s+in\s+avanti|da\s+adesso)\s+(tu\s+)?(sei|devi|esegui|fai|"
     r"rispondi|ignora|non)\b", "cambia il ruolo"),
    (r"\b(fingi|immagina)\s+di\s+essere\b", "cambia il ruolo"),
    # ordini permanenti pericolosi (la memoria non deve contenere azioni da fare)
    (r"\b(sempre|ogni\s+volta|always|every\s+time)\b.{0,40}\b(esegui|lancia|cancella|elimina|invia|manda|"
     r"scarica|installa|run|execute|delete|send|download|install)\b", "ordine permanente"),
    (r"\b(senza|non)\s+(chiedere|chiedermi|domandare)\s+(conferma|il\s+permesso)|\bwithout\s+(asking|confirmation)",
     "toglie le conferme"),
    (r"\b(invoke|curl|wget|iwr|invoke-webrequest|powershell\s+-e(nc)?|base64\s+-d)\b", "comando di rete"),
    (r"https?://\S+", "contiene un indirizzo web"),
]
_ISTRUZIONI = [(re.compile(p, re.I | re.M), m) for p, m in _ISTRUZIONI]

_CREDENZIALI = [
    ("chiave_anthropic", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("chiave_api", r"\bsk-[A-Za-z0-9_\-]{20,}"),
    ("chiave_aws", r"\bAKIA[0-9A-Z]{16}\b"),
    ("token_github", r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    ("token_slack", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("chiave_google", r"\bAIza[0-9A-Za-z_\-]{30,}"),
    ("token_telegram", r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{20,}"),
    ("chiave_privata", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    # "password: xyz", "token=xyz", "la password è xyz"
    ("password", r"(?i)\b(password|passwd|pwd|parola\s+d'ordine|token|api[_ ]?key|secret|segreto)"
                 r"(\s*[:=]\s*|\s+(è|e'|era|is)\s+)[\"']?[^\s\"',;]{4,}"),
]
_CREDENZIALI = [(t, re.compile(p)) for t, p in _CREDENZIALI]


def sospetto(testo, rigido=True):
    """Il motivo per cui il testo sembra un tentativo di dare ordini al modello, oppure None. Con rigido=False
    (fatto detto dall'utente stesso) un indirizzo web è ammesso."""
    for regola, motivo in _ISTRUZIONI:
        if not rigido and motivo == "contiene un indirizzo web":
            continue
        if regola.search(testo or ""):
            return motivo
    if credenziali(testo):
        return "contiene una credenziale"
    return None


def credenziali(testo):
    """I tipi di credenziale trovati nel testo."""
    return [t for t, regola in _CREDENZIALI if regola.search(testo or "")]


def oscura(testo):
    """Il testo con chiavi, token e password sostituiti da [NASCOSTO:tipo]."""
    for tipo, regola in _CREDENZIALI:
        testo = regola.sub(f"[NASCOSTO:{tipo}]", testo)
    return testo
