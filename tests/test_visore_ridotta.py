"""Finestra ridotta a icona chiesta per il visore (2026-11-26): si riapre da sola senza prendere il fuoco, e la
diretta manda fotogrammi invece di restare ferma su "collegamento". user32 finto, nessuna finestra vera."""
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import visore  # noqa: E402


class _User32Finto:
    def __init__(self):
        self.ridotta = True
        self.chiamate = []

    def IsWindow(self, h):
        return True

    def IsIconic(self, h):
        return self.ridotta

    def ShowWindow(self, h, comando):
        self.chiamate.append(comando)
        self.ridotta = False


def test_ripristina_senza_rubare_il_fuoco():
    u = _User32Finto()
    with mock.patch.object(visore, "user32", u):
        assert visore.ripristina_ridotta(123) is True
        assert u.chiamate == [4]                 # SW_SHOWNOACTIVATE, non SW_RESTORE (9) che la porterebbe davanti
        assert visore.ripristina_ridotta(123) is False
        assert u.chiamate == [4]


def test_diretta_di_una_finestra_ridotta_manda_fotogrammi():
    u = _User32Finto()
    finto_mss = types.SimpleNamespace(mss=lambda: mock.MagicMock(__enter__=lambda s: types.SimpleNamespace(
        monitors=[{}, {"left": 0, "top": 0, "width": 100, "height": 100}]), __exit__=lambda *a: False))

    class _Img:
        def save(self, out, *a, **k):
            out.write(b"\xff\xd8jpeg")

    with mock.patch.object(visore, "user32", u), mock.patch.dict(sys.modules, {"mss": finto_mss}), \
            mock.patch.object(visore, "tieni_acceso", lambda *a, **k: None), \
            mock.patch.object(visore, "_rettangolo", lambda h: (0, 0, 50, 40)), \
            mock.patch.object(visore, "_cattura_zona", lambda *a: _Img()):
        primo = next(visore.diretta(hwnd=123, fps=30))
    assert primo.startswith(b"\xff\xd8") and u.chiamate == [4]
