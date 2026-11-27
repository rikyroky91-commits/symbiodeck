"""La bacchetta magica del visore (2026-11-27): quello che cerchi nell'aria diventa un ritaglio che Jarvis può
guardare e cercare, come la Visual Intelligence.

Il visore dice dove hai disegnato (punti 0..1 sull'immagine) e su cosa: lo schermo del PC in Moonlight, una finestra
o uno schermo in diretta, una foto, o la stanza (e allora manda lui la foto della sua telecamera). Qui si prende
l'immagine, si ritaglia attorno al disegno e si salva in Immagini\\Bacchetta. L'immagine va a Claude solo quando poi
tocchi un pulsante o parli (vedi principale.py): disegnare da solo non manda niente fuori dal PC.
"""
import io
import secrets
import threading
import time
from pathlib import Path

from . import visore

LATO_CLAUDE = 1568          # oltre Claude rimpicciolisce da sé
LATO_ANTEPRIMA = 640
SCADE = 180                 # secondi: dopo, un ritaglio non si attacca più a quello che dici
MAX_PUNTI = 400


def leggi_punti(testo):
    """"x,y;x,y;..." con valori 0..1 (anche poco fuori: il disegno può sbordare)."""
    punti = []
    for coppia in str(testo or "").split(";")[:MAX_PUNTI]:
        x, _, y = coppia.partition(",")
        try:
            x, y = float(x), float(y)
        except ValueError:
            continue
        if -1.0 <= x <= 2.0 and -1.0 <= y <= 2.0:
            punti.append((x, y))
    if len(punti) < 2:
        raise ValueError("disegno troppo corto")
    return punti


def riquadro(punti, w, h, margine=0.08):
    """Il rettangolo da ritagliare (pixel) per un disegno su un'immagine w x h.

    Cerchio o forma chiusa: quello che sta dentro. Riga quasi piatta (sottolineatura): quello che sta sopra.
    Puntino (tocco): una zona attorno."""
    xs = [x * w for x, _ in punti]
    ys = [y * h for _, y in punti]
    l, r, t, b = min(xs), max(xs), min(ys), max(ys)
    lw, lh = r - l, b - t
    corto = 0.05 * min(w, h)
    if lw < corto and lh < corto:
        lato = 0.18 * min(w, h)
        cx, cy = (l + r) / 2, (t + b) / 2
        l, r, t, b = cx - lato, cx + lato, cy - lato, cy + lato
    elif lh < 0.22 * lw:
        t = b - max(0.35 * lw, corto * 2)
        b += corto / 2
    else:
        # un disegno stretto e alto (parentesi, riga verticale) si allarga quanto basta per leggere il contesto
        if lw < 0.22 * lh:
            allarga = 0.35 * lh
            l, r = l - allarga, r + allarga
    m = margine * max(r - l, b - t)
    l, t, r, b = l - m, t - m, r + m, b + m
    l, t = max(0, int(l)), max(0, int(t))
    r, b = min(w, int(round(r))), min(h, int(round(b)))
    if r - l < 8 or b - t < 8:
        raise ValueError("il disegno è fuori dall'immagine")
    return l, t, r, b


def _jpeg(im, lato, qualita=88):
    im = im.convert("RGB")
    if max(im.size) > lato:
        k = lato / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=qualita)
    return b.getvalue()


