"""Pannello del PC per lo SHIELD Tablet.

Legge i sensori da MSI Afterburner (memoria condivisa MAHMSharedMemory, la stessa che usa
RTSS per l'overlay) e li manda al tablet una volta al secondo. Se Afterburner è chiuso
ripiega su psutil e nvidia-smi (senza FPS e senza temperatura CPU).
Dal tablet arrivano i comandi: aprire un'app della griglia, premere una scorciatoia,
tasti multimediali. Le app e le scorciatoie stanno in config.json e si cambiano dal
pannello (matita in alto a destra) oppure a mano.

Solo libreria standard + psutil. Avvio:  pythonw pannello.py
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import psutil
except ImportError:  # il pannello funziona lo stesso, con meno dati
    psutil = None

QUI = Path(__file__).resolve().parent
WEB = QUI.parent / "web"
CONFIG = QUI / "config.json"
ICONE = QUI / "cache-icone"
REGISTRO = QUI / "pannello.log"
PORTA = 47800
SENZA_FINESTRA = 0x08000000  # CREATE_NO_WINDOW

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.OpenFileMappingW.restype = wt.HANDLE
kernel32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def log(*parti):
    riga = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parti)
    try:
        with open(REGISTRO, "a", encoding="utf-8") as f:
            f.write(riga + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- configurazione

SCORCIATOIE_PREDEFINITE = [
    {"nome": "Screenshot", "tasti": "win+shift+s", "icona": "📷"},
    {"nome": "Clip 30s", "tasti": "alt+f10", "icona": "🎬"},
    {"nome": "Overlay NVIDIA", "tasti": "alt+z", "icona": "🎮"},
    {"nome": "Muto Discord", "tasti": "ctrl+shift+m", "icona": "🎙️"},
    {"nome": "Desktop", "tasti": "win+d", "icona": "🖥️"},
    {"nome": "Task Manager", "tasti": "ctrl+shift+esc", "icona": "📊"},
]
APP_PREFERITE = ["Steam", "Discord", "Spotify", "Google Chrome", "Firefox", "OBS Studio",
                 "Battle.net", "Epic Games Launcher", "Telegram Desktop", "MSI Afterburner"]


def cartelle_start():
    return [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"]


SCARTA = ("uninstall", "disinstalla", "readme", "leggimi", "help", "guida", "manual", "website",
          "sito web", "license", "licenza", "release notes", "changelog")


def elenco_app():
    """Collegamenti del menu Start: sono le uniche app che il pannello può aprire."""
    trovate = {}
    for radice in cartelle_start():
        if not radice.is_dir():
            continue
        for p in list(radice.rglob("*.lnk")) + list(radice.rglob("*.url")):
            nome = p.stem.strip()
            if any(s in nome.lower() for s in SCARTA):
                continue
            trovate.setdefault(nome.lower(), {"nome": nome, "target": str(p)})
    return sorted(trovate.values(), key=lambda a: a["nome"].lower())


def config_predefinita():
    app = elenco_app()
    per_nome = {a["nome"].lower(): a for a in app}
    scelte = [per_nome[n.lower()] for n in APP_PREFERITE if n.lower() in per_nome][:6]
    return {
        "token": secrets.token_urlsafe(18),
        "token_pc": secrets.token_urlsafe(24),
        "porta": PORTA,
        "app": [{"id": f"a{i}", "nome": a["nome"], "target": a["target"]} for i, a in enumerate(scelte)],
        "scorciatoie": [dict(s, id=f"s{i}") for i, s in enumerate(SCORCIATOIE_PREDEFINITE)],
    }


_blocco_config = threading.Lock()
APP_MONITOR_PREFERITE = ["Claude", "Firefox", "Google Chrome", "Esplora file", "File Explorer", "WhatsApp",
                         "Telegram", "Discord", "Visual Studio Code"]


def app_monitor_predefinite():
    noti = {a["nome"].lower(): a for a in elenco_app() + app_store()}
    scelte, viste = [], set()
    for n in APP_MONITOR_PREFERITE:
        a = noti.get(n.lower())
        if a and a["target"] not in viste and not (n == "File Explorer" and "esplora file" in noti):
            viste.add(a["target"])
            scelte.append({"id": f"m{len(scelte)}", "nome": a["nome"], "target": a["target"]})
    return scelte[:7]


def carica_config():
    with _blocco_config:
        if CONFIG.exists():
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            if "token_pc" not in cfg:
                cfg["token_pc"] = secrets.token_urlsafe(24)
                CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            if "monitor_app" not in cfg:
                cfg["monitor_app"] = app_monitor_predefinite()
                CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            return cfg
        cfg = config_predefinita()
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return cfg


def salva_config(cfg):
    with _blocco_config:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG)


# ---------------------------------------------------------------- tasti

VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
      "tab": 0x09, "esc": 0x1B, "enter": 0x0D, "invio": 0x0D, "space": 0x20, "spazio": 0x20,
      "backspace": 0x08, "printscreen": 0x2C, "stamp": 0x2C, "home": 0x24, "end": 0x23,
      "pgup": 0x21, "pgdn": 0x22, "insert": 0x2D, "delete": 0x2E, "canc": 0x2E,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
      "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
ESTESI = {0x5B, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2C,
          0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}
MODIFICATORI = {0x11, 0x10, 0x12, 0x5B}


def leggi_tasti(testo):
    """'ctrl+shift+m' -> [0x11, 0x10, 0x4D]; ValueError se non è una combinazione valida."""
    parti = [p.strip().lower() for p in str(testo).split("+") if p.strip()]
    if not parti or len(parti) > 5:
        raise ValueError("combinazione vuota o troppo lunga")
    codici = []
    for p in parti:
        if p not in VK:
            raise ValueError(f"tasto sconosciuto: {p}")
        codici.append(VK[p])
    return codici


def premi(codici):
    def evento(vk, su):
        flag = (0x0001 if vk in ESTESI else 0) | (0x0002 if su else 0)
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), flag, 0)
    for vk in codici:
        evento(vk, False)
        if vk not in MODIFICATORI:
            time.sleep(0.03)
    for vk in reversed(codici):
        evento(vk, True)


# ---------------------------------------------------------------- sensori

class Afterburner:
    """Lettura di MAHMSharedMemory (MSI Afterburner 4.x, versione 2.0 del formato)."""
    NOME = "MAHMSharedMemory"
    NIENTE = 3.0e38  # Afterburner scrive FLT_MAX quando un valore non c'è (es. FPS senza gioco)

    def leggi(self):
        # OpenFileMapping e non mmap: mmap creerebbe la memoria se Afterburner è chiuso,
        # e Afterburner all'avvio troverebbe un blocco vuoto da 32 byte.
        h = kernel32.OpenFileMappingW(0x0004, False, self.NOME)  # FILE_MAP_READ
        if not h:
            return None
        try:
            vista = kernel32.MapViewOfFile(h, 0x0004, 0, 0, 0)
            if not vista:
                return None
            try:
                firma, _ver, hsz, n, esz, _t, ng, gsz = struct.unpack_from("<4sIIIIiII", ctypes.string_at(vista, 32))
                if firma != b"MHAM" or n == 0:  # "MAHM" letto come DWORD little-endian
                    return None
                blocco = ctypes.string_at(vista, hsz + n * esz)
            finally:
                kernel32.UnmapViewOfFile(ctypes.c_void_p(vista))
        finally:
            kernel32.CloseHandle(h)
        valori = {}
        for i in range(n):
            dato, _mn, _mx, _fl, gpu, src = struct.unpack_from("<fffIII", blocco, hsz + i * esz + 260 * 5)
            valori[(src, "tot" if gpu == 0xFFFFFFFF else gpu)] = None if dato > self.NIENTE else dato
        return valori


class Sensori:
    def __init__(self):
        self.ab = Afterburner()
        self.vram_tot = None
        self.ram_tot = psutil.virtual_memory().total / 2**20 if psutil else None
        self.ultimo_smi = 0
        self.smi = {}
        if psutil:
            psutil.cpu_percent(None)
        self._vram_totale()

    def _vram_totale(self):
        smi = shutil.which("nvidia-smi")
        if not smi:
            return
        try:
            out = subprocess.run([smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5, creationflags=SENZA_FINESTRA).stdout
            self.vram_tot = float(out.split()[0])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass

    def _nvidia_smi(self):
        if time.time() - self.ultimo_smi < 2:
            return self.smi
        self.ultimo_smi = time.time()
        smi = shutil.which("nvidia-smi")
        if not smi:
            return {}
        try:
            out = subprocess.run([smi, "--query-gpu=temperature.gpu,utilization.gpu,clocks.gr,power.draw,memory.used,fan.speed",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                 creationflags=SENZA_FINESTRA).stdout
            t, u, c, w, mem, fan = [x.strip() for x in out.splitlines()[0].split(",")]
            num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None
            self.smi = {"temp": num(t), "carico": num(u), "clock": num(c), "watt": num(w),
                        "vram": num(mem), "ventola": num(fan)}
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            self.smi = {}
        return self.smi

    def app_in_primo_piano(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        nome = None
        if psutil:
            try:
                nome = psutil.Process(pid.value).name()
            except (psutil.Error, ValueError):
                pass
        return {"titolo": buf.value[:80], "processo": nome}

    def lettura(self):
        v = self.ab.leggi()
        r = lambda x: None if x is None else round(x, 1)
        if v:
            g = lambda src, gpu=0: v.get((src, gpu))
            dati = {
                "fonte": "afterburner",
                "cpu": {"carico": r(g(0x90, "tot")), "temp": r(g(0x80, "tot")),
                        "clock": r(g(0xA0, "tot")), "watt": r(g(0x100, "tot"))},
                "gpu": {"carico": r(g(0x30)), "temp": r(g(0x00)), "clock": r(g(0x20)), "watt": r(g(0x61)),
                        "vram": r(g(0x31)), "vram_tot": self.vram_tot, "ventola": r(g(0x10))},
                "ram": {"usata": r(g(0x91)), "totale": r(self.ram_tot)},
                "fps": {"ora": r(g(0x50, "tot")), "media": r(g(0x53, "tot")), "low1": r(g(0x55, "tot"))},
            }
        else:
            s = self._nvidia_smi()
            vm = psutil.virtual_memory() if psutil else None
            dati = {
                "fonte": "base",
                "cpu": {"carico": r(psutil.cpu_percent(None)) if psutil else None, "temp": None,
                        "clock": r(psutil.cpu_freq().current) if psutil and psutil.cpu_freq() else None, "watt": None},
                "gpu": {"carico": s.get("carico"), "temp": s.get("temp"), "clock": s.get("clock"),
                        "watt": s.get("watt"), "vram": s.get("vram"), "vram_tot": self.vram_tot,
                        "ventola": s.get("ventola")},
                "ram": {"usata": r(vm.used / 2**20) if vm else None, "totale": r(self.ram_tot)},
                "fps": {"ora": None, "media": None, "low1": None},
            }
        dati["primo_piano"] = self.app_in_primo_piano()
        dati["t"] = time.time()
        return dati


class Campionatore(threading.Thread):
    """Legge i sensori una volta al secondo; i client in streaming aspettano la lettura nuova."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sensori = Sensori()
        self.ultima = None
        self.cond = threading.Condition()

    stato_pc = "acceso"   # "spegnimento" o "sospensione": il tablet spegne lo schermo
    versione_config = 1   # cambia a ogni salvataggio: il pannello ricarica app e scorciatoie

    def config_cambiata(self):
        self.versione_config += 1
        with self.cond:
            self.ultima = dict(self.ultima or {}, config=self.versione_config, t=time.time())
            self.cond.notify_all()

    def avvisa(self, stato):
        """Manda subito al tablet lo stato del PC, senza aspettare la lettura successiva."""
        self.stato_pc = stato
        log("stato PC:", stato)
        with self.cond:
            self.ultima = dict(self.ultima or {}, pc=stato, t=time.time())
            self.cond.notify_all()

    def run(self):
        while True:
            try:
                dati = self.sensori.lettura()
            except Exception as e:  # un sensore che sbaglia non deve fermare il pannello
                log("errore sensori:", repr(e))
                dati = {"errore": str(e), "t": time.time()}
            dati["pc"] = self.stato_pc
            dati["config"] = self.versione_config
            with self.cond:
                self.ultima = dati
                self.cond.notify_all()
            time.sleep(1)


