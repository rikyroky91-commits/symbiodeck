"""Il PC dentro il visore (Jarvis XR, web/xr.js): cartelle, foto, video, testi e finestre di Windows.

- File: elenco di una cartella, anteprime delle foto (Pillow, girate secondo l'EXIF, in cache), video e testi così
  come sono (con le richieste a pezzi, Range, per i video), "apri sul PC". Solo lettura. Si vede solo dentro la
  cartella dell'utente e sui dischi diversi da quello di sistema; mai Windows, Program Files, ProgramData, AppData,
  file nascosti. "Apri sul PC" non avvia programmi né script (.exe, .bat, .ps1, .lnk…): per quelli c'è Jarvis.
- Cartelle a parole: `risolvi_cartella("foto", "desktop")` trova la cartella più somigliante tra quelle note (Desktop,
  Immagini, Documenti, Download, Video, Musica, la cartella utente e gli altri dischi), fino a 3 livelli.
- Finestre: elenco delle finestre aperte, immagine di una finestra anche se coperta (PrintWindow) o di uno schermo
  intero, e comandi (clic, doppio clic, rotella, tasti, testo, portare davanti) a coordinate relative 0..1.
Le parti di Windows si caricano solo su Windows: altrove (prove) le funzioni dei file funzionano lo stesso.
"""
import io
import os
import re
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path

WINDOWS = sys.platform == "win32"

IMMAGINI = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/mp4", ".mkv": "video/x-matroska"}
TESTI = {".txt", ".md", ".log", ".json", ".csv", ".ini", ".cfg", ".py", ".js", ".html", ".css", ".xml", ".yml", ".yaml",
         ".bat", ".ps1"}
ESEGUIBILI = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
              ".msp", ".scr", ".lnk", ".url", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw", ".appref-ms"}
_VIETATE = re.compile(r"^[a-z]:\\(windows|program files( \(x86\))?|programdata|\$recycle\.bin|system volume information)"
                      r"(\\|$)|\\appdata(\\|$)", re.I)

# ------------------------------------------------------------------ cartelle note
_GUID = {"desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "documenti": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
         "immagini": "33E28130-4E1E-4676-835A-98395C3BC3BB", "download": "374DE290-123F-4565-9164-39C4925E467B",
         "video": "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "musica": "4BD8D571-6D19-48D3-BE97-422220080E43"}
_INGLESE = {"desktop": "Desktop", "documenti": "Documents", "immagini": "Pictures", "download": "Downloads",
            "video": "Videos", "musica": "Music"}
SINONIMI = {"scrivania": "desktop", "desktop": "desktop", "documenti": "documenti", "documents": "documenti",
            "immagini": "immagini", "foto": "immagini", "pictures": "immagini", "download": "download",
            "scaricati": "download", "downloads": "download", "video": "video", "filmati": "video",
            "musica": "musica", "music": "musica", "home": "utente", "utente": "utente"}
_cache_note = {}


def cartella_nota(nome):
    """Percorso vero di Desktop, Documenti, Immagini… (anche se spostati in OneDrive)."""
    nome = SINONIMI.get(_norma(nome), _norma(nome))
    if nome == "utente":
        return Path.home()
    if nome in _cache_note:
        return _cache_note[nome]
    percorso = None
    if WINDOWS and nome in _GUID:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]
        u = uuid.UUID(_GUID[nome])
        g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            percorso = Path(p.value)
            ctypes.windll.ole32.CoTaskMemFree(p)
    if percorso is None and nome in _INGLESE:
        percorso = Path.home() / _INGLESE[nome]
    _cache_note[nome] = percorso
    return percorso


def dischi():
    if not WINDOWS:
        return []
    sistema = (os.environ.get("SystemDrive") or "C:").upper()
    return [Path(f"{l}:\\") for l in "DEFGHIJKLMNOPQRSTUVWXYZ" if f"{l}:" != sistema and os.path.exists(f"{l}:\\")]


def radici():
    voci = []
    for chiave, nome in (("desktop", "Desktop"), ("immagini", "Immagini"), ("documenti", "Documenti"),
                         ("download", "Download"), ("video", "Video"), ("musica", "Musica"), ("utente", "Cartella utente")):
        p = cartella_nota(chiave)
        if p and p.is_dir():
            voci.append({"nome": nome, "percorso": str(p), "tipo": "cartella"})
    voci += [{"nome": f"Disco {d.drive}", "percorso": str(d), "tipo": "disco"} for d in dischi()]
    return voci


