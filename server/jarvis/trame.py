"""Parti senza dipendenze di Jarvis: trame audio verso il tablet, testo da dire ad alta voce,
descrizione delle azioni di Claude Code e riconoscimento di "sì"/"no". Provate da tests/test_jarvis_trame.py."""
import re
import struct

# ---------------------------------------------------------------- trame PC -> tablet
# [1 byte tipo][4 byte lunghezza, big endian][dati]
TRAMA_VIVO = 0      # nessun dato: tiene viva la connessione
TRAMA_AUDIO = 1     # PCM 16 bit mono 24 kHz da riprodurre
TRAMA_COMANDO = 2   # JSON UTF-8 (es. {"microfono": true})
TRAMA_TACI = 3      # svuota subito l'audio in coda (Jarvis interrotto)
INTESTAZIONE = struct.Struct(">BI")


def trama(tipo, dati=b""):
    return INTESTAZIONE.pack(tipo, len(dati)) + dati


def leggi_trame(buf):
    """Separa le trame complete in buf (bytes/bytearray). Restituisce ([(tipo, dati)], resto)."""
    trame, i = [], 0
    while len(buf) - i >= INTESTAZIONE.size:
        tipo, n = INTESTAZIONE.unpack_from(buf, i)
        if len(buf) - i - INTESTAZIONE.size < n:
            break
        inizio = i + INTESTAZIONE.size
        trame.append((tipo, bytes(buf[inizio:inizio + n])))
        i = inizio + n
    return trame, bytes(buf[i:])


# ---------------------------------------------------------------- testo da dire

_FINE_FRASE = re.compile(r"([.!?…;:])(\s+|$)|\n+")


def pulisci_per_voce(testo):
    """Toglie quello che non si legge ad alta voce: blocchi di codice, markdown, link, emoji."""
    t = re.sub(r"```.*?(```|$)", " ", testo, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                 # [testo](link) -> testo
    t = re.sub(r"https?://\S+", "il link", t)
    t = re.sub(r"^\s{0,3}(#{1,6}|[-*+>]|\d+[.)])\s+", "", t, flags=re.M)  # titoli, elenchi, citazioni
    t = re.sub(r"(\*\*|__|\*|_|~~)(.+?)\1", r"\2", t)
    t = re.sub(r"[|]", " ", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


class Frasi:
    """Accumula il testo che arriva a pezzi e restituisce le frasi complete, pronte da dire.
    Le frasi troppo corte si uniscono alla successiva (meno pause tra una sintesi e l'altra)."""

    def __init__(self, minimo=12):
        self.buf = ""
        self.minimo = minimo

    def aggiungi(self, pezzo):
        self.buf += pezzo
        pronte = []
        while True:
            # un blocco di codice aperto non si spezza: le frasi si cercano solo prima della sua apertura
            limite = self.buf.rfind("```") if self.buf.count("```") % 2 == 1 else len(self.buf)
            blocchi = [b.span() for b in re.finditer(r"```.*?```", self.buf[:limite], flags=re.S)]
            m = None
            for c in _FINE_FRASE.finditer(self.buf, 0, limite):
                if any(a <= c.start() < b for a, b in blocchi):
                    continue  # dentro un blocco di codice chiuso
                if c.end() >= self.minimo or c.group(0).startswith("\n"):
                    m = c
                    break
            if not m:
                break
            frase, self.buf = self.buf[:m.end()], self.buf[m.end():]
            frase = pulisci_per_voce(frase)
            if frase:
                pronte.append(frase)
        return pronte

    def fine(self):
        resto, self.buf = pulisci_per_voce(self.buf), ""
        return [resto] if resto else []


# ---------------------------------------------------------------- azioni di Claude Code

def _breve(s, n=60):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _file(percorso):
    return re.split(r"[\\/]", str(percorso).rstrip("\\/"))[-1] or str(percorso)


_INTERNI = {"ToolSearch", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "Skill"}


def descrivi_azione(nome, ingresso):
    """Riga breve per il pannello: cosa sta facendo Claude Code (None per gli strumenti interni)."""
    if nome in _INTERNI:
        return None
    i = ingresso or {}
    if nome in ("Bash", "PowerShell"):
        return f"Eseguo: {_breve(i.get('description') or i.get('command', ''))}"
    if nome == "Read":
        return f"Leggo {_file(i.get('file_path', ''))}"
    if nome in ("Write", "NotebookEdit"):
        return f"Scrivo {_file(i.get('file_path') or i.get('notebook_path', ''))}"
    if nome == "Edit":
        return f"Modifico {_file(i.get('file_path', ''))}"
    if nome in ("Glob", "Grep"):
        return f"Cerco {_breve(i.get('pattern', ''), 40)}"
    if nome == "WebSearch":
        return f"Cerco in rete: {_breve(i.get('query', ''), 50)}"
    if nome == "WebFetch":
        return "Leggo una pagina web"
    if nome in ("Task", "Agent"):
        return f"Passo all'esecutore: {_breve(i.get('description', ''), 50)}"
    if nome.startswith("mcp__"):
        parti = nome.split("__")
        return f"Uso {parti[1] if len(parti) > 1 else nome}: {parti[-1].replace('_', ' ')}"
    return f"Uso {nome}"


# ---------------------------------------------------------------- sì / no

_SI = {"si", "sì", "confermo", "conferma", "procedi", "vai", "ok", "okay", "certo", "esatto", "fallo",
       "affermativo", "va bene", "certamente", "assolutamente", "yes", "d'accordo", "daccordo"}
_NO = {"no", "annulla", "fermo", "fermati", "stop", "lascia stare", "non farlo", "negativo", "aspetta",
       "lascia perdere", "nemmeno per sogno", "per niente"}


def si_o_no(testo):
    """True per un sì, False per un no, None se non è chiaro. Il no vince sempre (più prudente)."""
    t = " " + re.sub(r"[^\wàèéìòù' ]", " ", testo.lower()) + " "
    t = re.sub(r"\s+", " ", t)
    no = any(f" {p} " in t for p in _NO) or " non " in t
    si = any(f" {p} " in t for p in _SI)
    if no:
        return False
    if si:
        return True
    return None