# ---------------------------------------------------------------- spegnimento e sospensione del PC

WM_QUERYENDSESSION, WM_ENDSESSION, WM_POWERBROADCAST = 0x0011, 0x0016, 0x0218
PBT_APMSUSPEND, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC = 0x4, 0x7, 0x12
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
CLASSE_FINESTRA = "PannelloPCAscolto"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


def ascolta_windows(camp):
    """Finestra invisibile che riceve da Windows l'avviso di spegnimento/riavvio e di sospensione,
    così il tablet spegne lo schermo subito invece di aspettare che il PC sparisca."""
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    def procedura(hwnd, msg, wp, lp):
        if msg == WM_QUERYENDSESSION:
            camp.avvisa("spegnimento")
            return 1  # lo spegnimento può proseguire
        if msg == WM_ENDSESSION:
            if wp:
                camp.avvisa("spegnimento")
                time.sleep(2)  # tempo per far arrivare l'avviso al tablet
            else:
                camp.avvisa("acceso")  # spegnimento annullato
            return 0
        if msg == WM_POWERBROADCAST:
            if wp == PBT_APMSUSPEND:
                camp.avvisa("sospensione")
                time.sleep(2)  # Windows aspetta ~2 s: tempo per far arrivare l'avviso (se non arriva, il tablet
                               # se ne accorge comunque dal silenzio del flusso)
            elif wp in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                camp.avvisa("acceso")
            return 1
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    proc = WNDPROC(procedura)
    camp._procedura = proc  # resta in vita finché gira il server
    classe = WNDCLASSW(lpfnWndProc=proc, lpszClassName=CLASSE_FINESTRA,
                       hInstance=kernel32.GetModuleHandleW(None))
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    if not user32.RegisterClassW(ctypes.byref(classe)):
        log("finestra di ascolto: RegisterClass fallita")
        return
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    hwnd = user32.CreateWindowExW(0, CLASSE_FINESTRA, "Pannello PC", 0, 0, 0, 0, 0, None, None,
                                  classe.hInstance, None)
    if not hwnd:
        log("finestra di ascolto: CreateWindow fallita")
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# ---------------------------------------------------------------- icone

