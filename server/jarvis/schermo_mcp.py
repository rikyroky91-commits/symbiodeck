"""Server MCP "schermo" per il Claude Code di Jarvis: vedere lo schermo e usare mouse e tastiera, come il
computer use dell'app Claude (che il Claude Code a riga di comando non ha).

Strumenti: schermi, screenshot (anche di una zona, per leggere i dettagli), clic, trascina, scorri, scrivi,
tasti, finestre, porta_davanti, e per le app normali di Windows controlli / usa_controllo: la finestra letta come
testo (UI Automation: pulsanti, campi, voci di menu con nome e valore) e usata per numero. Idea presa da
JARVIS-VORNEX (github.com/VORNEX-01/JARVIS-VORNEX): più affidabile dei clic a coordinate e utile al modello locale,
che non vede le immagini. Le coordinate sono sempre quelle dell'ultimo screenshot: il server le
converte in pixel veri (schermi in 4K, ridimensionamento di Windows, più monitor).

Lo avvia Claude Code (vedi esecutore.py, --mcp-config):  python schermo_mcp.py
"""
import ctypes
import ctypes.wintypes as wt
import io
import time

import mss
from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # pixel veri su tutti i monitor
except (AttributeError, OSError):
    pass

LATO_MASSIMO = 1568   # lato lungo dell'immagine mandata al modello
_ultima = {"sinistra": 0, "alto": 0, "scala": 1.0, "larghezza": 0, "altezza": 0}

server = MCPServer("schermo", instructions=(
    "Vedere e controllare lo schermo del PC Windows. Fai sempre uno screenshot prima di cliccare: le coordinate "
    "di clic, trascina e scorri sono in pixel dell'ULTIMO screenshot. Dopo un'azione rifai lo screenshot per "
    "controllare. Per i testi piccoli usa screenshot con una zona. Preferisci PowerShell quando basta (aprire "
    "programmi, file, impostazioni): il mouse è più lento e fragile. Per le app normali di Windows (Esplora file, "
    "Impostazioni, Blocco note, Office, finestre di dialogo) prova prima controlli + usa_controllo: leggi la "
    "finestra come testo e agisci per numero, poi richiama controlli per verificare. I giochi e molte app "
    "Electron/Chrome non espongono i controlli: lì usa screenshot e clic."))


# ---------------------------------------------------------------- schermo
def _monitor(numero):
    with mss.mss() as m:
        monitor = m.monitors
        if not 1 <= numero < len(monitor):
            raise ValueError(f"schermo {numero} inesistente: ce ne sono {len(monitor) - 1}")
        return monitor[numero]


@server.tool()
def schermi() -> str:
    """Elenca i monitor collegati (numero, risoluzione, posizione). Il numero 1 è il principale."""
    with mss.mss() as m:
        righe = [f"{i}: {s['width']}x{s['height']} in ({s['left']},{s['top']})" for i, s in enumerate(m.monitors) if i]
    return "\n".join(righe)


@server.tool()
def screenshot(schermo: int = 1, x0: int = -1, y0: int = -1, x1: int = -1, y1: int = -1) -> list:
    """Fotografa uno schermo (1 = principale). Con x0,y0,x1,y1 (coordinate dell'ultimo screenshot) fotografa solo
    quella zona, ingrandita: serve per leggere dettagli piccoli. I clic successivi usano le coordinate
    della NUOVA immagine."""
    s = _monitor(schermo)
    zona = {"left": s["left"], "top": s["top"], "width": s["width"], "height": s["height"]}
    if min(x0, y0, x1, y1) >= 0 and x1 > x0 and y1 > y0:
        rx0, ry0 = _vero(x0, y0)
        rx1, ry1 = _vero(x1, y1)
        zona = {"left": rx0, "top": ry0, "width": max(8, rx1 - rx0), "height": max(8, ry1 - ry0)}
    with mss.mss() as m:
        grezza = m.grab(zona)
    img = PILImage.frombytes("RGB", grezza.size, grezza.bgra, "raw", "BGRX")
    scala = min(1.0 if zona["width"] >= 600 else 2.0, LATO_MASSIMO / max(img.width, img.height))
    if scala != 1.0:
        img = img.resize((max(1, int(img.width * scala)), max(1, int(img.height * scala))), PILImage.LANCZOS)
    _ultima.update(sinistra=zona["left"], alto=zona["top"], scala=scala, larghezza=img.width, altezza=img.height)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return [Image(data=buf.getvalue(), format="jpeg"),
            f"Immagine {img.width}x{img.height}: usa queste coordinate per cliccare."]