# ------------------------------------------------------------------ permessi
def consentito(percorso):
    """Il percorso (già esistente) si può mostrare nel visore?"""
    try:
        p = Path(os.path.realpath(os.path.expandvars(str(percorso))))
    except (OSError, ValueError):
        return False
    s = str(p)
    if _VIETATE.search(s) or any(part.startswith(".") for part in p.parts[1:]):
        return False
    casa = Path(os.path.realpath(Path.home()))
    if p == casa or casa in p.parents:
        return True
    if not WINDOWS:
        return False
    sistema = (os.environ.get("SystemDrive") or "C:").upper()
    return p.drive.upper() != sistema and bool(p.drive)


def _nascosto(voce):
    if voce.name.startswith(".") or voce.name.lower() in ("desktop.ini", "thumbs.db", "ntuser.dat"):
        return True
    try:
        attributi = getattr(voce.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributi & 0x2 or attributi & 0x4)   # nascosto, di sistema


def tipo_di(nome):
    e = os.path.splitext(nome)[1].lower()
    if e in IMMAGINI:
        return "immagine"
    if e in VIDEO:
        return "video"
    if e in TESTI:
        return "testo"
    if e == ".pdf":
        return "pdf"
    if e in (".mp3", ".wav", ".flac", ".m4a", ".ogg"):
        return "audio"
    return "file"


def elenco(percorso, massimo=600):
    """Contenuto di una cartella: prima le cartelle, poi i file, in ordine di nome. "" = le radici."""
    if not percorso:
        return {"percorso": "", "nome": "Questo PC", "su": None, "voci": radici()}
    p = Path(os.path.realpath(percorso))
    if not p.is_dir() or not consentito(p):
        raise PermissionError("cartella non disponibile nel visore")
    voci = []
    with os.scandir(p) as it:
        for v in it:
            if _nascosto(v) or not consentito(v.path):
                continue
            try:
                cartella = v.is_dir()
                st = v.stat()
            except OSError:
                continue
            voci.append({"nome": v.name, "percorso": v.path, "tipo": "cartella" if cartella else tipo_di(v.name),
                         "dimensione": 0 if cartella else st.st_size, "modificato": int(st.st_mtime)})
            if len(voci) >= massimo * 3:
                break
    voci.sort(key=lambda x: (x["tipo"] != "cartella", x["nome"].lower()))
    su = str(p.parent) if p.parent != p and consentito(p.parent) else ""
    return {"percorso": str(p), "nome": p.name or str(p), "su": su, "voci": voci[:massimo],
            "troncato": len(voci) > massimo}


