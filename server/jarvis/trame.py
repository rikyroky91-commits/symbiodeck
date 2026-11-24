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

_FINE_FRASE = re.compile(r"([.!?…;])(\s+|$)|\n+")


_UNITA = [(r"(\d)\s*TB\b", r"\1 tera"), (r"(\d)\s*GB\b", r"\1 giga"), (r"(\d)\s*MB\b", r"\1 mega"),
          (r"(\d)\s*KB\b", r"\1 kilobyte"), (r"(\d)\s*GHz\b", r"\1 gigahertz"), (r"(\d)\s*MHz\b", r"\1 megahertz"),
          (r"(\d)\s*°C\b", r"\1 gradi"), (r"(\d)\s*W\b", r"\1 watt"), (r"(\d)\s*ms\b", r"\1 millisecondi"),
          (r"(\d)\s*[x×]\s*(\d)", r"\1 per \2"), (r"\bfps\b", "f p s")]


def pulisci_per_voce(testo):
    """Rende il testo dicibile: niente simboli letti alla lettera ("cancelletto", "asterisco"), niente codice,
    link o emoji; elenchi e tabelle diventano frasi. Pensato per la sintesi vocale italiana."""
    t = re.sub(r"<(invoke|parameter|function_calls)\b[^>]*>.*?(</\1>|$)", " ", testo, flags=re.S)  # chiamate scritte come testo
    t = re.sub(r"</?[A-Za-z][\w:-]*[^>]*>", " ", t)                       # altri tag
    t = re.sub(r"```.*?(```|$)", " ", t, flags=re.S)                      # blocchi di codice
    t = re.sub(r"`([^`]*)`", r"\1", t)                                    # codice in linea: resta il testo
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)                      # [testo](link) e immagini
    t = re.sub(r"https?://\S+|www\.\S+", "il link", t)
    t = re.sub(r"^[ \t]*[-|: \t]{3,}$", "", t, flags=re.M)                # righe di separazione delle tabelle
    t = re.sub(r"^[ \t]*\|(.*)\|[ \t]*$", lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()) + ".",
               t, flags=re.M)                                             # righe di tabella -> elenco parlato
    t = re.sub(r"^[ \t]{0,3}(#{1,6}|>+|[-*+•·▪►]|\d{1,2}[.)])[ \t]+", "", t, flags=re.M)  # titoli, citazioni, elenchi
    t = re.sub(r"\b([CF])#", r"\1 sharp", t)                              # C#, F#
    t = re.sub(r"\s*(->|=>|→|⇒)\s*", ", poi ", t)
    t = re.sub(r"\s*(<-|←)\s*", ", da ", t)
    t = t.replace("&", " e ").replace("≈", "circa ").replace("~", "circa ").replace("±", "più o meno ")
    t = re.sub(r"[*_#|<>^`\\]+", " ", t)                                  # simboli rimasti: mai letti
    t = re.sub(r"(\w)/(\w)", r"\1 \2", t)                                 # e/o, A/B
    for regola, sostituto in _UNITA:
        t = re.sub(regola, sostituto, t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u2190-\u21FF\u2700-\u27BF\u2500-\u25FF]", "", t)
    # ogni riga (es. voce di un elenco) finisce con una pausa
    righe = [r.strip(" ,;") for r in t.splitlines()]
    t = " ".join(r if re.search(r"[.!?…:;]$", r) else r + "." for r in righe if r)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])\1+", r"\1", t)
    t = re.sub(r"\.{2,}(?!\.)", ".", t) if "..." not in t else t
    return re.sub(r"\s{2,}", " ", t).strip(" ,;")


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


_SEMPRE = (" sempre ", " ogni volta ", " d'ora in poi ", " da ora in poi ", " d'ora in avanti ", " per sempre ",
           " tutte le volte ", " mai ", " mai più ", " always ", " never ")


def risposta_conferma(testo):
    """(ok, per_sempre) da una risposta a voce: "sì, sempre" -> (True, True), "no, mai" o "mai" -> (False, True),
    "sì" -> (True, False), non chiaro -> (None, False). Il no vince sempre."""
    t = " " + re.sub(r"\s+", " ", re.sub(r"[^\wàèéìòù' ]", " ", (testo or "").lower())) + " "
    t = t.replace(" non per sempre ", " ").replace(" non sempre ", " ")   # "sì, ma non sempre" = solo questa volta
    sempre = any(p in t for p in _SEMPRE)
    ok = si_o_no(t)
    if ok is None and (" mai " in t or " never " in t):
        ok = False
    if ok is None and sempre and " mai " not in t:
        ok = True   # solo "sempre" (o "d'ora in poi"): vale come sì
    if ok is True and " non " in t:
        ok = False
    return ok, bool(sempre and ok is not None)


# ---------------------------------------------------------------- interruzione mentre Jarvis parla

class Interruzione:
    """Decide se mentre Jarvis parla c'è una persona che gli parla sopra. Per ogni blocco di 80 ms riceve:
    la probabilità di voce (VAD), il volume del microfono e `rif`, il volume di quello che Jarvis sta facendo
    uscire dalle casse (il massimo dell'ultimo secondo, per coprire il ritardo). Impara il rapporto eco/uscita
    (quanto della sua voce rientra dal microfono) e interrompe solo se il microfono sente almeno `fattore` volte
    più di quanto spiegherebbe l'eco, con voce, per ~400 ms di fila. I primi blocchi di ogni frase servono solo
    a imparare."""

    BLOCCHI = 5        # 400 ms di voce sopra la sua
    RISCALDAMENTO = 6  # 480 ms all'inizio di ogni frase: solo per imparare l'eco

    def __init__(self):
        self.k = None      # rapporto microfono / uscita dovuto all'eco
        self.sopra = 0
        self.blocchi = 0

    @property
    def eco(self):
        return self.k or 0.0

    def passo(self, prob, rms, rif, fattore):
        if rif < 0.02 and self.k is None:
            return False        # la sua voce non è ancora partita: niente da imparare né da confrontare
        self.blocchi += 1
        rif = max(rif, 0.02)    # pause tra le parole: si confronta con un'uscita minima, non con il rumore
        rapporto = rms / rif
        # k segue i picchi dell'eco (sale in fretta, scende piano): una media lo sottostimerebbe e i picchi
        # della sua stessa voce sembrerebbero una persona. Quello che supera di molto l'eco non si impara.
        # All'inizio di ogni frase (riscaldamento) si impara in fretta; dopo, k si muove piano, così una persona
        # che parla non lo "trascina" su fino a non essere più sentita.
        if self.k is None:
            self.k = rapporto
        elif self.blocchi <= self.RISCALDAMENTO:
            self.k = max(self.k * 0.9, rapporto) if rapporto > self.k else self.k * 0.97 + rapporto * 0.03
        elif rapporto <= self.k:
            self.k = self.k * 0.995 + rapporto * 0.005
        elif rapporto <= self.k * fattore:
            self.k += (rapporto - self.k) * 0.02
        if self.blocchi <= self.RISCALDAMENTO:
            return False
        if prob >= 0.5 and rms > max(0.01, fattore * self.k * rif):
            self.sopra += 1
        else:
            self.sopra = max(0, self.sopra - 1)
        if self.sopra >= self.BLOCCHI:
            self.sopra = 0
            return True
        return False

    def azzera(self):
        """Jarvis ha smesso di parlare: alla prossima frase si ricomincia con il riscaldamento (k resta)."""
        self.sopra = 0
        self.blocchi = 0