ESTRATTORE_PS = r"""
param([string]$elenco)
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Drawing; using System.Drawing.Imaging; using System.Runtime.InteropServices;
public static class Icona {
  [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItemImageFactory { void GetImage(SIZE s, int flags, out IntPtr hbm); }
  [StructLayout(LayoutKind.Sequential)] struct SIZE { public int cx; public int cy; }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string p, IntPtr bc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
      [MarshalAs(UnmanagedType.Interface)] out IShellItemImageFactory f);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);
  public static void Salva(string sorgente, string png) {
    IShellItemImageFactory f;
    SHCreateItemFromParsingName(sorgente, IntPtr.Zero, typeof(IShellItemImageFactory).GUID, out f);
    IntPtr hbm; SIZE s; s.cx = 128; s.cy = 128;
    f.GetImage(s, 0x1 | 0x4, out hbm);   // BIGGERSIZEOK | ICONONLY
    try {
      using (Bitmap b = Image.FromHbitmap(hbm)) {
        Rectangle r = new Rectangle(0, 0, b.Width, b.Height);
        BitmapData d = b.LockBits(r, ImageLockMode.ReadOnly, b.PixelFormat);
        using (Bitmap a = new Bitmap(d.Width, d.Height, d.Stride, PixelFormat.Format32bppArgb, d.Scan0))
        using (Bitmap c = new Bitmap(a)) { c.Save(png, ImageFormat.Png); }
        b.UnlockBits(d);
      }
    } finally { DeleteObject(hbm); }
  }
}
'@
foreach ($riga in Get-Content -LiteralPath $elenco -Encoding UTF8) {
  $s, $d = $riga -split "`t", 2
  try { [Icona]::Salva($s, $d) } catch { }
}
"""

_blocco_icone = threading.Lock()


def file_icona(target):
    return ICONE / (hashlib.sha1(target.lower().encode("utf-8")).hexdigest()[:16] + ".png")


def estrai_icone(targets, aspetta=True):
    """Estrae le icone mancanti con un solo PowerShell. Con aspetta=False, se un'estrazione è già
    in corso non si mette in coda (la pagina mostra l'iniziale e riprova la volta dopo)."""
    mancanti = [t for t in targets if (os.path.exists(t) or t.lower().startswith("shell:appsfolder\\"))
                and not file_icona(t).exists()]
    if not mancanti:
        return
    if not _blocco_icone.acquire(blocking=aspetta):
        return
    try:
        mancanti = [t for t in mancanti if not file_icona(t).exists()]
        if not mancanti:
            return
        ICONE.mkdir(exist_ok=True)
        script = ICONE / "estrai.ps1"
        script.write_text(ESTRATTORE_PS, encoding="utf-8-sig")
        elenco = ICONE / f"elenco-{threading.get_ident()}.txt"
        elenco.write_text("\n".join(f"{t}\t{file_icona(t)}" for t in mancanti), encoding="utf-8-sig")
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(elenco)],
                           capture_output=True, timeout=300, creationflags=SENZA_FINESTRA)
        except (OSError, subprocess.TimeoutExpired) as e:
            log("icone:", repr(e))
        finally:
            elenco.unlink(missing_ok=True)
    finally:
        _blocco_icone.release()


# ---------------------------------------------------------------- giochi Steam

SCARTA_STEAM = ("redistributable", "proton", "steam linux runtime", "steamworks", "steamvr")
_cache_steam = {"t": 0, "giochi": [], "radice": None}


def radice_steam():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def giochi_steam():
    """Giochi installati, letti da libraryfolders.vdf e dagli appmanifest (niente API di Steam)."""
    if time.time() - _cache_steam["t"] < 120:
        return _cache_steam["giochi"]
    import re
    radice = radice_steam()
    giochi = {}
    if radice and (radice / "steamapps" / "libraryfolders.vdf").exists():
        vdf = (radice / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        librerie = {radice} | {Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf)}
        for lib in librerie:
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    testo = acf.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                appid = re.search(r'"appid"\s+"(\d+)"', testo)
                nome = re.search(r'"name"\s+"([^"]+)"', testo)
                if appid and nome and not any(s in nome.group(1).lower() for s in SCARTA_STEAM):
                    giochi[appid.group(1)] = {"nome": nome.group(1), "target": f"steam://rungameid/{appid.group(1)}",
                                              "tipo": "steam"}
    _cache_steam.update(t=time.time(), radice=radice,
                        giochi=sorted(giochi.values(), key=lambda g: g["nome"].lower()))
    return _cache_steam["giochi"]


