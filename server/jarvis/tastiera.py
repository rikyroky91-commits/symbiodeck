"""Scrivere nella finestra in primo piano del PC (dettatura di Jarvis): caratteri Unicode con SendInput, come lo
strumento scrivi del server MCP schermo. Solo libreria standard; su sistemi diversi da Windows non fa niente."""
import ctypes
import sys
import time

if sys.platform == "win32":
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32

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
else:   # prove su Linux: si registra quello che si sarebbe premuto
    PREMUTI = []

    def _invia(vk=0, scan=0, flag=0):
        PREMUTI.append((vk, scan, flag))


def scrivi(testo):
    for c in testo:
        if c == "\n":
            premi(0x0D)
            continue
        b = c.encode("utf-16-le")
        for i in range(0, len(b), 2):
            u = int.from_bytes(b[i:i + 2], "little")
            _invia(scan=u, flag=0x0004)            # KEYEVENTF_UNICODE
            _invia(scan=u, flag=0x0004 | 0x0002)   # + KEYUP
        time.sleep(0.004)


def premi(*codici):
    """Preme insieme i tasti virtuali (es. premi(0x11, 0x08) = Ctrl+Backspace) e li rilascia."""
    for vk in codici:
        _invia(vk, 0, 0)
        time.sleep(0.015)
    for vk in reversed(codici):
        _invia(vk, 0, 0x0002)


INVIO, BACKSPACE, CTRL = 0x0D, 0x08, 0x11
