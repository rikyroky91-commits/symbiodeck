"""Mouse vero del PC (2026-11-26): i dispositivi virtuali non contano, gli avvisi non sono più di due al secondo, la
finestra di Raw Input si registra. Senza visore: python tests/test_mouse_vero.py"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import mouse_vero as mv  # noqa: E402


def test_virtuali():
    assert not mv.virtuale(r"\\?\HID#VID_046D&PID_C539&MI_01&Col01#8&2a1b&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91e6bf6}")
    assert mv.virtuale(r"\\?\HID#VMULTI&Col02#1&2d595ca7&0&0001#{378de44c}")
    assert mv.virtuale(r"\\?\ROOT#HIDCLASS#0000#{378de44c}")
    assert mv.virtuale("Sunshine Virtual Mouse")
    assert not mv.virtuale("")


def test_avvisatore():
    ora = [100.0]
    avvisi = []
    a = mv.Avvisatore(lambda: avvisi.append(ora[0]), ogni=0.5, orologio=lambda: ora[0])
    for _ in range(10):          # 10 movimenti in 0,1 s: un solo avviso
        a.mosso()
        ora[0] += 0.01
    assert len(avvisi) == 1
    ora[0] += 0.5
    a.mosso()
    assert len(avvisi) == 2


def test_finestra_si_registra():
    righe = []
    threading.Thread(target=mv.ascolta, args=(lambda: None, lambda *p: righe.append(" ".join(map(str, p)))),
                     daemon=True).start()
    for _ in range(50):
        if righe:
            break
        time.sleep(0.05)
    assert righe and righe[0] == "mouse vero: in ascolto", righe


if __name__ == "__main__":
    test_virtuali()
    test_avvisatore()
    test_finestra_si_registra()
    print("ok")