def immagine_steam(target):
    """Icona quadrata del gioco dalla cache di Steam (librarycache/<appid>/), se c'è."""
    appid = target.rsplit("/", 1)[-1]
    radice = _cache_steam["radice"] or radice_steam()
    if not radice or not appid.isdigit():
        return None
    cartella = radice / "appcache" / "librarycache" / appid
    if cartella.is_dir():
        quadrate = sorted((f for f in cartella.glob("*.jpg") if len(f.stem) == 40), key=lambda f: f.stat().st_size)
        for f in quadrate + [cartella / "library_600x900.jpg", cartella / "header.jpg"]:
            if f.exists():
                return f
    vecchia = radice / "appcache" / "librarycache" / f"{appid}_icon.jpg"
    return vecchia if vecchia.exists() else None


def catalogo():
    """Tutto quello che si può mettere nella griglia: menu Start + giochi Steam."""
    # i giochi Steam hanno anche un collegamento nel menu Start: vince la voce Steam (icona e avvio migliori)
    voci = list(giochi_steam())
    nomi = {g["nome"].lower() for g in voci}
    voci += [dict(a, tipo="start") for a in elenco_app() if a["nome"].lower() not in nomi]
    nomi |= {a["nome"].lower() for a in voci}
    voci += [dict(a, tipo="start") for a in app_store() if a["nome"].lower() not in nomi]
    return sorted(voci, key=lambda a: a["nome"].lower())


def target_valido_pc(target):
    """Dal companion sul PC si può aggiungere qualsiasi programma, file, cartella o link."""
    import re
    target = str(target or "").strip().strip('"')
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]{1,30}:(//)?\S+$", target) and not re.match(r"^[a-zA-Z]:[\\/]", target):
        return target
    if target and os.path.exists(target):
        return str(Path(target))
    raise ValueError(f"non trovo il file o il link: {target}")


SFOGLIA_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$proprietario = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false; Opacity = 0 }
$proprietario.Show(); $proprietario.Activate()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Pannello PC: scegli un programma, un collegamento o un file'
$d.Filter = 'Programmi e collegamenti|*.exe;*.lnk;*.url;*.bat;*.cmd;*.ps1|Tutti i file|*.*'
$d.InitialDirectory = [Environment]::GetFolderPath('Desktop')
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if ($d.ShowDialog($proprietario) -eq 'OK') { $d.FileName }
$proprietario.Close()
"""


def sfoglia():
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", SFOGLIA_PS],
                       capture_output=True, timeout=600, creationflags=SENZA_FINESTRA)
    return r.stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------- secondo monitor (tablet)

class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wt.DWORD),
                ("szDevice", wt.WCHAR * 32)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
TITOLO_DOCK = "Dock Pannello PC"


def monitor_virtuale(lavoro=True):
    """(left, top, right, bottom) in pixel reali del monitor virtuale di Apollo, se c'è: l'area di lavoro
    (senza dock e taskbar) oppure, con lavoro=False, il monitor intero.
    Conta solo se c'è anche uno schermo vero: con la TV spenta Apollo tiene acceso un monitor virtuale come unico
    schermo del PC (2026-11-15), e lì la dock finiva sull'unico desktop."""
    nomi, fisici, i = set(), 0, 0
    while True:
        d = DISPLAY_DEVICEW()
        d.cb = ctypes.sizeof(d)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        if d.StateFlags & 1:  # collegato al desktop
            if "Virtual Display" in d.DeviceString:
                nomi.add(d.DeviceName)
            else:
                fisici += 1
        i += 1
    if not nomi or not fisici:
        return None
    trovati = []

    def cb(hmon, _hdc, _r, _lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)) and mi.szDevice in nomi:
            w = mi.rcWork if lavoro else mi.rcMonitor
            trovati.append((w.l, w.t, w.r, w.b))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return trovati[0] if trovati else None


def finestre_principali():
    """Finestre di primo livello visibili, con titolo, non di servizio e non nascoste da DWM."""
    risultato = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        nascosta = wt.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(nascosta), ctypes.sizeof(nascosta))
        if nascosta.value:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value != TITOLO_DOCK:
            risultato.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return risultato


def sul_monitor(hwnd, area):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx, cy = (r.l + r.r) // 2, (r.t + r.b) // 2
    return area[0] <= cx < area[2] and area[1] <= cy < area[3]


def metti_davanti(hwnd):
    """Porta la finestra sopra tutte le altre e, se Windows lo permette, in primo piano. Windows dà il primo piano
    solo a chi ha ricevuto l'ultimo input (qui: il tablet o il gioco), quindi: prima la si alza nell'ordine delle
    finestre (sempre in cima e poi no, questo è sempre permesso), poi ci si aggancia all'input di chi è davanti."""
    user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_TOPMOST, NOSIZE|NOMOVE|SHOW
    user32.SetWindowPos(hwnd, wt.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_NOTOPMOST
    davanti = user32.GetForegroundWindow()
    suo = user32.GetWindowThreadProcessId(davanti, None) if davanti else 0
    mio = ctypes.windll.kernel32.GetCurrentThreadId()
    agganciato = bool(suo) and suo != mio and user32.AttachThreadInput(mio, suo, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if agganciato:
            user32.AttachThreadInput(mio, suo, False)
    if user32.GetForegroundWindow() != hwnd:  # ultima strada: un Alt premuto e rilasciato sblocca il primo piano
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def porta_sul_monitor(hwnd, area):
    l, t, r, b = area
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE: una finestra massimizzata altrove non si sposta
    user32.SetWindowPos(hwnd, None, l + 40, t + 40, (r - l) - 80, (b - t) - 80, 0x0004 | 0x0040)  # NOZORDER|SHOWWINDOW
    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE sul monitor dove ora si trova
    metti_davanti(hwnd)


class _GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


def _guid(testo):
    import uuid
    u = uuid.UUID(testo)
    g = _GUID(u.time_low, u.time_mid, u.time_hi_version)
    g.d[:] = list(u.bytes[8:])
    return g


class _PKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wt.DWORD)]


