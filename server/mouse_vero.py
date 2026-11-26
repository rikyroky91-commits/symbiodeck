"""Il mouse vero del PC (2026-11-26): quando lo muovi mentre nel visore lo schermo del PC è aperto, il visore smette di
usare le mani come puntatore (altrimenti mani e mouse si contendono il cursore); con le mani giunte tornano le mani.

Raw Input con RIDEV_INPUTSINK su una finestra invisibile: arriva anche senza essere in primo piano e non rallenta il
mouse (un hook a basso livello invece lo farebbe aspettare Python a ogni movimento). Il mouse che arriva dal visore
passa da Apollo con SendInput: Windows lo consegna senza dispositivo (hDevice = 0), quindi non conta. Per prudenza
non contano neanche i dispositivi virtuali con un nome riconoscibile (driver di streaming o di controller).
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

WM_INPUT = 0x00FF
RID_HEADER = 0x10000005
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE = 0

# nomi (minuscoli) di dispositivi che non sono un mouse sulla scrivania
VIRTUALI = ("vmulti", "virtual", "apollo", "sunshine", "vigem", "parsec", "moonlight", "root#", "vhf", "vusb")


def virtuale(nome):
    n = (nome or "").lower()
    return any(v in n for v in VIRTUALI)


class Avvisatore:
    """Decide quando avvisare: al primo movimento dopo una pausa, poi al massimo ogni [ogni] secondi."""

    def __init__(self, avvisa, ogni=0.5, orologio=time.monotonic):
        self.avvisa = avvisa
        self.ogni = ogni
        self.orologio = orologio
        self.ultimo_avviso = None

    def mosso(self):
        ora = self.orologio()
        if self.ultimo_avviso is None or ora - self.ultimo_avviso >= self.ogni:
            self.ultimo_avviso = ora
            self.avvisa()
            return True
        return False


class _WNDCLASSW(ctypes.Structure):
    pass


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_WNDCLASSW._fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                       ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                       ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                       ("lpszClassName", wt.LPCWSTR)]


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT), ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD), ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


def ascolta(avvisa, log=print):
    """Bloccante (da un thread): chiama [avvisa] quando si muove o si clicca un mouse fisico."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT]
    user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT)]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE), wt.UINT, wt.UINT]

    avvisatore = Avvisatore(avvisa)
    dispositivi = {}      # handle -> conta (True) o no (False)

    def nome(h):
        n = wt.UINT(0)
        user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, ctypes.byref(n))
        if not n.value:
            return ""
        buf = ctypes.create_unicode_buffer(n.value + 1)
        user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, ctypes.byref(n))
        return buf.value

    def procedura(hwnd, msg, wp, lp):
        if msg == WM_INPUT:
            try:
                h = _RAWINPUTHEADER()
                n = wt.UINT(ctypes.sizeof(h))
                if user32.GetRawInputData(wt.HANDLE(lp), RID_HEADER, ctypes.byref(h), ctypes.byref(n),
                                          ctypes.sizeof(_RAWINPUTHEADER)) != 0xFFFFFFFF \
                        and h.dwType == RIM_TYPEMOUSE and h.hDevice:
                    conta = dispositivi.get(h.hDevice)
                    if conta is None:
                        nm = nome(h.hDevice)
                        conta = dispositivi[h.hDevice] = not virtuale(nm)
                        log("mouse vero:", "conta" if conta else "ignorato (virtuale)", nm or "(senza nome)")
                    if conta:
                        avvisatore.mosso()
            except Exception as e:   # mai far cadere la finestra per un evento strano
                log("mouse vero:", repr(e))
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    proc = WNDPROC(procedura)
    ascolta._procedura = proc      # resta in vita finché gira il server
    classe = _WNDCLASSW(lpfnWndProc=proc, lpszClassName="PannelloPCMouseVero",
                        hInstance=kernel32.GetModuleHandleW(None))
    if not user32.RegisterClassW(ctypes.byref(classe)):
        log("mouse vero: RegisterClass fallita")
        return
    hwnd = user32.CreateWindowExW(0, "PannelloPCMouseVero", "Pannello PC mouse", 0, 0, 0, 0, 0, None, None,
                                  classe.hInstance, None)
    if not hwnd:
        log("mouse vero: CreateWindow fallita")
        return
    d = _RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)    # pagina desktop generico, mouse
    if not user32.RegisterRawInputDevices(ctypes.byref(d), 1, ctypes.sizeof(d)):
        log("mouse vero: RegisterRawInputDevices fallita", ctypes.get_last_error())
        return
    log("mouse vero: in ascolto")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.DispatchMessageW(ctypes.byref(msg))


def avvia(avvisa, log=print):
    t = threading.Thread(target=ascolta, args=(avvisa, log), daemon=True, name="mouse-vero")
    t.start()
    return t
