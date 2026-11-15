"""Dock del secondo monitor: barra in basso sul monitor virtuale del tablet con le app scelte nel companion.

La lancia il server (classe Dock in pannello.py) quando compare il monitor virtuale di Apollo e la chiude
quando sparisce. È una "appbar" di Windows: riserva il suo spazio, quindi le finestre massimizzate sul
monitor del tablet si fermano sopra la barra invece di finirci sotto.

    python dock.py <porta> <token_pc> <left,top,right,bottom del monitor>
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading
import tkinter as tk
import urllib.parse
import urllib.request

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # pixel reali, come il server
except OSError:
    pass

PORTA, TOKEN = sys.argv[1], sys.argv[2]
L, T, R, B = (int(x) for x in sys.argv[3].split(","))
BASE = f"http://127.0.0.1:{PORTA}"
TITOLO = "Dock Pannello PC"
SFONDO, BORDO, TESTO, TENUE, LAMPO = "#0d1219", "#22303f", "#e8eef6", "#7d8a9c", "#1d2a3a"
ALTEZZA = max(110, min(190, int((B - T) * 0.10)))


def api(percorso, corpo=None):
    req = urllib.request.Request(BASE + percorso, headers={"X-Token": TOKEN})
    if corpo is not None:
        req.data = json.dumps(corpo).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read()


# ---------------------------------------------------------------- appbar (spazio riservato sul monitor)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT), ("uEdge", wt.UINT),
                ("rc", RECT), ("lParam", wt.LPARAM)]


shell32 = ctypes.windll.shell32
shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS, ABE_BOTTOM = 0, 1, 2, 3, 3


def registra_appbar(hwnd):
    abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA), hWnd=hwnd, uCallbackMessage=0x0401, uEdge=ABE_BOTTOM)
    shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(abd))
    abd.rc = RECT(L, B - ALTEZZA, R, B)
    shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(abd))   # Windows sposta il rettangolo se c'è la taskbar
    abd.rc.top = abd.rc.bottom - ALTEZZA
    shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))
    return abd


def monitor_ancora_presente():
    class DD(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                    ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]
    i = 0
    while True:
        d = DD()
        d.cb = ctypes.sizeof(d)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            return False
        if d.StateFlags & 1 and "Virtual Display" in d.DeviceString:
            return True
        i += 1


# ---------------------------------------------------------------- interfaccia

class Dock:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(TITOLO)
        self.root.overrideredirect(True)
        self.root.configure(bg=SFONDO)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{R - L}x{ALTEZZA}+{L}+{B - ALTEZZA}")
        self.root.update_idletasks()
        self.hwnd = int(self.root.wm_frame(), 16)
        self.abd = registra_appbar(self.hwnd)
        rc = self.abd.rc
        self.root.geometry(f"{rc.right - rc.left}x{ALTEZZA}+{rc.left}+{rc.top}")
        tk.Frame(self.root, bg=BORDO, height=2).pack(fill="x", side="top")
        self.fila = tk.Frame(self.root, bg=SFONDO)
        self.fila.pack(expand=True)
        self.immagini = {}
        self.versione = None
        self.errori = 0
        self.root.protocol("WM_DELETE_WINDOW", self.esci)
        self.aggiorna()
        self.controlla()

    def icona(self, target, lato):
        if target in self.immagini:
            return self.immagini[target]
        img = None
        try:
            dati = api("/api/icona?" + urllib.parse.urlencode({"target": target}))
            if dati[:8] == b"\x89PNG\r\n\x1a\n":  # Tk legge PNG, non JPEG
                img = tk.PhotoImage(data=base64.b64encode(dati))
                fattore = max(1, round(img.width() / lato))
                if fattore > 1:
                    img = img.subsample(fattore)
        except OSError:
            pass
        self.immagini[target] = img
        return img

    def aggiorna(self):
        try:
            cfg = json.loads(api("/api/config"))
        except OSError:
            return
        self.versione = cfg.get("versione")
        for w in self.fila.winfo_children():
            w.destroy()
        lato = int(ALTEZZA * 0.42)
        carattere = ("Segoe UI", max(9, int(ALTEZZA * 0.085)))
        for voce in cfg.get("monitor_app", []):
            cella = tk.Frame(self.fila, bg=SFONDO, padx=int(ALTEZZA * 0.16), pady=int(ALTEZZA * 0.08), cursor="hand2")
            img = self.icona(voce["target"], lato)
            if img:
                figura = tk.Label(cella, image=img, bg=SFONDO)
            else:
                figura = tk.Label(cella, text=voce["nome"][:1].upper(), width=2, bg="#2b3d55", fg=TESTO,
                                  font=("Segoe UI", int(lato * 0.45), "bold"))
            figura.pack()
            nome = tk.Label(cella, text=voce["nome"][:14], bg=SFONDO, fg=TENUE, font=carattere)
            nome.pack(pady=(int(ALTEZZA * 0.04), 0))
            for w in (cella, figura, nome):
                w.bind("<Button-1>", lambda _e, v=voce, c=cella: self.apri(v, c))
            cella.pack(side="left")

    def apri(self, voce, cella):
        parti = [cella] + list(cella.winfo_children())
        for w in parti:
            w.configure(bg=LAMPO)
        self.root.after(400, lambda: [w.configure(bg=SFONDO) for w in parti if w.winfo_exists()])
        # il tocco l'ha ricevuto la dock: solo lei può lasciare il primo piano all'app che sta per aprirsi
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(0xFFFFFFFF))  # ASFW_ANY
        threading.Thread(target=lambda: api("/api/azione", {"tipo": "monitor_app", "id": voce["id"]}), daemon=True).start()

    def controlla(self):
        """Ogni 2 s: il monitor del tablet c'è ancora? la config è cambiata? resta sopra le altre finestre."""
        if not monitor_ancora_presente():
            return self.esci()
        try:
            v = json.loads(api("/api/config")).get("versione")
            self.errori = 0
            if v != self.versione:
                self.aggiorna()
        except OSError:
            self.errori += 1
            if self.errori > 5:  # server spento
                return self.esci()
        self.root.attributes("-topmost", True)
        self.root.after(2000, self.controlla)

    def esci(self):
        shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self.abd))
        self.root.destroy()


if __name__ == "__main__":
    Dock().root.mainloop()