def ritaglia(dati, punti):
    """(pulito, segnato): il ritaglio da salvare e lo stesso col disegno dell'utente tracciato sopra (per Claude)."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(dati))
    im.load()
    im = im.convert("RGB")
    l, t, r, b = riquadro(punti, im.width, im.height)
    pulito = im.crop((l, t, r, b))
    # un ritaglio piccolo si ingrandisce: si legge meglio
    if max(pulito.size) < 480:
        k = 480 / max(pulito.size)
        pulito = pulito.resize((round(pulito.width * k), round(pulito.height * k)), Image.LANCZOS)
    segnato = pulito.copy()
    kx, ky = segnato.width / (r - l), segnato.height / (b - t)
    linea = [((x * im.width - l) * kx, (y * im.height - t) * ky) for x, y in punti]
    spessore = max(2, round(max(segnato.size) / 240))
    ImageDraw.Draw(segnato).line(linea, fill=(255, 176, 40), width=spessore, joint="curve")
    return _jpeg(pulito, LATO_CLAUDE, 92), _jpeg(segnato, LATO_CLAUDE)


def monitor_dello_stream(monitor, larghezza, altezza, virtuali=()):
    """Quale schermo mostra Moonlight: quello grande come lo streaming (prima i virtuali di Apollo), se no il primo.
    `monitor` come mss.monitors (il primo elemento è l'insieme di tutti e si salta); `virtuali` = (left, top)."""
    veri = list(enumerate(monitor))[1:]
    if not veri:
        raise LookupError("nessuno schermo")
    uguali = [(n, m) for n, m in veri if (m["width"], m["height"]) == (larghezza, altezza)]
    for n, m in uguali + veri:
        if (m["left"], m["top"]) in virtuali:
            return n
    return (uguali or veri)[0][0]


def _schermi_virtuali():
    """(left, top) dei monitor virtuali (Apollo)."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32

        class DD(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                        ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]

        class MI(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD),
                        ("szDevice", wt.WCHAR * 32)]

        nomi, i = set(), 0
        while True:
            d = DD()
            d.cb = ctypes.sizeof(d)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
                break
            if d.StateFlags & 1 and "Virtual Display" in d.DeviceString:
                nomi.add(d.DeviceName)
            i += 1
        trovati = set()
        prototipo = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

        def ognuno(h, _dc, _r, _l):
            m = MI()
            m.cbSize = ctypes.sizeof(m)
            if user32.GetMonitorInfoW(h, ctypes.byref(m)) and m.szDevice in nomi:
                trovati.add((m.rcMonitor.left, m.rcMonitor.top))
            return True

        user32.EnumDisplayMonitors(None, None, prototipo(ognuno), 0)
        return trovati
    except Exception:
        return set()


def immagine_di(fonte, q, corpo=b""):
    """L'immagine intera su cui è stato fatto il disegno. `q` = parametri della richiesta (stringhe)."""
    if fonte == "stanza":
        if not corpo.startswith(b"\xff\xd8"):
            raise ValueError("manca la foto della stanza")
        return corpo
    if fonte == "schermo":
        import mss
        with mss.mss() as m:
            numero = monitor_dello_stream(m.monitors, int(q.get("larghezza") or 0), int(q.get("altezza") or 0),
                                          _schermi_virtuali())
        return visore.cattura_schermo(numero, 3840)
    if fonte == "finestra":
        if int(q.get("numero") or 0) > 0:
            return visore.cattura_schermo(int(q["numero"]), 3840)
        return visore.cattura_finestra(int(q.get("hwnd") or 0), 3840)
    if fonte == "foto":
        return visore.anteprima(str(q.get("percorso") or ""), 3840)
    raise ValueError("fonte sconosciuta")


NOMI_FONTE = {"stanza": "un oggetto vero davanti a lui (foto dalla telecamera del visore)",
              "schermo": "lo schermo del PC", "finestra": "una finestra del PC", "foto": "una foto sul PC"}


class Bacchetta:
    """I ritagli in attesa: l'ultimo si attacca a quello che l'utente dice o al pulsante che tocca."""

    def __init__(self, cartella=None):
        self._cartella = cartella
        self._blocco = threading.Lock()
        self.attesa = None

    def cartella(self):
        if self._cartella is None:
            immagini = visore.cartella_nota("immagini") or Path.home() / "Pictures"
            self._cartella = Path(immagini) / "Bacchetta"
        return Path(self._cartella)

    def nuovo(self, fonte, punti, q, corpo=b"", immagine=None):
        dati = immagine if immagine is not None else immagine_di(fonte, q, corpo)
        pulito, segnato = ritaglia(dati, punti)
        cartella = self.cartella()
        cartella.mkdir(parents=True, exist_ok=True)
        nome = time.strftime("%Y-%m-%d %H.%M.%S")
        file = cartella / f"{nome}.jpg"
        n = 2
        while file.exists():
            file = cartella / f"{nome} ({n}).jpg"
            n += 1
        file.write_bytes(pulito)
        s = {"id": secrets.token_hex(6), "fonte": fonte, "percorso": str(file), "jpeg": segnato, "quando": time.time()}
        with self._blocco:
            self.attesa = s
        return s, _jpeg_ridotto(pulito)

    def prendi(self, id_=None):
        """Toglie e restituisce il ritaglio in attesa (None se scaduto o se l'id non è quello)."""
        with self._blocco:
            s = self.attesa
            if not s or (id_ and s["id"] != id_) or time.time() - s["quando"] > SCADE:
                return None
            self.attesa = None
            return s

    def annulla(self, id_=None):
        return self.prendi(id_) is not None


def _jpeg_ridotto(dati):
    from PIL import Image
    return _jpeg(Image.open(io.BytesIO(dati)), LATO_ANTEPRIMA, 85)


# cosa chiede ogni pulsante della scheda nel visore
RICHIESTE = {
    "cerca": "Cerca quello che ho cerchiato: dimmi cos'è e trova in rete le informazioni utili (se è un oggetto, "
             "dove si compra e quanto costa).",
    "guarda": "Guarda quello che ho cerchiato e dimmi in breve cos'è o cosa c'è scritto. Poi tienilo a mente: "
              "potrei farti domande.",
    "traduci": "Leggi il testo che ho cerchiato e, se non è in italiano, traducilo.",
}


def nota_per_claude(s):
    dove = NOMI_FONTE.get(s["fonte"], "uno schermo")
    return (f"[Con la bacchetta magica del visore l'utente ha cerchiato qualcosa su {dove}. "
            "L'immagine allegata è il ritaglio, con il suo segno arancione. Il ritaglio senza segno è "
            f"salvato in \"{s['percorso']}\": se affidi un lavoro all'esecutore, dagli questo percorso.]")