_IID_IPROPERTYSTORE = _guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
_PKEY_APPID = _PKEY(_guid("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)  # PKEY_AppUserModel_ID


def id_app(hwnd):
    """AppUserModelID della finestra (quello usato da shell:AppsFolder): proprietà della finestra (Chrome,
    Firefox, Discord, Telegram...) oppure quello del pacchetto del processo (app dello Store, es. Claude)."""
    try:
        ps = ctypes.c_void_p()
        if ctypes.windll.shell32.SHGetPropertyStoreForWindow(wt.HWND(hwnd), ctypes.byref(_IID_IPROPERTYSTORE),
                                                             ctypes.byref(ps)) == 0 and ps:
            vt = ctypes.cast(ps, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            pv = (ctypes.c_ubyte * 24)()
            try:
                get_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PKEY), ctypes.c_void_p)(vt[5])
                if get_value(ps, ctypes.byref(_PKEY_APPID), ctypes.byref(pv)) == 0 and pv[0] == 31:  # VT_LPWSTR
                    valore = ctypes.wstring_at(ctypes.c_void_p.from_buffer(pv, 8).value)
                    ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))
                    if valore:
                        return valore
            finally:
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(ps)
    except OSError:
        pass
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k32 = ctypes.windll.kernel32
    hp = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return ""
    try:
        n = wt.UINT(260)
        buf = ctypes.create_unicode_buffer(260)
        return buf.value if k32.GetApplicationUserModelId(wt.HANDLE(hp), ctypes.byref(n), buf) == 0 else ""
    finally:
        k32.CloseHandle(wt.HANDLE(hp))


def finestre_dell_app(target):
    """Finestre principali già aperte dell'app shell:AppsFolder\\<id> (dalla più in alto), oppure []."""
    if not target.lower().startswith("shell:appsfolder\\"):
        return []
    voluto = target.split("\\", 1)[1].lower()
    risultato = []
    for h in finestre_principali():
        if voluto == "microsoft.windows.explorer":  # le finestre di Esplora file non hanno un AppUserModelID
            c = ctypes.create_unicode_buffer(32)
            user32.GetClassNameW(h, c, 32)
            if c.value == "CabinetWClass":
                risultato.append(h)
            continue
        i = id_app(h).lower()
        if i and (i == voluto or i.startswith(voluto + ".")):
            risultato.append(h)
    return risultato


def apri_sul_monitor(voce):
    """Apre l'app e porta la sua finestra (nuova, o quella che si è messa in primo piano) sul monitor del tablet."""
    area = monitor_virtuale()
    CustodeGioco.apertura_fino = time.time() + 25
    try:
        gia_aperte = finestre_dell_app(voce["target"]) if area else []
    except Exception as e:
        log("finestre dell'app:", repr(e))
        gia_aperte = []
    if gia_aperte:
        # app già aperta: si sposta la sua finestra (2026-11-15: con Claude aperto sul monitor principale il tocco
        # sulla dock non faceva comparire niente e bisognava cliccarla sulla barra delle applicazioni)
        log("porto sul secondo monitor", voce["nome"], "(già aperta)")
        sistema_sul_monitor(gia_aperte[0], area)
        return
    prima = set(finestre_principali())
    davanti = user32.GetForegroundWindow()
    os.startfile(voce["target"])
    log("apro sul secondo monitor", voce["nome"])
    if not area:
        return
    inizio = time.time()
    scelta = None
    while time.time() - inizio < 20 and not scelta:
        time.sleep(0.3)
        nuove = [h for h in finestre_principali() if h not in prima]
        if nuove:
            sue = set(finestre_dell_app(voce["target"]))
            scelta = ([h for h in nuove if h in sue] or nuove)[0]
        elif time.time() - inizio > 2.5:
            ora = user32.GetForegroundWindow()
            if ora and ora != davanti and ora in set(finestre_principali()):
                scelta = ora   # app già aperta: Windows ha solo portato davanti la sua finestra
    if not scelta:
        log("nessuna finestra trovata per", voce["nome"])
        return
    sistema_sul_monitor(scelta, area)


def sistema_sul_monitor(scelta, area):
    CustodeGioco.apertura_fino = time.time() + 6  # il custode del gioco aspetta che l'app sia sistemata
    for _ in range(3):  # alcune app (Electron) si rimettono dov'erano subito dopo la comparsa
        porta_sul_monitor(scelta, area)
        time.sleep(1.2)
        if sul_monitor(scelta, area):
            break
    CustodeGioco.apertura_fino = time.time() + 0.5


class Dock(threading.Thread):
    """Apre la dock sul monitor virtuale quando compare e la chiude quando sparisce."""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.processo = None
        self.area = None

    def run(self):
        while True:
            try:
                area = monitor_virtuale(lavoro=False)  # la dock stessa cambia l'area di lavoro
                vivo = self.processo and self.processo.poll() is None
                if area and (not vivo or area != self.area):
                    self.chiudi()
                    self.area = area
                    self.processo = subprocess.Popen(
                        [sys.executable, str(QUI / "dock.py"), str(PORTA), self.cfg["token_pc"], ",".join(map(str, area))],
                        creationflags=SENZA_FINESTRA)
                    log("dock aperta sul secondo monitor", area)
                elif not area and vivo:
                    self.chiudi()
                    log("dock chiusa")
            except Exception as e:
                log("dock:", repr(e))
            time.sleep(2)

    def chiudi(self):
        if self.processo and self.processo.poll() is None:
            self.processo.terminate()
        self.processo = None


# ---------------------------------------------------------------- custode del gioco

user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]


class DecisioneGioco:
    """La parte che decide, senza chiamate a Windows (si prova con tests/test_custode.py).

    Un gioco a schermo intero è in primo piano su uno schermo vero; un tocco sul secondo monitor (Artemis muove
    il mouse del PC lì e clicca) porta davanti la finestra toccata e il gioco perde i comandi. Se, quando il gioco
    perde il primo piano, il cursore è sul monitor virtuale, il gioco va ridato appena il tocco è finito: cursore
    fermo per FERMO secondi o tornato fuori dal monitor virtuale. Se invece il gioco lo lasci tu (Alt+Tab, tasto
    Windows) il cursore non è sul monitor virtuale e non si fa niente."""

    FERMO = 1.2

    def __init__(self):
        self.gioco = None
        self.perso_alle = 0
        self.cursore = None
        self.fermo_da = 0

    def passo(self, ora, davanti, davanti_gioco, gioco_esiste, cursore, cursore_sul_virtuale, apertura):
        """davanti_gioco: la finestra davanti è a schermo intero su uno schermo vero. Ritorna la finestra del gioco
        da rimettere davanti, oppure None."""
        if davanti_gioco:
            self.gioco, self.perso_alle = davanti, 0
            return None
        if not self.gioco or not davanti or davanti == self.gioco:
            return None
        if not gioco_esiste:
            self.gioco = None
            return None
        if not self.perso_alle:
            if not cursore_sul_virtuale:
                self.gioco = None  # lasciato apposta: lo si riprende quando torna davanti
                return None
            self.perso_alle, self.cursore, self.fermo_da = ora, cursore, ora
            return None
        if apertura or cursore != self.cursore:
            self.cursore, self.fermo_da = cursore, ora
            if cursore_sul_virtuale or apertura:
                return None
        if cursore_sul_virtuale and ora - self.fermo_da < self.FERMO:
            return None
        self.perso_alle = 0
        return self.gioco