# ------------------------------------------------------------------ cartelle a parole
def _norma(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


_PAROLE_VUOTE = {"la", "il", "lo", "le", "i", "gli", "cartella", "cartelle", "del", "della", "dei", "delle", "di",
                 "sul", "sulla", "nel", "nella", "mia", "mie", "mio", "miei", "folder"}
_SIMILI = {"foto": ["foto", "photo", "photos", "immagini", "pictures", "camera", "rullino"],
           "immagini": ["immagini", "pictures", "foto", "photos"], "video": ["video", "videos", "filmati"],
           "musica": ["musica", "music"], "giochi": ["giochi", "games", "game", "steam", "steamlibrary"]}


def risolvi_cartella(nome, dove=""):
    """La cartella più somigliante a `nome` (es. "foto") dentro `dove` (es. "desktop"; vuoto: dappertutto)."""
    nome = str(nome or "").strip().strip('"')
    if nome and os.path.isabs(os.path.expandvars(nome)):
        p = Path(os.path.expandvars(nome))
        return str(p) if p.is_dir() and consentito(p) else None
    parole = [w for w in _norma(nome).split() if w not in _PAROLE_VUOTE]
    dove = " ".join(w for w in _norma(dove).split() if w not in _PAROLE_VUOTE)   # "sul desktop" -> "desktop"
    # "la cartella Download" (anche con dove="download"): è la cartella di Windows, non una sottocartella che si
    # chiama così (2026-11-25: apriva ...\Fluffy Mod Manager\TmpDownload invece di Download)
    if len(parole) == 1 and SINONIMI.get(parole[0]) and (not dove or SINONIMI.get(dove) == SINONIMI[parole[0]]):
        nota = cartella_nota(parole[0])
        if nota and nota.is_dir():
            return str(nota)
    if dove:
        base = cartella_nota(dove) if SINONIMI.get(_norma(dove)) else None
        if base is None:
            trovata = risolvi_cartella(dove)
            base = Path(trovata) if trovata else None
        basi = [base] if base else []
    else:
        basi = [cartella_nota(k) for k in ("desktop", "immagini", "documenti", "download", "video", "musica")]
        basi = [b for b in basi if b] + [Path.home()] + dischi()
    basi = [b for b in basi if b and b.is_dir()]
    if not parole:
        return str(basi[0]) if basi else None
    if len(parole) == 1 and not dove and SINONIMI.get(parole[0]) and cartella_nota(parole[0]):
        nota = cartella_nota(parole[0])          # "le immagini", "i download": la cartella di Windows
        if nota.is_dir():
            return str(nota)
    varianti = [[x for x in _SIMILI.get(w, [w])] for w in parole]
    migliore, punti_migliori = None, 0
    visitate = 0
    for base in basi:
        coda = [(base, 0)]
        while coda and visitate < 6000:
            cartella, livello = coda.pop(0)
            try:
                figli = [v for v in os.scandir(cartella) if v.is_dir() and not _nascosto(v)]
            except OSError:
                continue
            for v in figli:
                visitate += 1
                n = _norma(v.name)
                punti = 0
                for alternative in varianti:
                    if any(n == a for a in alternative):
                        punti += 10
                    elif any(n.startswith(a) or a in n.split() for a in alternative):
                        punti += 6
                    elif any(a in n for a in alternative):
                        punti += 3
                    else:
                        punti -= 4
                punti -= livello * 2 + basi.index(base) * 0.5
                if punti > punti_migliori and consentito(v.path):
                    migliore, punti_migliori = v.path, punti
                if livello < 2:
                    coda.append((Path(v.path), livello + 1))
    return migliore


# ------------------------------------------------------------------ anteprime e file
_anteprime = OrderedDict()
_blocco = threading.Lock()


def anteprima(percorso, lato=320):
    """JPEG dell'immagine ridotta al lato dato (girata secondo l'EXIF). In cache per le ultime 400."""
    p = Path(percorso)
    if not p.is_file() or not consentito(p) or tipo_di(p.name) != "immagine":
        raise PermissionError("immagine non disponibile")
    lato = max(64, min(int(lato), 2560))
    chiave = (str(p), p.stat().st_mtime, lato)
    with _blocco:
        if chiave in _anteprime:
            _anteprime.move_to_end(chiave)
            return _anteprime[chiave]
    from PIL import Image, ImageOps
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((lato, lato))
        if im.mode not in ("RGB", "L"):
            fondo = Image.new("RGB", im.size, (8, 14, 20))
            fondo.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = fondo
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82 if lato > 600 else 74, optimize=True)
        dati = out.getvalue()
    with _blocco:
        _anteprime[chiave] = dati
        while len(_anteprime) > 400:
            _anteprime.popitem(last=False)
    return dati


def tipo_mime(percorso):
    e = os.path.splitext(str(percorso))[1].lower()
    if e in VIDEO:
        return VIDEO[e]
    if e in TESTI:
        return "text/plain; charset=utf-8"
    return {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac",
            ".pdf": "application/pdf"}.get(e, "application/octet-stream")


def leggibile(percorso):
    p = Path(percorso)
    if not p.is_file() or not consentito(p):
        raise PermissionError("file non disponibile nel visore")
    return p


def apri_sul_pc(percorso):
    p = leggibile(percorso) if Path(percorso).is_file() else Path(percorso)
    if not p.exists() or not consentito(p):
        raise PermissionError("non disponibile")
    if p.is_file() and p.suffix.lower() in ESEGUIBILI:
        raise PermissionError("programmi e script non si avviano dal visore: chiedilo a Jarvis")
    os.startfile(str(p))   # noqa: S606 (solo Windows; l'estensione è già stata controllata)


# ------------------------------------------------------------------ finestre di Windows
if WINDOWS:
    import ctypes
    from ctypes import wintypes as wt
    user32, gdi32, dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # coordinate in pixel veri
    except (AttributeError, OSError):
        pass

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]