def _vero(x, y):
    s = _ultima["scala"] or 1.0
    return int(_ultima["sinistra"] + x / s), int(_ultima["alto"] + y / s)


# ---------------------------------------------------------------- mouse
SU_GIU = {"sinistro": (0x0002, 0x0004), "destro": (0x0008, 0x0010), "centrale": (0x0020, 0x0040)}


def _sposta(x, y):
    vx, vy = _vero(x, y)
    user32.SetCursorPos(vx, vy)
    time.sleep(0.05)
    return vx, vy


@server.tool()
def clic(x: int, y: int, pulsante: str = "sinistro", doppio: bool = False) -> str:
    """Clic nel punto (x, y) dell'ultimo screenshot. pulsante: sinistro, destro o centrale."""
    giu, su = SU_GIU.get(pulsante, SU_GIU["sinistro"])
    vx, vy = _sposta(x, y)
    for _ in range(2 if doppio else 1):
        user32.mouse_event(giu, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(su, 0, 0, 0, 0)
        time.sleep(0.08)
    return f"clic {pulsante}{' doppio' if doppio else ''} in ({vx},{vy})"


@server.tool()
def trascina(x0: int, y0: int, x1: int, y1: int) -> str:
    """Trascina con il tasto sinistro da (x0,y0) a (x1,y1) (coordinate dell'ultimo screenshot)."""
    _sposta(x0, y0)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    passi = 12
    for i in range(1, passi + 1):
        _sposta(x0 + (x1 - x0) * i / passi, y0 + (y1 - y0) * i / passi)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    return "trascinato"


@server.tool()
def scorri(x: int, y: int, scatti: int = -3) -> str:
    """Rotellina del mouse sopra (x, y): scatti negativi = giù, positivi = su."""
    _sposta(x, y)
    user32.mouse_event(0x0800, 0, 0, ctypes.c_uint32(int(scatti) * 120).value, 0)
    return f"scorso di {scatti}"


# ---------------------------------------------------------------- tastiera
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _invia(vk=0, scan=0, flag=0):
    i = _INPUT(type=1)
    i.ki = _KEYBDINPUT(vk, scan, flag, 0, 0)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_INPUT))


@server.tool()
def scrivi(testo: str) -> str:
    """Scrive il testo nella finestra attiva (qualsiasi carattere, anche accentato). Per Invio usa tasti("enter")."""
    for c in testo:
        if c == "\n":
            _premi([0x0D])
            continue
        b = c.encode("utf-16-le")  # emoji e simili sono due unità UTF-16: si mandano entrambe
        for i in range(0, len(b), 2):
            u = int.from_bytes(b[i:i + 2], "little")
            _invia(scan=u, flag=0x0004)            # KEYEVENTF_UNICODE
            _invia(scan=u, flag=0x0004 | 0x0002)   # + KEYUP
        time.sleep(0.005)
    return f"scritti {len(testo)} caratteri"


VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 0x09, "esc": 0x1B, "enter": 0x0D, "invio": 0x0D,
      "space": 0x20, "spazio": 0x20, "backspace": 0x08, "delete": 0x2E, "canc": 0x2E, "home": 0x24, "end": 0x23,
      "pgup": 0x21, "pgdn": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D,
      "printscreen": 0x2C, "volume_up": 0xAF, "volume_down": 0xAE, "volume_mute": 0xAD,
      "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