class CustodeGioco(threading.Thread):
    """Ridà il primo piano al gioco a schermo intero dopo un tocco sul secondo monitor (2026-11-15: in FC 27 un
    tocco sul tablet con Artemis toglieva i comandi al gioco e serviva Alt+Tab). Si spegne con
    "torna_al_gioco": false in config.json."""

    apertura_fino = 0  # apri_sul_monitor sta sistemando una finestra: non la si disturba

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.decisione = DecisioneGioco()
        self.area, self.area_letta = None, 0

    def run(self):
        while True:
            time.sleep(0.15)
            try:
                self.passo()
            except Exception as e:
                log("custode del gioco:", repr(e))
                time.sleep(5)

    def passo(self):
        ora = time.time()
        if ora - self.area_letta > 2:
            self.area, self.area_letta = monitor_virtuale(lavoro=False), ora
        if not self.area or self.cfg.get("torna_al_gioco") is False:
            self.decisione.gioco = None
            return
        davanti = user32.GetForegroundWindow()
        p = wt.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        cursore = (p.x, p.y)
        sul_virtuale = self.area[0] <= p.x < self.area[2] and self.area[1] <= p.y < self.area[3]
        gioco = self.decisione.gioco
        da_ridare = self.decisione.passo(
            ora, davanti, bool(davanti) and self.a_schermo_intero(davanti),
            bool(gioco) and bool(user32.IsWindow(gioco)), cursore, sul_virtuale, ora < CustodeGioco.apertura_fino)
        if da_ridare:
            self.ridai(da_ridare)

    def a_schermo_intero(self, hwnd):
        """Finestra senza barra del titolo grande quanto il suo monitor, su uno schermo vero (non il desktop)."""
        if user32.GetWindowLongW(hwnd, -16) & 0x00C00000 == 0x00C00000:  # WS_CAPTION
            return False
        c = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, c, 64)
        if c.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        r, m = RECT(), self.monitor_di(hwnd)
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if not m or (r.l, r.t, r.r, r.b) != m:
            return False
        return m != self.area

    @staticmethod
    def monitor_di(hwnd):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
            return None
        return (mi.rcMonitor.l, mi.rcMonitor.t, mi.rcMonitor.r, mi.rcMonitor.b)

    def ridai(self, gioco):
        if user32.IsIconic(gioco):
            user32.ShowWindow(gioco, 9)  # SW_RESTORE
        m = self.monitor_di(gioco)
        if m:
            user32.SetCursorPos((m[0] + m[2]) // 2, (m[1] + m[3]) // 2)
        riuscito = metti_davanti(gioco)
        log("gioco rimesso in primo piano dopo un tocco sul secondo monitor",
            "(riuscito)" if riuscito else "(Windows non l'ha permesso)")


# ---------------------------------------------------------------- app moderne (Microsoft Store / MSIX)

_cache_store = {"t": 0, "app": []}


def app_store():
    """App del menu Start che non hanno un collegamento .lnk (es. Claude, WhatsApp): si aprono con shell:AppsFolder."""
    if time.time() - _cache_store["t"] < 600:
        return _cache_store["app"]
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                           capture_output=True, timeout=30, creationflags=SENZA_FINESTRA)
        voci = json.loads(r.stdout.decode("utf-8") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        log("app store:", repr(e))
        voci = []
    app = []
    for v in voci if isinstance(voci, list) else [voci]:
        nome, aid = v.get("Name", ""), v.get("AppID", "")
        # gli AppID con percorso o URL sono già coperti dai collegamenti del menu Start
        if not nome or not aid or "\\" in aid or "/" in aid or any(s in nome.lower() for s in SCARTA):
            continue
        app.append({"nome": nome, "target": "shell:AppsFolder\\" + aid})
    _cache_store.update(t=time.time(), app=app)
    return app


# ---------------------------------------------------------------- server HTTP

TIPI = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
        ".woff2": "font/woff2", ".json": "application/json", ".jpg": "image/jpeg"}