def _titolo(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _rettangolo(hwnd):
    r = wt.RECT()
    # i bordi veri, senza l'ombra invisibile di Windows 10/11 (DWMWA_EXTENDED_FRAME_BOUNDS = 9)
    if dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def finestre():
    """Finestre aperte e visibili, la prima è quella davanti."""
    if not WINDOWS:
        return []
    trovate, davanti = [], user32.GetForegroundWindow()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def ognuna(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
            return True
        nascosta = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(nascosta), ctypes.sizeof(nascosta))   # cloaked
        stile = user32.GetWindowLongW(hwnd, -20)
        titolo = _titolo(hwnd)
        if nascosta.value or stile & 0x80 or not titolo or titolo in ("Program Manager",):   # WS_EX_TOOLWINDOW
            return True
        l, t, r, b = _rettangolo(hwnd)
        processo = ""
        try:
            import psutil
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            processo = psutil.Process(pid.value).name()
        except Exception:
            pass
        trovate.append({"hwnd": int(hwnd), "titolo": titolo, "processo": processo, "davanti": hwnd == davanti,
                        "ridotta": bool(user32.IsIconic(hwnd)), "larghezza": r - l, "altezza": b - t})
        return True
    user32.EnumWindows(ognuna, 0)
    trovate.sort(key=lambda f: not f["davanti"])
    return trovate


def trova_finestra(titolo):
    t = _norma(titolo)
    for f in finestre():
        if t and (t in _norma(f["titolo"]) or t in _norma(f["processo"])):
            return f
    return None


def _jpeg(im, lato, qualita=72):
    im.thumbnail((lato, lato))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=qualita)
    return out.getvalue()


def cattura_finestra(hwnd, lato=1600):
    """JPEG della finestra, anche se è coperta da altre (PrintWindow con PW_RENDERFULLCONTENT)."""
    from PIL import Image
    hwnd = int(hwnd)
    if not user32.IsWindow(hwnd):
        raise LookupError("finestra chiusa")
    if user32.IsIconic(hwnd):
        raise LookupError("finestra ridotta a icona: portala davanti")
    l, t, r, b = _rettangolo(hwnd)
    wl = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wl))
    w, h = wl.right - wl.left, wl.bottom - wl.top
    if w <= 0 or h <= 0:
        raise LookupError("finestra vuota")
    dc_finestra = user32.GetWindowDC(hwnd)
    dc = gdi32.CreateCompatibleDC(dc_finestra)
    bmp = gdi32.CreateCompatibleBitmap(dc_finestra, w, h)
    vecchio = gdi32.SelectObject(dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, dc, 2)
        bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    finally:
        gdi32.SelectObject(dc, vecchio)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(hwnd, dc_finestra)
    im = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    # si toglie l'ombra invisibile: si tiene solo il rettangolo vero della finestra
    im = im.crop((l - wl.left, t - wl.top, w - (wl.right - r), h - (wl.bottom - b)))
    if not ok or not im.getbbox():
        import mss
        with mss.mss() as m:
            s = m.grab({"left": l, "top": t, "width": r - l, "height": b - t})
            im = Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX")
    return _jpeg(im, lato)