ESTESI = {0x5B, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2C}


def _premi(codici):
    for vk in codici:
        _invia(vk, user32.MapVirtualKeyW(vk, 0), 0x0001 if vk in ESTESI else 0)
        time.sleep(0.02)
    for vk in reversed(codici):
        _invia(vk, user32.MapVirtualKeyW(vk, 0), (0x0001 if vk in ESTESI else 0) | 0x0002)


@server.tool()
def tasti(combinazione: str) -> str:
    """Preme una combinazione, es. "ctrl+s", "alt+f4", "win+d", "enter", "ctrl+shift+esc"."""
    codici = []
    for p in combinazione.lower().replace(" ", "").split("+"):
        if p not in VK:
            raise ValueError(f"tasto sconosciuto: {p}")
        codici.append(VK[p])
    _premi(codici)
    return f"premuto {combinazione}"


# ---------------------------------------------------------------- finestre
def _elenco():
    trovate = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def ognuna(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # niente finestre "possedute"
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                trovate.append((hwnd, buf.value))
        return True
    user32.EnumWindows(ognuna, 0)
    return trovate


@server.tool()
def finestre() -> str:
    """Elenca le finestre aperte (titolo), la prima è quella davanti."""
    davanti = user32.GetForegroundWindow()
    return "\n".join(("▶ " if h == davanti else "  ") + t for h, t in _elenco()) or "nessuna finestra"


@server.tool()
def porta_davanti(titolo: str) -> str:
    """Porta davanti la prima finestra il cui titolo contiene il testo dato (anche se ridotta a icona)."""
    for hwnd, t in _elenco():
        if titolo.lower() in t.lower():
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.keybd_event(0x12, 0, 0, 0)          # Alt: Windows permette di cambiare il primo piano
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(0x12, 0, 0x0002, 0)
            return f"davanti: {t}"
    raise ValueError(f"nessuna finestra con '{titolo}' nel titolo")


# ---------------------------------------------------------------- controlli (UI Automation)
_inventario = {"finestra": "", "cerca": "", "massimo": 150, "nomi": []}
_TIPI_UTILI = {"ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl",
               "ListItemControl", "MenuItemControl", "TabItemControl", "HyperlinkControl", "TreeItemControl",
               "DocumentControl", "SliderControl", "SplitButtonControl", "DataItemControl", "TextControl"}


def _uia():
    try:
        import uiautomation
    except ImportError as e:
        raise RuntimeError("manca il pacchetto uiautomation (pip install uiautomation)") from e
    return uiautomation


def _valore(c):
    try:
        return (c.GetValuePattern().Value or "")[:120]
    except Exception:
        return ""


def _finestra_uia(titolo):
    auto = _uia()
    if titolo:
        for hwnd, t in _elenco():
            if titolo.lower() in t.lower():
                return auto.ControlFromHandle(hwnd), t
        raise ValueError(f"nessuna finestra con '{titolo}' nel titolo")
    hwnd = user32.GetForegroundWindow()
    return auto.ControlFromHandle(hwnd), (auto.ControlFromHandle(hwnd).Name or "finestra davanti")


def _raccogli(finestra, cerca, massimo):
    """(titolo, [(controllo, tipo, nome, valore, attivo)]) in ordine di lettura. Si rilegge a ogni uso: gli
    oggetti COM non passano bene da un thread all'altro e la finestra può essere cambiata."""
    radice, titolo = _finestra_uia(finestra)
    trovati, cerca = [], cerca.lower()

    def visita(c, profondita):
        if len(trovati) >= max(10, min(massimo, 400)) or profondita > 25:
            return
        try:
            figli = c.GetChildren()
        except Exception:
            return
        for x in figli:
            try:
                tipo, nome = x.ControlTypeName, (x.Name or "").strip()
                if tipo in _TIPI_UTILI and not x.IsOffscreen:
                    valore = _valore(x)
                    if (nome or valore) and (not cerca or cerca in nome.lower() or cerca in valore.lower()):
                        trovati.append((x, tipo, nome[:80], valore, x.IsEnabled))
            except Exception:
                continue
            visita(x, profondita + 1)
    visita(radice, 0)
    return titolo, trovati


@server.tool()
def controlli(finestra: str = "", cerca: str = "", massimo: int = 150) -> str:
    """Legge come testo i controlli di una finestra (quella davanti, o la prima il cui titolo contiene `finestra`):
    pulsanti, campi, caselle, voci di elenco e di menu, con un numero, il tipo, il nome e il valore. Con `cerca`
    tiene solo quelli il cui nome o valore contiene quel testo. Poi usa_controllo(numero, ...)."""
    auto = _uia()
    with auto.UIAutomationInitializerInThread():
        titolo, trovati = _raccogli(finestra, cerca, massimo)
    _inventario.update(finestra=finestra or titolo, cerca=cerca, massimo=massimo,
                       nomi=[(t[1], t[2]) for t in trovati])
    if not trovati:
        return (f"{titolo}: nessun controllo leggibile (gioco, app Electron/Chrome o finestra vuota). "
                "Usa screenshot e clic.")
    righe = [f"Finestra: {titolo}"]
    for i, (_c, tipo, nome, valore, attivo) in enumerate(trovati, 1):
        righe.append(f"{i}. {tipo.replace('Control', '')} «{nome}»" + (f" = «{valore}»" if valore else "")
                     + ("" if attivo else " (disattivato)"))
    return "\n".join(righe)


@server.tool()
def usa_controllo(numero: int, azione: str = "clic", testo: str = "") -> str:
    """Agisce sul controllo con quel numero (dall'ultimo `controlli`). azione: clic (premere un pulsante, aprire
    una voce), scrivi (mette `testo` in un campo, sostituendo quello che c'era), spunta (casella sì/no), seleziona
    (voce di elenco o scheda), espandi (menu, albero, tendina). Poi richiama controlli per verificare."""
    nomi = _inventario["nomi"]
    if not 1 <= numero <= len(nomi):
        raise ValueError(f"numero {numero} non valido: rileggi la finestra con controlli")
    auto = _uia()
    with auto.UIAutomationInitializerInThread():
        _titolo, trovati = _raccogli(_inventario["finestra"], _inventario["cerca"], _inventario["massimo"])
        if len(trovati) < numero or (trovati[numero - 1][1], trovati[numero - 1][2]) != nomi[numero - 1]:
            raise ValueError("la finestra è cambiata dall'ultima lettura: richiama controlli e riprova")
        c = trovati[numero - 1][0]
        if azione == "scrivi":
            try:
                c.GetValuePattern().SetValue(testo)
            except Exception:
                c.SetFocus()
                auto.SendKeys("{Ctrl}a{Del}", waitTime=0.05)
                auto.SendKeys(testo.replace("{", "{{}").replace("}", "{}}"), interval=0.01, waitTime=0.05)
            return f"scritto in «{c.Name}»: {_valore(c) or testo}"
        if azione == "spunta":
            c.GetTogglePattern().Toggle()
            return f"«{c.Name}» ora è {'spuntato' if c.GetTogglePattern().ToggleState == 1 else 'non spuntato'}"
        if azione == "seleziona":
            c.GetSelectionItemPattern().Select()
            return f"selezionato «{c.Name}»"
        if azione == "espandi":
            c.GetExpandCollapsePattern().Expand()
            return f"aperto «{c.Name}»"
        try:
            c.GetInvokePattern().Invoke()
        except Exception:
            c.Click(simulateMove=False, waitTime=0.1)
        time.sleep(0.3)
        return f"cliccato «{c.Name}»"


if __name__ == "__main__":
    server.run("stdio")