def rete_locale(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
    except ValueError:
        return False


class Gestore(BaseHTTPRequestHandler):
    server_version = "Pannello/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # -- risposte
    def manda(self, codice, corpo=b"", tipo="application/json", extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        elif isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        intestazioni = {"Cache-Control": "no-store" if tipo == "application/json" else "no-cache"}
        intestazioni.update(extra or {})
        for k, v in intestazioni.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def livello(self, q):
        """'pc' con la chiave del companion (e solo dal PC stesso), 'tablet' con quella del pannello."""
        token = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        cfg = self.server.cfg
        if token and secrets.compare_digest(token, cfg["token_pc"]) \
                and ipaddress.ip_address(self.client_address[0]).is_loopback:
            return "pc"
        if token and secrets.compare_digest(token, cfg["token"]):
            if not cfg.get("abbinato"):  # primo tablet arrivato con la chiave: l'abbinamento si chiude
                cfg["abbinato"] = True
                salva_config(cfg)
                Abbinamento.chiudi()
                log("tablet abbinato:", self.client_address[0])
            return "tablet"
        return None

    def corpo_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise ValueError("richiesta troppo grande")
        return json.loads(self.rfile.read(n) or b"{}")

    # -- GET
    def do_GET(self):
        if not rete_locale(self.client_address[0]):
            return self.manda(403, {"errore": "solo rete locale"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not u.path.startswith("/api/"):
            return self.statico(u.path)
        if u.path == "/api/abbina":
            # tablet collegato via USB (adb reverse: arriva da 127.0.0.1) mentre l'abbinamento è aperto
            if not ipaddress.ip_address(self.client_address[0]).is_loopback or not Abbinamento.aperto():
                return self.manda(403, {"errore": "abbinamento chiuso: aprilo dalle impostazioni sul PC"})
            return self.manda(200, Abbinamento.risposta(self.server.cfg, con_chiave=True))
        livello = self.livello(q)
        if not livello:
            return self.manda(401, {"errore": "token mancante o sbagliato"})
        cfg = self.server.cfg
        if u.path == "/api/stato":
            return self.manda(200, self.server.campionatore.ultima or {})
        if u.path == "/api/flusso":
            return self.flusso()
        if u.path == "/api/config":
            return self.manda(200, {"app": cfg["app"], "scorciatoie": cfg["scorciatoie"],
                                    "monitor": bool(cfg.get("monitor")),
                                    "monitor_app": cfg.get("monitor_app", []),
                                    "versione": self.server.campionatore.versione_config})
        if u.path == "/api/elenco-app":
            return self.manda(200, catalogo())
        if u.path == "/api/icona":
            return self.icona((q.get("target") or [""])[0])
        if livello == "pc" and u.path == "/api/pc/stato":
            ora = time.time()
            return self.manda(200, {"tablet": [{"ip": ip, "secondi_fa": round(ora - t)}
                                               for ip, t in self.server.tablet.items() if ora - t < 30],
                                    "porta": PORTA, "abbinamento": Abbinamento.secondi_rimasti()})
        return self.manda(404, {"errore": "non trovato"})

    def icona(self, target):
        cfg = self.server.cfg
        noti = {a["target"] for a in cfg["app"] + cfg.get("monitor_app", [])} | {a["target"] for a in catalogo()}
        if target not in noti:
            return self.manda(404, {"errore": "icona sconosciuta"})
        if target.startswith("steam://rungameid/"):
            f = immagine_steam(target)
            if f:
                return self.manda(200, f.read_bytes(), "image/jpeg", {"Cache-Control": "max-age=86400"})
            return self.manda(404, {"errore": "icona non disponibile"})
        f = file_icona(target)
        if not f.exists():
            estrai_icone([target], aspetta=False)
        if not f.exists():
            return self.manda(404, {"errore": "icona non disponibile"})
        return self.manda(200, f.read_bytes(), "image/png", {"Cache-Control": "max-age=86400"})

    def statico(self, percorso):
        nome = {"/": "index.html", "": "index.html", "/impostazioni": "impostazioni.html"}.get(percorso, percorso.lstrip("/"))
        f = (WEB / nome).resolve()
        if WEB.resolve() not in f.parents or not f.is_file():
            return self.manda(404, "non trovato", "text/plain")
        self.manda(200, f.read_bytes(), TIPI.get(f.suffix, "application/octet-stream"))

    def flusso(self):
        """Server-Sent Events: una lettura al secondo, finché il tablet resta collegato."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        c = self.server.campionatore
        ip = self.client_address[0]
        ultima_t = None
        try:
            while True:
                with c.cond:
                    c.cond.wait_for(lambda: c.ultima is not None and c.ultima.get("t") != ultima_t, timeout=15)
                    dati = c.ultima
                if dati is None or dati.get("t") == ultima_t:
                    self.wfile.write(b": ping\n\n")
                else:
                    ultima_t = dati.get("t")
                    self.wfile.write(b"data: " + json.dumps(dati, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                self.server.tablet[ip] = time.time()
        except (ConnectionError, OSError):
            pass

    # -- POST
    def do_POST(self):
        if not rete_locale(self.client_address[0]):
            return self.manda(403, {"errore": "solo rete locale"})
        u = urlparse(self.path)
        livello = self.livello(parse_qs(u.query))
        if not livello:
            return self.manda(401, {"errore": "token mancante o sbagliato"})
        try:
            dati = self.corpo_json()
            if u.path == "/api/azione":
                return self.manda(200, self.azione(dati))
            if u.path == "/api/config":
                return self.manda(200, self.aggiorna_config(dati, livello))
            if livello == "pc" and u.path == "/api/pc/abbina":
                Abbinamento.apri()
                return self.manda(200, {"abbinamento": Abbinamento.secondi_rimasti()})
            if livello == "pc" and u.path == "/api/pc/sfoglia":
                return self.manda(200, {"percorso": sfoglia()})
            if livello == "pc" and u.path == "/api/pc/prova":
                tasti = leggi_tasti(dati.get("tasti", ""))
                threading.Timer(3, premi, args=(tasti,)).start()
                return self.manda(200, {"ok": True})
        except ValueError as e:
            return self.manda(400, {"errore": str(e)})
        except Exception as e:
            log("errore azione:", repr(e))
            return self.manda(500, {"errore": str(e)})
        return self.manda(404, {"errore": "non trovato"})

    def azione(self, d):
        cfg = self.server.cfg
        tipo = d.get("tipo")
        if tipo == "app":
            voce = next((a for a in cfg["app"] if a["id"] == d.get("id")), None)
            if not voce:
                raise ValueError("app non configurata")
            os.startfile(voce["target"])
            log("apro", voce["nome"])
            return {"ok": True}
        if tipo == "monitor_app":
            voce = next((a for a in cfg.get("monitor_app", []) if a["id"] == d.get("id")), None)
            if not voce:
                raise ValueError("app del secondo monitor non configurata")
            threading.Thread(target=apri_sul_monitor, args=(voce,), daemon=True).start()
            return {"ok": True}
        if tipo == "scorciatoia":
            voce = next((s for s in cfg["scorciatoie"] if s["id"] == d.get("id")), None)
            if not voce:
                raise ValueError("scorciatoia non configurata")
            premi(leggi_tasti(voce["tasti"]))
            return {"ok": True}
        if tipo == "media":
            tasto = d.get("tasto")
            if tasto not in ("volume_up", "volume_down", "volume_mute", "media_play_pause", "media_next", "media_prev"):
                raise ValueError("tasto multimediale sconosciuto")
            premi([VK[tasto]])
            return {"ok": True}
        raise ValueError("azione sconosciuta")

    def aggiorna_config(self, d, livello):
        cfg = self.server.cfg
        for chiave, prefisso, massimo in (("app", "a", 18), ("monitor_app", "m", 10)):
            if chiave not in d:
                continue
            noti = {a["target"]: a for a in catalogo()}
            noti.update({a["target"]: a for a in cfg.get(chiave, [])})
            nuove = []
            for i, a in enumerate(d[chiave][:massimo]):
                target = a.get("target")
                if target not in noti:
                    if livello != "pc":
                        raise ValueError("dal tablet si possono aggiungere solo app del menu Start o giochi Steam: "
                                         f"{a.get('nome')}")
                    target = target_valido_pc(target)
                nome = str(a.get("nome") or (noti.get(target) or {}).get("nome") or Path(target).stem)[:40]
                nuove.append({"id": f"{prefisso}{i}", "nome": nome, "target": target})
            cfg[chiave] = nuove
        if "scorciatoie" in d:
            nuove = []
            for i, s in enumerate(d["scorciatoie"][:18]):
                leggi_tasti(s.get("tasti", ""))
                nuove.append({"id": f"s{i}", "nome": str(s.get("nome") or s["tasti"])[:30],
                              "tasti": str(s["tasti"]).lower(), "icona": str(s.get("icona") or "⌨️")[:4]})
            cfg["scorciatoie"] = nuove
        salva_config(cfg)
        self.server.campionatore.config_cambiata()
        log("config aggiornata da", livello)
        return {"app": cfg["app"], "scorciatoie": cfg["scorciatoie"], "monitor_app": cfg.get("monitor_app", []),
                "versione": self.server.campionatore.versione_config}


# ---------------------------------------------------------------- collegamento USB

def adb_reverse():
    """Se il tablet è collegato via USB con il debug attivo, gli rende raggiungibile il PC su
    127.0.0.1: niente firewall e niente rete. Se non c'è, il tablet usa la rete locale."""
    adb = shutil.which("adb") or next((p for p in (r"C:\platform-tools\adb.exe",
                                                   os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"))
                                       if os.path.exists(p)), None)
    if not adb:
        return
    while True:
        try:
            subprocess.run([adb, "reverse", f"tcp:{PORTA}", f"tcp:{PORTA}"], capture_output=True, timeout=10,
                           creationflags=SENZA_FINESTRA)
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(15)


# ---------------------------------------------------------------- abbinamento del tablet

class Abbinamento:
    """Il tablet trova il PC da solo. Sulla porta UDP PORTA+1 risponde a "PANNELLO-PC?" (broadcast in rete locale)
    con porta e nome del PC: così un tablet già abbinato ritrova il PC anche se l'IP cambia. La chiave del pannello
    viaggia solo mentre l'abbinamento è aperto: i primi 10 minuti finché nessun tablet si è mai collegato, oppure
    10 minuti dal pulsante "Abbina un tablet" nelle impostazioni. Il primo tablet che entra con la chiave lo chiude."""
    DURATA = 600
    _fino = 0.0

    @classmethod
    def apri(cls):
        cls._fino = time.time() + cls.DURATA
        log("abbinamento aperto per", cls.DURATA, "s")

    @classmethod
    def chiudi(cls):
        cls._fino = 0.0

    @classmethod
    def aperto(cls):
        return time.time() < cls._fino

    @classmethod
    def secondi_rimasti(cls):
        return max(0, round(cls._fino - time.time()))

    @classmethod
    def risposta(cls, cfg, con_chiave):
        r = {"pannello": 1, "porta": PORTA, "pc": os.environ.get("COMPUTERNAME", ""),
             "app_monitor": cfg.get("app_monitor", "Virtual Display"), "app_monitor_uuid": cfg.get("app_monitor_uuid", "")}
        if con_chiave:
            r["token"] = cfg["token"]
        return r

    @classmethod
    def ascolta(cls, cfg):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", PORTA + 1))
        except OSError as e:
            log("abbinamento UDP non disponibile:", e)
            return
        while True:
            try:
                dati, mittente = s.recvfrom(256)
                if dati.strip() != b"PANNELLO-PC?" or not rete_locale(mittente[0]):
                    continue
                aperto = cls.aperto()
                s.sendto(json.dumps(cls.risposta(cfg, con_chiave=aperto)).encode("utf-8"), mittente)
                if aperto:
                    log("chiave del pannello inviata al tablet", mittente[0])
            except OSError:
                time.sleep(1)


def crea_collegamenti(cfg):
    """Collegamenti "Pannello PC - Impostazioni" sul Desktop e nel menu Start (li usa installa.ps1)."""
    url = f"http://127.0.0.1:{int(cfg.get('porta', PORTA))}/impostazioni#{cfg['token_pc']}"
    testo = f"[InternetShortcut]\r\nURL={url}\r\n"
    cartelle = [Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"))]
    try:
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY (anche OneDrive)
        cartelle.append(Path(buf.value))
    except (AttributeError, OSError):
        cartelle.append(Path.home() / "Desktop")
    for c in cartelle:
        if c.is_dir():
            (c / "Pannello PC - Impostazioni.url").write_text(testo, encoding="utf-8")
            print("creato", c / "Pannello PC - Impostazioni.url")


class ServerUnico(ThreadingHTTPServer):
    allow_reuse_address = False  # su Windows SO_REUSEADDR farebbe ascoltare due server sulla stessa porta


def main():
    if "--collegamenti" in sys.argv:  # solo crea config.json (se manca) e i collegamenti, poi esce
        crea_collegamenti(carica_config())
        return
    # una sola copia del server (attività pianificata all'accesso + eventuali avvii a mano)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wt.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    main.mutex = k32.CreateMutexW(None, False, "Local\\PannelloPCServer")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("server già in esecuzione: esco")
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass
    cfg = carica_config()
    global PORTA
    PORTA = int(cfg.get("porta", PORTA))
    camp = Campionatore()
    camp.start()
    threading.Thread(target=adb_reverse, daemon=True).start()
    if not cfg.get("abbinato") or "--abbina" in sys.argv:
        Abbinamento.apri()
    threading.Thread(target=Abbinamento.ascolta, args=(cfg,), daemon=True).start()
    threading.Thread(target=ascolta_windows, args=(camp,), daemon=True).start()
    threading.Thread(target=lambda: estrai_icone([a["target"] for a in cfg["app"]] + [a["target"] for a in elenco_app()]),
                     daemon=True).start()
    srv = ServerUnico(("0.0.0.0", PORTA), Gestore)
    srv.daemon_threads = True
    srv.tablet = {}
    Dock(cfg).start()
    CustodeGioco(cfg).start()
    srv.cfg = cfg
    srv.campionatore = camp
    log("pannello in ascolto sulla porta", PORTA)
    if "--stampa" in sys.argv:
        print(f"http://127.0.0.1:{PORTA}/?t={cfg['token']}")
        print(f"http://127.0.0.1:{PORTA}/impostazioni#{cfg['token_pc']}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