def cattura_schermo(numero=1, lato=1920):
    from PIL import Image
    import mss
    with mss.mss() as m:
        monitor = m.monitors[numero if 1 <= numero < len(m.monitors) else 1]
        s = m.grab(monitor)
        return _jpeg(Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX"), lato)


def _misura_ridotta(w, h, lato):
    k = min(1.0, lato / max(w, h))
    return max(1, round(w * k)), max(1, round(h * k))


def _cattura_gdi(l, t, w, h, lato):
    """Zona dello schermo già ridotta da Windows mentre la legge (StretchBlt): misurato su uno schermo 4K ~80 ms,
    contro ~180 ms di cattura intera + conversione + riduzione."""
    from PIL import Image
    W, H = _misura_ridotta(w, h, lato)
    dc_schermo = user32.GetDC(0)
    dc = gdi32.CreateCompatibleDC(dc_schermo)
    bmp = gdi32.CreateCompatibleBitmap(dc_schermo, W, H)
    vecchio = gdi32.SelectObject(dc, bmp)
    try:
        gdi32.SetStretchBltMode(dc, 3)   # COLORONCOLOR: veloce (HALFTONE è più morbido ma ~115 ms)
        if not gdi32.StretchBlt(dc, 0, 0, W, H, dc_schermo, l, t, w, h, 0x00CC0020):   # SRCCOPY
            raise OSError("StretchBlt non riuscito (schermo del PC spento o in standby?)")
        bih = _BIH(ctypes.sizeof(_BIH), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(W * H * 4)
        gdi32.GetDIBits(dc, bmp, 0, H, buf, ctypes.byref(bih), 0)
    finally:
        gdi32.SelectObject(dc, vecchio)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(0, dc_schermo)
    return Image.frombuffer("RGB", (W, H), buf, "raw", "BGRX", 0, 1)


class _CatturaDxgi:
    """Cattura dalla scheda video (DXGI Desktop Duplication con dxcam): 30-60 fotogrammi al secondo anche in 4K.
    Solo se dxcam è installato (pip install dxcam opencv-python-headless); se no, o se fallisce, si usa GDI."""

    def __init__(self):
        import dxcam
        self.cam = dxcam.create(output_color="RGB")
        if self.cam is None:
            raise OSError("dxcam: nessuno schermo")
        self.ultima, self.chiave = None, None

    def zona(self, l, t, w, h, lato):
        """None se sullo schermo non è cambiato niente e non c'è ancora un'immagine di questa zona."""
        import numpy as np
        from PIL import Image
        chiave = (l, t, w, h, lato)
        # anche a schermo fermo (niente fotogrammi nuovi) si vuole l'immagine: new_frame_only=False dà l'ultima.
        # Il primissimo fotogramma può mancare: si riprova per mezzo secondo
        f = None
        for _ in range(25):
            f = self.cam.grab(region=(l, t, l + w, t + h), new_frame_only=False)
            if f is not None:
                break
            time.sleep(0.02)
        if f is None:                       # niente di nuovo sullo schermo: si rimanda l'ultima della stessa zona
            return self.ultima if self.chiave == chiave else None
        W, H = _misura_ridotta(w, h, lato)
        try:
            import cv2
            f = cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA)
            im = Image.fromarray(np.ascontiguousarray(f))
        except ImportError:
            im = Image.fromarray(f).resize((W, H), Image.BILINEAR)
        self.ultima, self.chiave = im, chiave
        return im


_dxgi = {"cattura": None, "provata": False, "blocco": threading.Lock()}   # una sola cattura DXGI alla volta


def _cattura_zona(l, t, w, h, lato):
    # dxcam solo nel processo di cattura (jarvis/cattura.py): dentro Jarvis faceva cadere tutto (0xC0000409)
    if not _dxgi["provata"] and os.environ.get("JARVIS_CATTURA_DXGI") != "1":
        _dxgi["provata"] = True
    if not _dxgi["provata"]:
        _dxgi["provata"] = True
        try:
            _dxgi["cattura"] = _CatturaDxgi()
        except Exception:
            _dxgi["cattura"] = None
    if _dxgi["cattura"] is not None:
        try:
            with _dxgi["blocco"]:
                im = _dxgi["cattura"].zona(l, t, w, h, lato)
            if im is not None:
                return im
        except Exception:
            _dxgi["cattura"] = None             # non va: da qui in poi GDI
    return _cattura_gdi(l, t, w, h, lato)       # (o solo per questo fotogramma: schermo fermo, zona nuova)


def diretta(numero=0, hwnd=0, lato=1280, fps=30, qualita=72):
    """Fotogrammi JPEG continui di uno schermo o di una finestra (modalità cinema/gioco di Jarvis XR): la zona dello
    schermo dove sta la finestra, presa com'è sullo schermo (anche giochi in finestra senza bordi, che PrintWindow
    vede neri). Con dxcam installato si cattura dalla scheda video (fluida); se no GDI ridotto (~12 al secondo su
    uno schermo 4K). Schermo spento: per una finestra si ripiega su PrintWindow. Generatore: si ferma quando chi
    legge smette."""
    import time as _t
    import mss
    intervallo = 1 / max(5, min(60, int(fps)))
    hwnd = int(hwnd or 0)
    with mss.mss() as m:
        schermo = m.monitors[numero if 1 <= numero < len(m.monitors) else 1]
    while True:
        t0 = _t.monotonic()
        if hwnd:
            if not user32.IsWindow(hwnd):
                raise LookupError("finestra chiusa")
            if user32.IsIconic(hwnd):
                _t.sleep(0.3)
                continue
            l, t, r, b = _rettangolo(hwnd)
            if r - l < 8 or b - t < 8:
                _t.sleep(0.3)
                continue
            zona = (l, t, r - l, b - t)
        else:
            zona = (schermo["left"], schermo["top"], schermo["width"], schermo["height"])
        try:
            im = _cattura_zona(*zona, lato)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=qualita)
            yield out.getvalue()
        except OSError:
            # schermo del PC spento o in standby: la cattura dello schermo fallisce, PrintWindow no
            if not hwnd:
                raise LookupError("lo schermo del PC non si può catturare (spento o in standby?)")
            yield cattura_finestra(hwnd, lato)
        resto = intervallo - (_t.monotonic() - t0)
        if resto > 0:
            _t.sleep(resto)


def diretta_isolata(numero=0, hwnd=0, lato=1280, fps=30):
    """Come diretta(), ma con dxcam (se c'è) in un processo a parte (jarvis/cattura.py): se quello cade, si
    continua qui con GDI. Senza dxcam: diretta() con GDI e basta."""
    import importlib.util
    import subprocess
    import sys
    if not importlib.util.find_spec("dxcam"):
        yield from diretta(numero, hwnd, lato, fps)
        return
    proc = subprocess.Popen([sys.executable, "-m", "jarvis.cattura", str(int(numero)), str(int(hwnd)), str(int(lato)),
                             str(int(fps))], cwd=str(Path(__file__).resolve().parent.parent),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            creationflags=0x08000000)   # CREATE_NO_WINDOW
    try:
        while True:
            testa = proc.stdout.read(4)
            if len(testa) < 4:
                break                                   # il processo di cattura è finito o caduto
            jpeg = proc.stdout.read(int.from_bytes(testa, "big"))
            if not jpeg.startswith(b"\xff\xd8"):
                break
            yield jpeg
    finally:
        proc.kill()
        proc.wait(5)
    if hwnd and not user32.IsWindow(int(hwnd)):
        raise LookupError("finestra chiusa")
    yield from diretta(numero, hwnd, lato, fps)         # ripiego: GDI qui dentro


def porta_davanti(hwnd):
    hwnd = int(hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)          # Alt: Windows permette di cambiare il primo piano
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 0x0002, 0)
    time.sleep(0.08)


def schermo_in_pixel(hwnd, x, y):
    """Da coordinate relative (0..1) sulla finestra (o sullo schermo 1 con hwnd=0) a pixel dello schermo."""
    if hwnd:
        l, t, r, b = _rettangolo(int(hwnd))
    else:
        l, t, r, b = 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x, y = max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))
    return int(l + x * (r - l - 1)), int(t + y * (b - t - 1))


def azione(hwnd, tipo, x=0.5, y=0.5, scatti=0, testo="", tasti=""):
    """Clic, doppio clic, clic destro, rotella, testo o tasti sulla finestra (o sullo schermo 1 con hwnd=0)."""
    from . import tastiera
    hwnd = int(hwnd or 0)
    if hwnd and not user32.IsWindow(hwnd):
        raise LookupError("finestra chiusa")
    if hwnd:
        porta_davanti(hwnd)
    if tipo == "davanti":
        return "davanti"
    if tipo in ("clic", "doppio", "destro", "rotella"):
        px, py = schermo_in_pixel(hwnd, x, y)
        user32.SetCursorPos(px, py)
        time.sleep(0.03)
        if tipo == "rotella":
            user32.mouse_event(0x0800, 0, 0, ctypes.c_uint32(int(scatti) * 120).value, 0)
            return f"rotella {scatti}"
        giu, su = (0x0008, 0x0010) if tipo == "destro" else (0x0002, 0x0004)
        for _ in range(2 if tipo == "doppio" else 1):
            user32.mouse_event(giu, 0, 0, 0, 0)
            user32.mouse_event(su, 0, 0, 0, 0)
            time.sleep(0.05)
        return f"{tipo} in {px},{py}"
    if tipo == "testo":
        tastiera.scrivi(str(testo)[:2000])
        return "scritto"
    if tipo == "tasti":
        codici = [_VK[k] for k in str(tasti).lower().replace(" ", "").split("+") if k in _VK]
        if not codici:
            raise ValueError("tasti sconosciuti")
        tastiera.premi(*codici)
        return f"premuto {tasti}"
    raise ValueError("azione sconosciuta")


_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 0x09, "esc": 0x1B, "enter": 0x0D, "invio": 0x0D,
       "space": 0x20, "spazio": 0x20, "backspace": 0x08, "delete": 0x2E, "canc": 0x2E, "home": 0x24, "end": 0x23,
       "pgup": 0x21, "pgdn": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "f5": 0x74, "f11": 0x7A}
_VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
